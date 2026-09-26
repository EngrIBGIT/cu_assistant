#!/usr/bin/env python
"""One-off: set a per-topic abstention threshold.

Run once, then delete.

A single global threshold cannot be right for this vocabulary. "Accommodation"
and "population" are unambiguous in a university context: nobody mentions either
in passing, so one hit is enough to know the question is about something the
University does not publish. "Deadline" and "accepted" are the opposite — "I
want to add and drop subjects before the deadline" is a request to do something,
not a request for a date, and "Has my admission been accepted?" is a request to
check a status the portal owns. Refusing those was wrong, and a uniform
threshold of one had caused exactly that.

The threshold is therefore declared where the vocabulary is discussed, and the
default is one only for terms that cannot plausibly appear by accident.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "routing_table.json"

#: Two corroborating terms needed. Each of these topics owns at least one term
#: that is ordinary English and appears in questions the University *can* answer.
MIN_HITS = {
    "U-FEES": 2,
    "U-DEADLINE": 2,
    "U-ENTRY": 2,
    "U-SCHOLARSHIP": 2,
    "U-EXAMDATE": 2,
    "U-PERSON": 2,
    "U-DURATION": 2,
    "U-PAYMENT": 2,
    # Unambiguous on their own; kept at one.
    "U-ACCOMMODATION": 1,
    "U-POPULATION": 1,
    "U-TRANSCRIPT": 1,
}

EXTRA_TERMS = {
    # "how do i pay" demands the word "how", which "where do I pay" does not
    # have. The shorter phrase is the one users actually type.
    "U-PAYMENT": ["do i pay", "where do i pay"],
}


def main() -> int:
    table = json.loads(PATH.read_text(encoding="utf-8"))
    for topic in table["unpublished_topics"]:
        tid = topic["topic_id"]
        topic["min_hits"] = MIN_HITS[tid]
        for term in EXTRA_TERMS.get(tid, []):
            if term not in topic["match_terms"]:
                topic["match_terms"].append(term)
    PATH.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("per-topic thresholds written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
