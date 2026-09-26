#!/usr/bin/env python
"""One-off: add a user-facing `summary` to every route in the routing table.

The summary is what a route card shows under the destination name, so that a
user can tell whether they are being sent to the right place before they click.
Each one states only what the cited page actually supports.

Run once, then delete: the summaries are hand-written, not generated, because a
generated summary is a generated claim and this system does not make claims it
cannot cite.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "routing_table.json"

SUMMARIES = {
    "R-ADM-HUB": "Start here if you are not sure which application route you need. This page lists undergraduate, postgraduate and certificate applications.",
    "R-ADM-UG-APPLY": "The undergraduate application is submitted online through this portal, and the same portal is where you sign in to continue or check status.",
    "R-ADM-PG-APPLY": "Postgraduate applications are submitted online here, for Master's and Doctoral programmes.",
    "R-ADM-STATUS": "Sign in to check whether an application has been submitted, is still in progress, or has an outcome.",
    "R-CERT-PROGRAMMES": "Fourteen professional certificate programmes across business, technology and AI, finance and law, and specialised areas.",
    "R-CERT-AI": "Six of the fourteen certificates are specifically about artificial intelligence, from AI for finance and law to AI applications in robotics.",
    "R-FEES-ADMISSIONS": "The University does not publish tuition amounts on any page that can be read reliably, so this is the office to ask for a figure.",
    "R-ADM-EMAIL-UG": "The address the contact page labels for undergraduate admissions. Note that the admission portal publishes a different address for the same job.",
    "R-ADM-EMAIL-PG": "The address the contact page labels for postgraduate admissions.",
    "R-ICT-HELPDESK": "First place for a password problem, a network fault, or anything technical. Staffed Monday to Friday, 8am to 5pm.",
    "R-STUDENT-PORTAL": "Where students see grades and results, and where course registration is done.",
    "R-STAFF-PORTAL": "The staff login, reached from the IT department's services page.",
    "R-ELEARNING": "The e-learning platform, reached from the IT department's services page.",
    "R-ACADEMIC-CALENDAR": "The academic calendar, reached from the IT department's services page.",
    "R-JOBS-PORTAL": "A jobs portal for vacancies, reached from the IT department's services page.",
    "R-SELF-ASSESS": "The self assessment portal, reached from the IT department's services page.",
    "R-LIBRARY": "The university library: borrowing, reserving, renewing, reading guides, and the digital archives.",
    "R-LIBRARY-CATALOG": "The online catalogue for searching the library's books, journals and research materials.",
    "R-SWITCHBOARD": "The university's general contact point for anything not covered by a specialist office, including enquiries about a named member of staff.",
    "R-ADDRESS": "The main campus address, and the right first stop for questions about accommodation or campus transport, which are not published.",
    "R-ABOUT": "Background on the university, its mission, and its development.",
    "R-COURSE-FINDER": "The tool for browsing the programmes on offer.",
    "R-PARTNERSHIPS": "The university's route for industry collaboration, partnerships and sponsorship enquiries.",
    "R-FIMS-API": "The only openly documented API the university publishes. API key requests go to the address shown, with your organisation name, use case, and required scopes.",
    "R-CCSA": "The Centre for Climate-Smart Agriculture, which has its own office in Wuse II and its own contact address.",
    "R-CCSA-PROGRAMMES": "The programmes being developed by the new Faculty of Climate-Smart Agriculture and Sustainability.",
    "R-FILM": "A full diploma in film studies covering eight career areas, from cinematography and editing to screenwriting and sound design.",
    "R-IDEAS-APPLY": "Application route for the IDEAS technology training programme and its five skill tracks.",
    "R-IDEAS-STATUS": "Where an applicant to the IDEAS programme checks their admission status.",
}


def main() -> int:
    with open(PATH, encoding="utf-8") as handle:
        table = json.load(handle)

    missing = []
    for route in table["routes"]:
        summary = SUMMARIES.get(route["route_id"])
        if summary is None:
            missing.append(route["route_id"])
            continue
        # Rebuild the key order so `summary` sits next to `label` in the file,
        # which keeps the table readable for whoever maintains it next.
        rebuilt = {}
        for key, value in route.items():
            rebuilt[key] = value
            if key == "label":
                rebuilt["summary"] = summary
        route.clear()
        route.update(rebuilt)

    if missing:
        print("MISSING SUMMARIES:", ", ".join(missing))
        return 1

    with open(PATH, "w", encoding="utf-8") as handle:
        json.dump(table, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"summaries added to {len(table['routes'])} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
