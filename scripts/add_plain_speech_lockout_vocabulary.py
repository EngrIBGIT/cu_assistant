#!/usr/bin/env python
"""One-off: give the ICT helpdesk the plain speech people actually use about a
lockout.

Run once, then delete.

The routing table's login vocabulary was written in institutional register —
"cannot log in", "locked out", "forgot my password" — because those are the forms
that appear in the University's own published pages. Real users do not write that
way. "The machine will not let me in anymore and I cannot remember what I typed"
shares no phrase with the table and no content word with any of it: the router
scored every destination below 0.17 against a 0.26 bar, and the user who is
actually locked out of their portal was told the assistant had no information and
given a switchboard number. That is the worst possible answer to that question,
because the person who needs the helpdesk most is the person least likely to
phrase the question in the helpdesk's vocabulary.

This is a vocabulary gap, not a modelling failure. The embedding model cannot fix
it, because the sentence is genuinely semantically thin — it never says
"password" or "portal", it describes a symptom. Only curated phrasing reaches it,
which is exactly what the keyword term exists for.

Added phrases, all of them ordinary things a locked-out user would write:

* "will not let me in" / "not let me in" — the symptom, in the words used.
* "remember what i typed" / "cannot remember" — a forgotten credential described
  as a memory failure rather than a password.
* "it worked yesterday" / "stopped working" — the other standard framing.
* "wrong password" / "says my password is wrong" / "password is incorrect".
* "account locked" / "account is locked" / "blocked".

Deliberately *not* added to R-ADM-STATUS or R-STUDENT-PORTAL: "password" and
"log in" already sit on R-ADM-STATUS, and a generic lockout on an unnamed system
is a technical fault for the department that runs the systems, not an admissions
question. The distinction the table already draws — the admission portal's own
sign-in goes to Admissions, everything else goes to ICT — is preserved.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "routing_table.json"

ICT_HELPDESK_KEYWORDS = [
    "will not let me in",
    "not let me in",
    "let me in",
    "will not accept me",
    "cannot remember",
    "remember what i typed",
    "what i typed",
    "i cannot remember my password",
    "it worked yesterday",
    "stopped working",
    "used to work",
    "wrong password",
    "password is wrong",
    "password is incorrect",
    "incorrect password",
    "says my password is wrong",
    "account locked",
    "account is locked",
    "locked account",
    "blocked",
    "keeps rejecting",
    "rejects my password",
    "will not accept my password",
]


def main() -> int:
    table = json.loads(PATH.read_text(encoding="utf-8"))
    routes = {r["route_id"]: r for r in table["routes"]}

    helpdesk = routes["R-ICT-HELPDESK"]
    added = []
    for keyword in ICT_HELPDESK_KEYWORDS:
        if keyword not in helpdesk["keywords"]:
            helpdesk["keywords"].append(keyword)
            added.append(keyword)

    PATH.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"added {len(added)} phrases to R-ICT-HELPDESK: {', '.join(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
