#!/usr/bin/env python
"""Build and cache the retrieval index, and print a corpus report.

Useful as a warm-up in deployment and as a way to inspect what the assistant can
actually see. A corpus report is part of the evidence pack: an assessor should be
able to confirm which pages the system is grounded in without reading code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EMBEDDER, VAR_DIR  # noqa: E402
from app.ingest import load_corpus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the retrieval index.")
    parser.add_argument("--embedder", default=EMBEDDER, choices=["auto", "neural", "lexical"])
    parser.add_argument("--report", action="store_true", help="print a corpus report")
    args = parser.parse_args()

    from app.embeddings import build_embedder  # noqa: PLC0415

    started = time.perf_counter()
    embedder, note = build_embedder(args.embedder)
    embed_ms = int((time.perf_counter() - started) * 1000)

    corpus = load_corpus()
    from app.retriever import Retriever  # noqa: PLC0415

    index_started = time.perf_counter()
    retriever = Retriever(corpus.chunks, embedder)
    index_ms = int((time.perf_counter() - index_started) * 1000)

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "embedder": embedder.name,
        "embedder_note": note,
        "dim": embedder.dim,
        "documents": len(corpus.documents),
        "chunks": retriever.size,
        "embed_ms": embed_ms,
        "index_ms": index_ms,
        "sources": sorted(retriever.source_urls()),
    }
    (VAR_DIR / "index_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"embedder   : {embedder.name} ({embedder.dim} dimensions)")
    if note:
        print(f"             {note}")
    print(f"documents  : {len(corpus.documents)}")
    print(f"chunks     : {retriever.size}")
    print(f"embed      : {embed_ms} ms")
    print(f"index      : {index_ms} ms")
    print(f"manifest   : {VAR_DIR / 'index_manifest.json'}")

    if args.report:
        print("\nCORPUS REPORT")
        print("=" * 78)
        print(f"{'chunks':>7}  {'verif':<28} {'type':<34} source")
        print("-" * 78)
        for document in sorted(corpus.documents, key=lambda d: d.source_url):
            count = sum(1 for c in corpus.chunks if c.source_url == document.source_url)
            print(f"{count:>7}  {document.verification:<28} "
                  f"{document.content_type:<34} {document.source_url}")

        print("\nROUTING COVERAGE")
        print("=" * 78)
        with open(Path(__file__).resolve().parent.parent / "data" / "routing_table.json",
                  encoding="utf-8") as handle:
            table = json.load(handle)

        covered = {r for d in corpus.documents for r in d.routes}
        for route in table["routes"]:
            mark = "ok " if route["route_id"] in covered else "GAP"
            print(f"  {mark} {route['route_id']:<24} {route['label']}")

        print("\n  coverage : "
              f"{len(covered & {r['route_id'] for r in table['routes']})}"
              f"/{len(table['routes'])} routes referenced by at least one chunk")

        print("\nNEGATION NOTICES (chunks that record what is NOT published)")
        print("=" * 78)
        notices = [c for c in corpus.chunks if c.is_negation_notice]
        for chunk in notices:
            print(f"  - [{chunk.source_url}]")
            print(f"    {chunk.text.strip()[:150].replace(chr(10), ' ')}…")
        print(f"\n  {len(notices)} negation notices. These are what the assistant "
              f"uses to tell a user what is missing\n  rather than failing silently.")

        print("\nCONTENT TYPES")
        print("=" * 78)
        for content_type, count in sorted(Counter(d.content_type for d in corpus.documents).items()):
            print(f"  {count:>3}  {content_type}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
