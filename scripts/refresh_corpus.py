#!/usr/bin/env python
"""Compare a proposed corpus snapshot against the committed corpus.

The refresh pipeline's automation stops short of the write. This script reads
``data/corpus`` and a proposed snapshot, writes a delta report and an approval
queue into ``var/``, and exits. It never touches ``data/corpus``. A human
resolves the queue by editing the corpus and committing, which is what keeps a
stale or wrong refresh from reaching users without anyone noticing.

The snapshot is supplied rather than fetched: curation is manual, so the
"snapshot" is a directory of pages in the same front-matter format as the
corpus, and this compares them.

    python scripts/refresh_corpus.py --snapshot path/to/proposed-pages
    python scripts/refresh_corpus.py --snapshot snapshots/2026-12-01 --max-age-days 60
    python scripts/refresh_corpus.py --snapshot proposed --now 2026-12-01T00:00:00

Exit codes: 0 the report was written; 2 the snapshot or corpus could not be
read. A non-zero exit is never a statement about whether changes are pending -
pending changes are the normal case, and the report, not the exit code, is the
output.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CORPUS_DIR, VAR_DIR  # noqa: E402
from app.ingest import load_corpus  # noqa: E402
from app.refresh import (  # noqa: E402
    DEFAULT_MAX_AGE_DAYS,
    approval_queue,
    build_report,
    render_text,
    report_to_dict,
)


def _parse_now(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff a proposed corpus snapshot against the committed corpus.",
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="directory of proposed pages, front matter identical to data/corpus",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=VAR_DIR / "refresh",
        help="directory for the delta report and approval queue",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="report committed pages older than this as stale",
    )
    parser.add_argument(
        "--now",
        default="",
        help="ISO timestamp for a reproducible run; defaults to the current UTC time",
    )
    args = parser.parse_args(argv)

    if not args.snapshot.is_dir():
        print(f"error: snapshot directory not found: {args.snapshot}", file=sys.stderr)
        return 2

    try:
        current = load_corpus(CORPUS_DIR)
        snapshot = load_corpus(args.snapshot)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = build_report(
        current,
        snapshot,
        now=_parse_now(args.now),
        max_age_days=args.max_age_days,
    )

    stamp = report.generated_at.replace(":", "").replace("-", "")
    out_dir = args.out / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "delta.txt").write_text(render_text(report) + "\n", encoding="utf-8")
    (out_dir / "delta.json").write_text(
        json.dumps(report_to_dict(report), indent=2), encoding="utf-8"
    )
    (out_dir / "approval_queue.json").write_text(
        json.dumps(approval_queue(report), indent=2), encoding="utf-8"
    )

    print(render_text(report))
    print()
    print(f"  report     : {out_dir / 'delta.txt'}")
    print(f"  data       : {out_dir / 'delta.json'}")
    print(f"  queue      : {out_dir / 'approval_queue.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
