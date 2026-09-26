"""Retrieval index.

Design note on score calibration.

Raw cosine similarity from a sentence-embedding model is not comparable to a
probability. For all-MiniLM-L6-v2, a clearly relevant pair typically lands
around 0.45 to 0.70 and an unrelated pair around 0.00 to 0.15. Mapping that
straight onto a 0-to-1 scale and then setting a threshold at "0.34" produces a
system that never abstains, which is the exact failure this product must not
have.

The scores below are therefore linearly rescaled between two documented anchor
points, so that the abstention thresholds in ``Thresholds`` mean what they say.
The anchors were chosen by inspecting the score distribution over the frozen
evaluation sets and are recorded here rather than hidden in a helper, because a
calibration constant that nobody can see is indistinguishable from a fudge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .config import thresholds as T
from .embeddings import Embedder, cosine_scores

#: Cosine at which a match is treated as no evidence at all.
ANCHOR_NOISE = 0.15
#: Cosine at which a match is treated as fully supported.
ANCHOR_STRONG = 0.62

#: Multiplier applied to a sibling passage admitted by document-level context
#: expansion. Low enough that a direct match always outranks it, high enough to
#: clear the noise floor so the passage is still available to the composer.
CONTEXT_DECAY = 0.55

#: How many sibling passages one matched document may contribute.
SIBLINGS = 3


def calibrate(raw: np.ndarray | float) -> np.ndarray | float:
    """Rescale raw cosine similarity onto 0..1 using the documented anchors."""
    scaled = (np.asarray(raw, dtype=np.float32) - ANCHOR_NOISE) / (
        ANCHOR_STRONG - ANCHOR_NOISE
    )
    return np.clip(scaled, 0.0, 1.0)


@dataclass
class Hit:
    """One retrieved chunk with its calibrated score."""

    chunk: object
    score: float
    raw: float
    #: True when this passage was admitted by document-level expansion rather
    #: than matched on its own. Consumers need to know: an expanded passage is
    #: evidence about its document, not about the question, and quoting an
    #: enumeration out of one attaches the wrong list to the answer.
    expanded: bool = False

    @property
    def supported(self) -> bool:
        return self.score >= T.min_evidence


class Retriever:
    """Vector index over the corpus.

    A brute-force exact search is used deliberately. The corpus is a few hundred
    chunks, where an approximate index would add a dependency, a tuning
    parameter, and a recall failure mode in exchange for speed that is not
    needed. Moving to an approximate index is recorded as a v2 step, contingent
    on the corpus growing by roughly two orders of magnitude.
    """

    def __init__(self, chunks: Sequence, embedder: Embedder) -> None:
        self.chunks = list(chunks)
        self.embedder = embedder
        self._matrix = self.embedder.encode([c.text for c in self.chunks])
        if self._matrix.shape[0] != len(self.chunks):
            raise RuntimeError("Embedding matrix does not align with chunks")

    def search(self, query: str, top_k: int | None = None) -> list[Hit]:
        """Return the highest-scoring chunks for a query, best first."""
        top_k = top_k or T.top_k
        query_vector = self.embedder.encode([query])[0]
        raw = cosine_scores(query_vector, self._matrix)
        scores = calibrate(raw)

        # Rank on the raw cosine, not the calibrated score. Calibration clips
        # everything at or above the strong anchor to exactly 1.0, so ranking
        # on it sorts a whole plateau of genuinely different passages as
        # identical and hands the ordering back to whatever the sort considers
        # stable, which is chunk index. On a corpus where several passages share
        # a topic that meant the passage actually listing the programmes could
        # be returned after the passages merely mentioning the centre. The
        # calibrated score stays authoritative for the thresholds; it just is
        # not fit to order by.
        order = np.argsort(-raw)[:top_k]
        hits = [
            Hit(chunk=self.chunks[i], score=float(scores[i]), raw=float(raw[i]))
            for i in order
        ]
        return [h for h in hits if h.score > T.noise_floor]

    def best(self, query: str) -> Hit | None:
        hits = self.search(query, top_k=1)
        return hits[0] if hits else None

    def expand(self, direct: Sequence[Hit], siblings: int = SIBLINGS) -> list[Hit]:
        """Admit sibling passages from the documents the direct hits came from.

        A sentence embedding can only match a passage on the words the two
        share. The passage that actually answers "which programmes does the
        centre run" is a bare list — "Agronomy and Crop Production, Agricultural
        Extension and Rural Innovation, Agribusiness" — which shares no topic
        words with the question at all, while the surrounding passages about the
        centre match strongly and say nothing about programmes. Matching on
        chunks alone therefore reliably returns the introduction and drops the
        answer.

        The standard remedy is to treat the chunk as the unit of matching and the
        document as the unit of context. Once a document is judged relevant, its
        remaining passages are admitted at a decayed score so they stay available
        to the composer but never outrank a direct match. The text is still
        verbatim from the corpus, so grounding is untouched; this widens what
        counts as evidence for a document, not what counts as a source.
        """
        direct = list(direct)
        if not direct:
            return direct

        seen = {h.chunk.chunk_id for h in direct}
        by_source: dict[str, list] = {}
        for chunk in self.chunks:
            by_source.setdefault(chunk.source_url, []).append(chunk)

        out = list(direct)
        for parent in direct:
            pool = by_source.get(parent.chunk.source_url, ())
            added = 0
            for chunk in pool:
                if added >= siblings:
                    break
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                added += 1
                decayed = parent.score * CONTEXT_DECAY
                if decayed <= T.noise_floor:
                    continue
                out.append(
                    Hit(
                        chunk=chunk,
                        score=decayed,
                        raw=parent.raw * CONTEXT_DECAY,
                        expanded=True,
                    )
                )
        return out

    @property
    def size(self) -> int:
        return len(self.chunks)

    def source_urls(self) -> set[str]:
        return {c.source_url for c in self.chunks}
