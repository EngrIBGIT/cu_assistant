"""Server-rendered no-JavaScript experience.

This is a first-class interface, not an error page. The audit that motivated
this project found that the University's own website returns no content at all
to clients that do not execute JavaScript, excluding exactly the users on low
bandwidth, low-end devices, and assistive technology. A remedy for that problem
that required JavaScript would reproduce the problem.

Everything here is plain semantic HTML with inline CSS, so it needs no build
step, no external stylesheet, and no network round trip beyond the first page
load. It carries the entire primary journey:

* every destination, with its contact details and the URL it was compiled from;
* every topic the University does not publish, stated plainly rather than
  omitted, so a user is never left wondering whether the site simply forgot;
* the published contact conflicts, disclosed rather than silently resolved.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .config import ROUTING_TABLE_PATH, settings

_CATEGORY_LABELS = {
    "admissions": "Applying and programmes",
    "fees": "Fees and payment",
    "it": "Technical support",
    "student": "Student services",
    "staff": "Staff services",
    "library": "Library",
    "general": "General enquiries",
    "careers": "Careers",
    "research": "Research centres",
    "technical": "APIs and data",
}

_CSS = """
:root{--ink:#16202b;--muted:#5a6b7b;--line:#d5dee6;--bg:#f6f8fa;--card:#fff;
--accent:#0b5d3b;--accent-ink:#fff;--warn:#8a4b00;--warn-bg:#fff6e6;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
a:focus,button:focus{outline:3px solid #b8560f;outline-offset:2px}
.skip{position:absolute;left:-9999px}
.skip:focus{left:8px;top:8px;background:#fff;padding:8px;z-index:10}
header{background:var(--accent);color:var(--accent-ink);padding:20px 16px}
header .wrap,main,footer .wrap{max-width:960px;margin:0 auto}
header h1{margin:0 0 4px;font-size:1.4rem}
header p{margin:0;opacity:.92;font-size:.95rem}
main{padding:20px 16px 48px}
h2{font-size:1.15rem;margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h3{font-size:1rem;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px 16px;margin:0 0 12px}
dl{margin:0}
dt{font-weight:600;font-size:.82rem;text-transform:uppercase;letter-spacing:.03em;
color:var(--muted);margin-top:8px}
dt:first-child{margin-top:0}
dd{margin:2px 0 0}
.note{background:var(--warn-bg);border-left:4px solid var(--warn);padding:12px 14px;
border-radius:0 6px 6px 0;margin:0 0 16px}
.note strong{display:block;margin-bottom:4px}
.src{display:inline-block;margin-top:10px;font-size:.82rem;color:var(--muted)}
code{background:#eef2f5;padding:1px 5px;border-radius:3px;font-size:.92em}
footer{border-top:1px solid var(--line);padding:18px 16px;font-size:.88rem;color:var(--muted)}
ul{padding-left:20px}
"""


def _load() -> dict:
    with open(ROUTING_TABLE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _contact_dl(route: dict) -> str:
    rows: list[str] = []
    if route.get("office"):
        rows.append(f"<dt>Office</dt><dd>{_esc(route['office'])}</dd>")
    if route.get("entry_point", "").startswith("http"):
        rows.append(
            f'<dt>Where to go</dt><dd><a href="{_esc(route["entry_point"])}">'
            f"{_esc(route['entry_point'])}</a></dd>"
        )
    if route.get("email"):
        rows.append(
            f'<dt>Email</dt><dd><a href="mailto:{_esc(route["email"])}">'
            f"{_esc(route['email'])}</a></dd>"
        )
    if route.get("phone"):
        rows.append(f"<dt>Phone</dt><dd>{_esc(route['phone'])}</dd>")
    if route.get("alternative_phone"):
        rows.append(
            "<dt>Also published</dt><dd>"
            f"{_esc(route['alternative_phone'])}</dd>"
        )
    if route.get("whatsapp"):
        rows.append(f"<dt>WhatsApp</dt><dd>{_esc(route['whatsapp'])}</dd>")
    if route.get("hours"):
        rows.append(f"<dt>Opening hours</dt><dd>{_esc(route['hours'])}</dd>")
    if route.get("address"):
        rows.append(f"<dt>Address</dt><dd>{_esc(route['address'])}</dd>")
    return f"<dl>{''.join(rows)}</dl>"


def render(demo: bool = False) -> str:
    table = _load()
    routes = table["routes"]
    grouped: dict[str, list[dict]] = {}
    for route in routes:
        grouped.setdefault(route.get("category", "general"), []).append(route)

    sections: list[str] = []
    for category, items in grouped.items():
        heading = _CATEGORY_LABELS.get(category, category.replace("_", " ").title())
        anchor = f"cat-{category}"
        cards: list[str] = []
        for route in sorted(items, key=lambda r: r["label"]):
            conflict = ""
            if route.get("verification") == "published_conflicting":
                conflict = (
                    '<p class="src"><strong>Note:</strong> the University publishes more '
                    "than one contact detail for this. If the first does not answer, "
                    "try the second.</p>"
                )
            source = ""
            if route.get("source_url"):
                source = (
                    f'<p class="src">Compiled from '
                    f'<a href="{_esc(route["source_url"])}">{_esc(route["source_url"])}</a></p>'
                )
            cards.append(
                f'<article class="card"><h3>{_esc(route["label"])}</h3>'
                f"{_contact_dl(route)}{conflict}{source}</article>"
            )
        sections.append(
            f'<h2 id="{_esc(anchor)}">{_esc(heading)}</h2>{"".join(cards)}'
        )

    # In-page navigation, generated rather than hand-written. With ten categories
    # and twenty-nine destinations, a reader using a screen reader or a keyboard
    # otherwise has to traverse the entire document to reach the library section.
    # Anchors are the only navigation that works without scripting, so this is the
    # part of the page that has to exist for those readers.
    nav_items = "".join(
        f'<li><a href="#cat-{_esc(category)}">'
        f"{_esc(_CATEGORY_LABELS.get(category, category.replace('_', ' ').title()))}"
        "</a></li>"
        for category in grouped
    )
    nav = (
        '<nav aria-label="Sections of this directory"><h2>Jump to</h2>'
        f"<ul>{nav_items}</ul></nav>"
    )

    def _topic_note(topic: dict) -> str:
        """The sentence explaining why a topic is not published.

        Two field names are accepted because both exist in the routing table, and
        one of them was introduced by a later script. Reading only ``note`` made
        this page raise ``KeyError: 'note'`` for a topic written with
        ``refusal_note`` — which is the worst possible failure for the no-JavaScript
        page specifically. It is the interface the audit says the users who most
        need help depend on, it was returning a 500 to every one of them, and
        nothing caught it, because every other test calls the pipeline directly and
        the pipeline does not read this field at all.

        A missing note is tolerated for the same reason: this page must render
        even when the data is incomplete, because an error page tells a locked-out
        applicant nothing.
        """
        return topic.get("note") or topic.get("refusal_note") or ""

    unpublished_rows = "".join(
        f"<li><strong>{_esc(t['label'])}</strong> — {_esc(_topic_note(t))}</li>"
        for t in table.get("unpublished_topics", [])
    )

    banner = ""
    if demo:
        banner = """
<div class="note">
<strong>This page works with JavaScript switched off.</strong>
Cosmopolitan University's own website returns no content to clients that do not
execute JavaScript, which excludes people on limited data, older phones, and some
assistive technology. So this directory is entirely server-rendered: everything
you can ask the assistant is listed here as plain HTML. The chat assistant is an
addition to this page, never a prerequisite for it.
</div>
<p><a href="/fallback">Open the plain directory without the demo header</a> &middot;
<a href="/docs">API documentation</a> &middot;
<a href="/api/health">Service health</a></p>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(settings.app_name)} — contact directory for {_esc(settings.institution)}</title>
<meta name="description" content="Find the right portal, office, email address, or phone number for Cosmopolitan University, Abuja.">
<style>{_CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header><div class="wrap">
<h1>{_esc(settings.app_name)}</h1>
<p>Which door? Contact directory for {_esc(settings.institution)}</p>
</div></header>
<main id="main">
{banner}
{nav}
<div class="note">
<strong>Check fees and deadlines with the University before you rely on them.</strong>
The University does not publish tuition amounts, application deadlines, entry
requirements, scholarships, or accommodation on any page that can be read
reliably. This assistant will not guess at any of them. The contacts below are
compiled from the University's own published pages, and the page each one came
from is shown.
</div>
{''.join(sections)}
<h2>What the University does not publish</h2>
<p>These are common questions this assistant is asked and cannot answer from
published sources. It says so rather than inventing an answer.</p>
<ul>{unpublished_rows}</ul>
</main>
<footer><div class="wrap">
<p>Compiled {_esc(table.get('compiled_at', ''))} from publicly available pages.
Routing table version {_esc(table.get('routing_table_version', ''))}.
No personal data is collected by this service and no user query is stored or used
to train any model.</p>
</div></footer>
</body>
</html>"""
