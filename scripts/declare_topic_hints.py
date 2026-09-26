#!/usr/bin/env python
"""One-off: declare each unpublished topic's owning offices, and fix the two
keyword sets that mis-owned questions.

Run once, then delete.

Three changes, each from an observed failure rather than a guess:

1. ``route_hints`` on every topic, in preference order. The refusal path needs
   to choose between plausible destinations, and a single hardcoded hint cannot
   serve a topic whose owners differ by question: "how long does the Project
   Management certificate take" belongs to the certificate programmes page while
   "how many programmes can I list on the undergraduate portal" belongs to the
   undergraduate application portal.

2. Deadline vocabulary. "When does the application close?" was answered rather
   than refused, because the topic listed "closes on" and "closing date" but not
   the plain verb.

3. Named-personnel ownership. R-SWITCHBOARD's summary claimed it handled
   "enquiries about a named member of staff", which made it win every question
   of the shape "who is the Vice Chancellor". The University describes its
   leadership on the About page; the switchboard is where you go when the About
   page has not answered. R-ABOUT now carries the identity vocabulary.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "routing_table.json"

HINTS = {
    "U-FEES": ["R-FEES-ADMISSIONS"],
    "U-DEADLINE": ["R-ADM-HUB", "R-ADM-UG-APPLY", "R-ADM-PG-APPLY"],
    "U-ENTRY": ["R-ADM-HUB", "R-ADM-UG-APPLY"],
    "U-SCHOLARSHIP": ["R-ADM-HUB", "R-FEES-ADMISSIONS"],
    "U-ACCOMMODATION": ["R-ADDRESS", "R-FEES-ADMISSIONS"],
    "U-PAYMENT": ["R-FEES-ADMISSIONS"],
    "U-EXAMDATE": ["R-ACADEMIC-CALENDAR"],
    "U-PERSON": ["R-ABOUT", "R-SWITCHBOARD"],
    "U-DURATION": [
        "R-ADM-UG-APPLY",
        "R-ADM-PG-APPLY",
        "R-CERT-PROGRAMMES",
        "R-ADM-HUB",
    ],
    "U-POPULATION": ["R-ABOUT"],
    "U-TRANSCRIPT": ["R-STUDENT-PORTAL"],
}

EXTRA_DEADLINE_TERMS = [
    "application close",
    "admission close",
    "closes",
    "when does it close",
    "application deadline",
]

ABOUT_KEYWORDS = [
    "who is",
    "who is the",
    "vice chancellor",
    "vice-chancellor",
    "mission",
    "core values",
    "history of the university",
    "who leads the university",
]

UG_APPLY_KEYWORDS = [
    "undergraduate portal",
    "ug portal",
    "undergraduate application",
    "apply to undergraduate",
    "undergraduate admission",
]

SWITCHBOARD_SUMMARY = (
    "The university's general contact point for anything not covered by a "
    "specialist office, including reaching a named member of staff."
)

ABOUT_SUMMARY = (
    "Background on the university, its mission, its values, and its leadership. "
    "This is also where a named office holder is described."
)


def main() -> int:
    table = json.loads(PATH.read_text(encoding="utf-8"))

    for topic in table["unpublished_topics"]:
        topic["route_hints"] = HINTS[topic["topic_id"]]
        if topic["topic_id"] == "U-DEADLINE":
            for term in EXTRA_DEADLINE_TERMS:
                if term not in topic["match_terms"]:
                    topic["match_terms"].append(term)

    for route in table["routes"]:
        rid = route["route_id"]
        if rid == "R-ABOUT":
            for keyword in ABOUT_KEYWORDS:
                if keyword not in route["keywords"]:
                    route["keywords"].append(keyword)
            route["summary"] = ABOUT_SUMMARY
        elif rid == "R-SWITCHBOARD":
            route["summary"] = SWITCHBOARD_SUMMARY
        elif rid == "R-ADM-UG-APPLY":
            for keyword in UG_APPLY_KEYWORDS:
                if keyword not in route["keywords"]:
                    route["keywords"].append(keyword)

    PATH.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"{len(table['unpublished_topics'])} topics given route hints, "
        "keyword sets updated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
