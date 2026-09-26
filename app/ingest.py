"""Corpus ingestion and chunking.

The corpus is a directory of Markdown files, each carrying a YAML front-matter
block with the URL it was taken from, the date it was retrieved, and its
verification status. Provenance travels with every chunk, so no answer can ever
be produced without knowing where it came from.

Chunking is paragraph-based with a character cap rather than a fixed token
window. At a corpus of this size a fixed window would be simpler, but it would
routinely cut a contact line away from the office it belongs to, which is
exactly the kind of fact this product exists to deliver.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CORPUS_DIR, ROUTING_TABLE_PATH

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_CHARS = 620
_MIN_CHARS = 110

#: Content the ingestion layer must never treat as an answerable fact. These
#: are published as prose in the corpus precisely so the system can tell a user
#: what is missing rather than filling the gap.
_WHITELIST_MARKER = "not published"


def _parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse the small YAML subset used by the corpus files.

    A full YAML dependency is deliberately avoided: the front matter is a flat
    mapping of scalars and string lists, and adding PyYAML to read it would be a
    dependency bought for nothing. Both YAML spellings of a list are supported —
    inline ``[a, b]`` and block ``- a`` on following lines — because a corpus
    that silently loses its route provenance is worse than no corpus at all.
    """
    match = _FRONT_MATTER.match(raw)
    if not match:
        return {}, raw

    block, body = match.group(1), raw[match.end():]
    meta: dict[str, Any] = {}
    pending_list_key: str | None = None

    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        # Block sequence item belonging to the previous key.
        if line.lstrip().startswith("- ") and pending_list_key:
            item = line.lstrip()[2:].strip().strip("\"'")
            if item:
                meta.setdefault(pending_list_key, []).append(item)
            continue

        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
            pending_list_key = None
        elif not value:
            # A key with no inline value: either a block sequence follows, or it
            # is an empty scalar. Start a list and let the loop fill it.
            meta[key] = []
            pending_list_key = key
        else:
            meta[key] = value.strip("\"'")
            pending_list_key = None

    return meta, body


def _strip_boilerplate(text: str) -> str:
    """Remove headings and markdown emphasis, keeping the prose intact.

    Headings are dropped because they are navigational labels that match
    queries well but answer nothing. Emphasis markers are stripped rather than
    their contents so that a cited span reads as a sentence rather than as
    formatting.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("#"):
            continue
        stripped = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
        stripped = re.sub(r"`(.+?)`", r"\1", stripped)
        stripped = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", stripped)
        lines.append(stripped)
    return "\n".join(lines)


def _split_units(text: str) -> list[str]:
    """Split cleaned body text into paragraph units, then greedily pack them."""
    units: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        # A markdown table is one semantic unit; do not split it mid-row.
        if block.lstrip().startswith("|"):
            units.append(block)
            continue
        # Keep list items that belong to the same list together.
        if re.match(r"^\s*[-*]\s+", block):
            units.append(block)
            continue
        units.append(block)

    packed: list[str] = []
    buffer = ""
    for unit in units:
        if not buffer:
            buffer = unit
        elif len(buffer) + len(unit) + 2 <= _MAX_CHARS:
            buffer = f"{buffer}\n\n{unit}"
        else:
            packed.append(buffer)
            buffer = unit
    if buffer:
        packed.append(buffer)
    return packed


@dataclass
class Chunk:
    """One retrievable unit of the corpus, with its provenance attached."""

    chunk_id: str
    text: str
    source_url: str
    source_title: str
    retrieved_at: str
    verification: str
    content_type: str
    routes: list[str] = field(default_factory=list)
    ordinal: int = 0

    @property
    def is_negation_notice(self) -> bool:
        """True when this chunk exists to record that a fact is not published.

        The system uses these to tell a user *what is missing* rather than
        silently failing to answer. They are the most safety-critical chunks in
        the corpus.
        """
        lowered = self.text.lower()
        return _WHITELIST_MARKER in lowered or "must not" in lowered

    @property
    def carries_contact_detail(self) -> bool:
        """True when the chunk contains a way to make contact.

        Used to bias the composer towards chunks that can actually resolve a
        user's problem rather than chunks that merely mention the topic.
        """
        return bool(
            re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", self.text)
            or re.search(r"\+?\d[\d\s()-]{7,}", self.text)
            or "http" in self.text.lower()
        )


@dataclass
class Document:
    source_url: str
    source_title: str
    retrieved_at: str
    verification: str
    content_type: str
    routes: list[str]
    body: str
    path: str
    sha256: str

    @property
    def fingerprint(self) -> str:
        return self.sha256[:12]


@dataclass
class Corpus:
    documents: list[Document]
    chunks: list[Chunk]

    @property
    def by_url(self) -> dict[str, Document]:
        return {d.source_url: d for d in self.documents}


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> Corpus:
    """Load and chunk every Markdown file in the corpus directory."""
    documents: list[Document] = []
    chunks: list[Chunk] = []

    for path in sorted(Path(corpus_dir).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        cleaned = _strip_boilerplate(body)

        source_url = meta.get("source_url", "")
        if not source_url:
            raise ValueError(f"{path.name} has no source_url in front matter")

        digest = hashlib.sha256(
            (source_url + "|" + cleaned).encode("utf-8")
        ).hexdigest()

        document = Document(
            source_url=source_url,
            source_title=meta.get("source_title", ""),
            retrieved_at=meta.get("retrieved_at", ""),
            verification=meta.get("verification", "published"),
            content_type=meta.get("content_type", ""),
            routes=meta.get("routes", []) or [],
            body=cleaned,
            path=str(path),
            sha256=digest,
        )
        documents.append(document)

        for ordinal, unit in enumerate(_split_units(cleaned)):
            if len(unit) < _MIN_CHARS and ordinal > 0:
                continue
            cid = hashlib.sha1(
                f"{source_url}#{ordinal}#{unit}".encode("utf-8")
            ).hexdigest()[:16]
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    text=unit.strip(),
                    source_url=source_url,
                    source_title=document.source_title,
                    retrieved_at=document.retrieved_at,
                    verification=document.verification,
                    content_type=document.content_type,
                    routes=list(document.routes),
                    ordinal=ordinal,
                )
            )

    if not chunks:
        raise RuntimeError(f"No chunks produced from {corpus_dir}")

    return Corpus(documents=documents, chunks=chunks)


def load_routing_table(path: Path = ROUTING_TABLE_PATH) -> dict[str, Any]:
    """Load the reconciled routing table."""
    import json

    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
