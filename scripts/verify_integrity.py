#!/usr/bin/env python
"""Verify that the evaluation sets predate the implementation.

This is the integrity gate described in the project plan as risk R1, and it is
the check that separates a measurement from a demonstration. If the gold set can
be shown to have been written after the system it evaluates, no result from
that system is worth anything, regardless of how good the number looks.

It is run in CI so that the guarantee cannot lapse quietly.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ["eval/gold_set.json", "eval/paraphrase_set.json", "eval/out_of_scope_set.json"]

#: Paths whose first appearance marks the start of implementation.
IMPL_GLOBS = ["app/**", "scripts/run_eval.py", "scripts/build_index.py", "data/**"]


def _log(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _first_commit_for(paths: list[str]) -> tuple[str | None, str | None]:
    """Earliest commit timestamp touching any of the given paths."""
    output = subprocess.run(
        ["git", "log", "--reverse", "--format=%H|%ad", "--date=iso", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()

    if not output:
        return None, None
    first = output.splitlines()[0]
    commit, _, when = first.partition("|")
    return commit, when


def main() -> int:
    try:
        _log("rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        print("FAIL  not a git repository; integrity cannot be established")
        return 1

    print("CU Route Assistant — evaluation integrity check")
    print("=" * 66)

    eval_commit, eval_when = _first_commit_for(FROZEN)
    if not eval_commit:
        print("FAIL  evaluation sets have never been committed")
        return 1

    impl_commit, impl_when = _first_commit_for(IMPL_GLOBS)
    if not impl_commit:
        print("INFO  no implementation commit found yet; the gate cannot be failed")
        print(f"  evaluation frozen at {eval_when}  ({eval_commit[:10]})")
        return 0

    eval_time = datetime.fromisoformat(eval_when)
    impl_time = datetime.fromisoformat(impl_when)

    print(f"  evaluation frozen : {eval_when}  {eval_commit[:10]}")
    print(f"  implementation    : {impl_when}  {impl_commit[:10]}")

    if eval_time < impl_time:
        print("  RESULT            : PASS — the test set predates the implementation")
        print("=" * 66)
        return 0

    print("  RESULT            : FAIL — the evaluation sets were modified after")
    print("                      implementation began. Every result from this")
    print("                      repository is void. Restore the sets from the")
    print("                      frozen commit and re-run the evaluation with a")
    print("                      new set; do not repair the existing one.")
    print("=" * 66)
    return 1


if __name__ == "__main__":
    sys.exit(main())
