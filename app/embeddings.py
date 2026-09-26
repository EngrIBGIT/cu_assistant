"""Embedders.

Two implementations behind one interface:

``MiniLMEmbedder``
    A real sentence-embedding model (all-MiniLM-L6-v2, 384 dimensions). This is
    the production path. It is what makes the assistant handle "I am a working
    professional, I want to deepen my qualification" as a postgraduate enquiry
    without any shared keywords with "How do I apply for a postgraduate
    programme?"

``TfidfEmbedder``
    A dependency-free character n-gram vectoriser in pure NumPy. It is the
    fallback used by CI, by first-run on a machine with no model cache, and
    whenever the neural model cannot be loaded. It is materially worse at
    paraphrase, and the evaluation harness measures exactly how much worse, so
    the cost of degradation is measured rather than assumed.

The choice is made at construction time, not per query, so that a single request
cannot silently change representation and produce an unreproducible answer.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol, Sequence

import numpy as np

from .config import EMBEDDER, EMBEDDING_MODEL, Thresholds

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenisation.

    Deliberately not stemmed and not stop-worded. Stop-word removal loses
    "not" and "no", which carry the difference between a published fact and an
    unpublished one, and that difference is the whole safety story here.
    """
    return _TOKEN.findall(text.lower())


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


class TfidfEmbedder:
    """Hashed word and character n-gram TF-IDF vectors. No learned parameters.

    Character trigrams are included because they give partial credit for
    morphological variants ("registering" / "registration") without a
    stemmer, which keeps the dependency list at zero.
    """

    name = "tfidf-lexical"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self._idf: dict[str, float] = {}
        self._fitted = False

    def _features(self, text: str) -> Counter:
        tokens = tokenize(text)
        features: Counter = Counter()
        features.update(f"w:{t}" for t in tokens)

        joined = " ".join(tokens)
        features.update(f"c:{joined[i:i + 3]}" for i in range(max(0, len(joined) - 2)))
        return features

    def fit(self, corpus: Sequence[str]) -> "TfidfEmbedder":
        document_frequency: Counter = Counter()
        for text in corpus:
            document_frequency.update(set(self._features(text).keys()))

        total = max(1, len(corpus))
        self._idf = {
            feature: math.log((1.0 + total) / (1.0 + df)) + 1.0
            for feature, df in document_frequency.items()
        }
        self._fitted = True
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not self._fitted:
            self.fit(texts)

        rows = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature, count in self._features(text).items():
                weight = (1.0 + math.log(count)) * self._idf.get(feature, 1.0)
                # Signed hashing keeps collisions unbiased rather than always
                # adding mass to a bucket.
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest, "big") % self.dim
                sign = 1.0 if digest[0] % 2 == 0 else -1.0
                rows[row, index] += sign * weight
        return _l2_normalise(rows)


class MiniLMEmbedder:
    """all-MiniLM-L6-v2 sentence embeddings via sentence-transformers."""

    name = "minilm-l6-v2"

    def __init__(self, model_name: str = EMBEDDING_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model_name)
        # The accessor was renamed in sentence-transformers 3.x; prefer the new
        # name and fall back to the deprecated alias so the project runs on a
        # cohort member's machine as well as a pinned one.
        for accessor in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            if hasattr(self._model, accessor):
                self.dim = int(getattr(self._model, accessor)())
                break
        else:
            self.dim = int(self._model.encode(["dimension probe"]).shape[1])

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(kind: str = EMBEDDER) -> tuple[Embedder, str | None]:
    """Return an embedder and a note explaining any fallback that occurred.

    The note is surfaced in the API's health endpoint and stamped into
    evaluation results, so a run that silently degraded cannot be mistaken for
    a run that did not.
    """
    if kind == "lexical":
        return TfidfEmbedder(), None

    try:
        return MiniLMEmbedder(), None
    except Exception as exc:  # noqa: BLE001 - any failure must degrade, not crash
        if kind == "neural":
            raise
        return (
            TfidfEmbedder(),
            f"neural embedder unavailable ({type(exc).__name__}: {exc}); "
            "fell back to the lexical embedder",
        )


def cosine_scores(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one query vector against a matrix of unit vectors."""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    return matrix @ query


__all__ = [
    "Embedder",
    "TfidfEmbedder",
    "MiniLMEmbedder",
    "build_embedder",
    "cosine_scores",
    "tokenize",
    "Thresholds",
]
