#!/usr/bin/env python
"""Verify that the evaluation sets predate the implementation.

This is the integrity gate described in the project plan as risk R1, and it is
the check that separates a measurement from a demonstration. If the gold set can
be shown to have been written after the system it evaluates, no result from
that system is worth anything, regardless of how good the number looks.

It is run in CI so that the guarantee cannot lapse quietly.

Two independent checks, because either alone is defeatable:

  1. Ordering. The sets must have been committed before any implementation.
     This catches the ordinary failure: building the system first, then writing
     tests to match it.

  2. Content. The sets must be byte-identical to their state at that first
     commit.

Check 2 exists because check 1 was found to be insufficient during development.
Check 1 reads the *earliest* commit touching the eval files, so an edit made in
a *later* commit is invisible to it: the earliest commit is still the original
freeze, which of course predates the implementation, so the gate reports PASS on
a set whose expected answers have been rewritten to match the system's output.

That is precisely the failure this gate was built to prevent, and it passed. A
gate that cannot fail is worse than no gate, because it is read as reassurance.
The ordering check answers "was this written first?"; only the content check
answers "is it still what was written first?".
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


def _drift_from(commit: str) -> str:
    """Diffstat for the frozen sets between ``commit`` and the working tree.

    Returned as a diffstat, empty when the frozen sets are untouched. Compared
    against the working tree rather than HEAD so that an uncommitted edit is
    caught too: a tampered file that has not been committed yet is still a
    tampered file, and a gate that inspects only history misses it entirely.
    """
    output = subprocess.run(
        ["git", "diff", "--stat", commit, "--", *FROZEN],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return output


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
    print()

    failures = 0

    # --- check 1: ordering -------------------------------------------------
    if eval_time < impl_time:
        print("  [1/2] order      : PASS - the test set predates the implementation")
    else:
        print("  [1/2] order      : FAIL - the evaluation sets were modified after")
        print("                      implementation began. Every result from this")
        print("                      repository is void. Restore the sets from the")
        print("                      frozen commit and re-run the evaluation with a")
        print("                      new set; do not repair the existing one.")
        failures += 1

    # --- check 2: content --------------------------------------------------
    drift = _drift_from(eval_commit)
    if drift:
        print("  [2/2] content    : FAIL - the frozen sets have changed since they")
        print("                      were first committed:")
        for line in drift.splitlines():
            print(f"                        {line}")
        print("                      They are expected to be byte-identical to")
        print(f"                      {eval_commit[:10]}. A test set edited after the")
        print("                      fact measures nothing, however good the score.")
        print("                      Restore it with:")
        print(f"                        git checkout {eval_commit[:10]} -- "
              + " ".join(FROZEN))
        failures += 1
    else:
        print("  [2/2] content    : PASS - the sets are byte-identical to the freeze")

    print("=" * 66)
    if failures:
        print(f"  RESULT           : FAIL ({failures} of 2 checks failed)")
        print("=" * 66)
        return 1

    print("  RESULT           : PASS - the test set is unmodified and predates the")
    print("                      implementation")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
