#!/usr/bin/env python
"""One-off: give two offices the vocabulary for questions that are already theirs.

Run once, then delete.

A refusal names the office that owns the question, so that office has to win the
router. Two did not, because the questions were routed by vocabulary belonging
to a different office:

* "Do you have scholarships for indigent students?" is an admissions question.
  R-ADM-HUB had no scholarship vocabulary, so R-FEES-ADMISSIONS won on the word
  "students" and the refusal pointed a scholarship enquiry at the fees desk.
  Scholarships are administered through admissions; that is a fact about the
  office, not a preference about which route should win.

* "What is the hostel fee and how many rooms are available?" is an accommodation
  question, so R-ADDRESS has to be the plausible one. It scored 0.18 against a
  0.20 bar, which is a vocabulary gap, not a judgement: nothing in the route said
  "hostel".

Also adds U-TRANSPORT. "Is there a campus bus service and what is the fare?"
mentions no accommodation word at all, so no existing topic could own it, and the
system answered it from an address page instead of refusing. Campus transport is
genuinely unpublished and belongs to the same office that already owns "which
first stop do I go to", so it is a topic in its own right rather than a keyword
stuffed into the accommodation topic.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "routing_table.json"

ADM_HUB_KEYWORDS = ["scholarship", "scholarships", "bursary", "bursaries", "financial aid"]

ADDRESS_KEYWORDS = [
    "hostel",
    "hostels",
    "accommodation",
    "where to live",
    "where will i live",
    "campus accommodation",
    "residence",
]

TRANSPORT_TOPIC = {
    "topic_id": "U-TRANSPORT",
    "label": "campus transport",
    "match_terms": [
        "campus bus",
        "bus service",
        "buses",
        "commuter",
        "shuttle",
        "fare",
        "transport",
        "campus transport",
        "get to campus",
        "how do i get to campus",
        "lift",
        "transportation",
    ],
    "min_hits": 2,
    "route_hints": ["R-ADDRESS"],
    "refusal_note": (
        "Campus bus routes, fares, and timetables are not published on the "
        "website, and the operator can change them between terms."
    ),
}


def add_keywords(route: dict, keywords: list[str]) -> None:
    for keyword in keywords:
        if keyword not in route["keywords"]:
            route["keywords"].append(keyword)


def main() -> int:
    table = json.loads(PATH.read_text(encoding="utf-8"))
    routes = {r["route_id"]: r for r in table["routes"]}

    add_keywords(routes["R-ADM-HUB"], ADM_HUB_KEYWORDS)
    add_keywords(routes["R-ADDRESS"], ADDRESS_KEYWORDS)

    topics = table["unpublished_topics"]
    if not any(t["topic_id"] == "U-TRANSPORT" for t in topics):
        topics.append(TRANSPORT_TOPIC)

    PATH.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(table['routes'])} routes, {len(topics)} unpublished topics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
