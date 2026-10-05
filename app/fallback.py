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
* the published contact conflicts, disclosed rather than silently resolved;
* a question box, so a reader who cannot execute JavaScript can *ask* rather
  than only browse.

That last part was the gap. The page was a complete directory and nothing more,
so the claim that it "carries the whole primary journey" was true of routing and
false of answering: a user who knew which category their problem sat in was
served, and a user who did not — which is the majority, and is exactly the user
the audit was written about — had to read twenty-nine cards to find out. The
form is a plain ``GET``, which is the oldest and most reliable way to submit
something without scripting, and it calls the same ``Assistant.ask`` the API and
the widget call, so there is still exactly one decision path.
"""

from __future__ import annotations

import html
import json
import re
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

#: The page's stylesheet, inlined rather than linked.
#:
#: Inlined because the no-JavaScript page must render correctly when nothing else
#: loads, and because a second request is a second thing that can fail on a
#: metered connection. The chat widget's own stylesheet is a separate file
#: because that one is shared with third-party embeds.
#:
#: The visual language is the Academic Institutional system: a #F6F8FA canvas,
#: white modules separated by a 1px #E2E8F0 rule rather than by a shadow, 4px on
#: controls and 8px on cards, and no drop shadow anywhere on this page because
#: nothing here floats. Amber is reserved exclusively for abstention and caution,
#: so a reader learns in one glance that amber means "check this yourself".
#:
#: ``--muted`` is #5C6A77 rather than the #64748B the design system names. The
#: system's own grey clears 4.5:1 on white but not on the ribbon band, and the
#: ribbon is one of the two places the muted colour carries text; the substituted
#: value measures 4.93:1 there and 5.21:1 on the canvas, so every muted pair in
#: both schemes clears AA. That is the one place this stylesheet departs from the
#: system it implements, and it departs in the direction of legibility.
#:
#: Two further constraints are load-bearing rather than incidental. The font
#: stack is system-only, because this page exists for people on metered
#: connections and a webfont is a request this page can decline to make; Source
#: Sans 3 is named first so a machine that has it installed uses it. And the
#: layout is one column, because the page has to work on a 320px phone, which is
#: the case the audit identified. Every interactive element has a visible focus
#: ring, and the dark scheme is applied from the same rules.
_CSS = """
:root{
  --ink:#1f2937;--head:#2c3a47;--muted:#5c6a77;--line:#e2e8f0;--line-strong:#d1d5db;
  --line-soft:#eef2f6;--bg:#f6f8fa;--card:#fff;--accent:#0b5d3b;--accent-hover:#08492e;
  --accent-ink:#fff;--accent-wash:#eff6f1;--field:#f6f8fa;--field-line:#cbd5e1;
  --warn:#8a4b00;--warn-bg:#fffbeb;--warn-line:#fde68a;--warn-ink:#5c3200;
  --focus:#0b5d3b;
  --r-sm:4px;--r-md:8px;--r-lg:12px;--measure:68ch;
  --sans:"Source Sans 3",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
    Helvetica,Arial,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,
    "Liberation Mono",monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --ink:#e8eef3;--head:#e8eef3;--muted:#9fb0bf;--line:#2c3a47;--line-strong:#3a4b5a;
    --line-soft:#1e2a35;--bg:#0f151b;--card:#141c24;--accent:#2fa36a;
    --accent-hover:#14a06a;--accent-ink:#08120c;--accent-wash:#10241b;
    --field:#0f151b;--field-line:#3a4b5a;
    --warn:#e0a458;--warn-bg:#2b1f0c;--warn-line:#6b4a1c;--warn-ink:#f4d9b0;
  }
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font:400 16px/1.6 var(--sans);
  -webkit-text-size-adjust:100%;
}
a{color:var(--accent)}
a:hover{color:var(--accent-hover)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:3px}
.skip{position:absolute;left:-9999px}
.skip:focus{left:8px;top:8px;background:var(--card);color:var(--ink);
  padding:10px 14px;z-index:10;border-radius:var(--r-sm);
  box-shadow:0 2px 4px rgba(44,58,71,.06),0 1px 2px rgba(44,58,71,.04)}

/* ---- masthead ----------------------------------------------------------- */
header{background:var(--card);border-bottom:1px solid var(--line)}
header .wrap,main,.ribbon .wrap,footer .wrap{max-width:60rem;margin:0 auto}
.bar{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;padding:14px 16px}
.bar .wordmark{font:700 13px/1.4 var(--sans);letter-spacing:.05em;
  text-transform:uppercase;color:var(--head)}
.bar .institution{font-size:.86rem;color:var(--muted)}
.bar nav{display:flex;flex-wrap:wrap;gap:14px;margin-left:auto;
  font:600 13px/1.4 var(--sans)}
.bar nav a{color:var(--muted);text-decoration:none}
.bar nav a:hover{color:var(--accent);text-decoration:underline}
.bar .appid{padding:3px 8px;border:1px solid var(--line-strong);
  border-radius:var(--r-sm);background:var(--bg);color:var(--muted);
  font:700 11px/1.4 var(--sans);letter-spacing:.05em;text-transform:uppercase}

/* The band under the masthead. It carries the standing claim the whole service
   rests on — the directory is compiled from published pages, and nothing a user
   types is kept — so it is present whether or not anyone reaches the footer. */
.ribbon{background:var(--line-soft);border-bottom:1px solid var(--line)}
.ribbon .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:4px 16px;
  padding:7px 16px}
.ribbon .claim{font:700 11px/1.5 var(--sans);letter-spacing:.05em;
  text-transform:uppercase;color:var(--head)}
.ribbon .meta{font-size:.8rem;color:var(--muted)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:var(--accent);margin-right:6px;vertical-align:1px}
main{padding:24px 16px 56px}

/* The chat is the first thing on the page, so the page reads as an assistant
   first and a directory second. On /fallback there is no chat and the form
   below becomes the first thing instead. */
#cra-root{margin:0 0 24px}
#cra-root:empty{display:none}

/* ---- editorial title block ----------------------------------------------- */
.hero{margin:0 0 20px}
.hero .eyebrow{font:700 11px/1.5 var(--sans);letter-spacing:.05em;
  text-transform:uppercase;color:var(--accent)}
.hero h1{margin:6px 0 0;font-size:1.9rem;line-height:1.2;font-weight:700;
  letter-spacing:-.02em;color:var(--head)}
.hero .tagline{margin:8px 0 0;max-width:var(--measure);color:var(--muted)}

/* ---- typography --------------------------------------------------------- */
h3{font-size:1.12rem;margin:0 0 8px;line-height:1.3;color:var(--head)}
h4{font-size:1rem;margin:0 0 5px;line-height:1.4;color:var(--head)}
p,ul,dl{max-width:var(--measure)}
ul{padding-left:20px}
li{margin:.25em 0}

/* Section headings read as taxonomy bands rather than as decorated titles: a
   rule under the text, a green square as the marker, and a count of the
   destinations underneath. */
h2{font-size:1.02rem;margin:34px 0 12px;padding-bottom:8px;
  border-bottom:1px solid var(--line);letter-spacing:.04em;font-weight:700;
  text-transform:uppercase;color:var(--head);display:flex;
  align-items:center;gap:8px}
h2::before{content:"";width:9px;height:9px;background:var(--accent);
  border-radius:2px;flex:none}

/* ---- ask form ----------------------------------------------------------- */
.ask{background:var(--card);border:1px solid var(--line-strong);
  border-radius:var(--r-lg);padding:18px;margin:0 0 22px}
.ask .badge{display:inline-block;margin:0;padding:3px 8px;
  border-radius:var(--r-sm);background:var(--line-soft);color:var(--head);
  font:700 11px/1.5 var(--sans);letter-spacing:.05em;text-transform:uppercase}
.ask .badge .dot{width:6px;height:6px;margin-right:5px;vertical-align:1px}
.ask label{display:block;font-weight:600;margin:12px 0 7px}
.ask input[type=text]{width:100%;padding:12px;font:inherit;color:var(--ink);
  background:var(--field);border:1px solid var(--field-line);
  border-radius:var(--r-sm)}
.ask input[type=text]::placeholder{color:var(--muted)}
.ask input[type=text]:focus-visible{border-color:var(--accent)}
.ask button{margin-top:11px;font:600 15px/1.2 var(--sans);padding:13px 22px;
  border:1px solid var(--accent);border-radius:var(--r-sm);
  background:var(--accent);color:var(--accent-ink);cursor:pointer}
.ask button:hover{background:var(--accent-hover);border-color:var(--accent-hover)}
.ask .src{margin:12px 0 0}

/* ---- answer ------------------------------------------------------------- */
.answer p{margin:0 0 10px}
.answer ul{margin:0 0 10px}
.answer-route{background:var(--card);border:1px solid var(--line-strong);
  border-left:3px solid var(--accent);border-radius:var(--r-md);padding:16px;
  margin:16px 0}
.status{background:var(--warn-bg);border:1px solid var(--warn-line);
  border-left:4px solid var(--warn);padding:12px 14px;
  border-radius:0 var(--r-sm) var(--r-sm) 0;margin:0 0 16px;
  color:var(--warn-ink);max-width:var(--measure)}
.status strong{color:var(--warn)}
.sources{font-size:.92rem;margin:0 0 6px}

/* ---- shared bits -------------------------------------------------------- */
dl{margin:0}
dt{font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);margin-top:10px}
dt:first-child{margin-top:0}
dd{margin:3px 0 0;overflow-wrap:anywhere}
/* An office, room, extension or address is a record copied out of a directory,
   not prose, so it is set monospaced and reads as a value to be transcribed. */
dd{font-family:var(--mono);font-size:13px;line-height:1.45}
.note{background:var(--warn-bg);border:1px solid var(--warn-line);
  border-left:4px solid var(--warn);padding:15px 17px;
  border-radius:0 var(--r-sm) var(--r-sm) 0;margin:0 0 22px;
  max-width:var(--measure);color:var(--warn-ink);font-size:.94rem}
.note strong{display:block;margin-bottom:4px;color:var(--warn)}
.src{display:block;margin-top:10px;font-size:.84rem;color:var(--muted)}
.src a{overflow-wrap:anywhere}
/* The demo-page service links. Present on / only, and deliberately plain: they
   are for whoever is running the thing, not for an applicant. */
.service-links{margin:-10px 0 22px;font-size:.88rem}
code{background:var(--line-soft);padding:1px 5px;border-radius:3px;
  font-family:var(--mono);font-size:.92em}

/* ---- directory ---------------------------------------------------------- */
.jumpto{display:flex;flex-wrap:wrap;gap:6px;padding:0;margin:0 0 24px;
  list-style:none}
.jumpto li{margin:0}
.jumpto a{display:inline-block;padding:7px 12px;border:1px solid var(--line);
  border-radius:var(--r-sm);background:var(--card);color:var(--accent);
  font:600 13px/1.4 var(--sans);text-decoration:none}
.jumpto a:hover{background:var(--accent-wash);border-color:var(--accent);
  color:var(--accent-hover)}
.directory .card{background:var(--card);border:1px solid var(--line);
  border-radius:var(--r-md);padding:16px 18px;margin:0 0 10px}
.directory .card .count{font-weight:400;letter-spacing:.01em;
  text-transform:none;color:var(--muted);font-size:.82rem}

footer{background:var(--card);border-top:1px solid var(--line);padding:20px 16px;
  font-size:.88rem;color:var(--muted)}
footer p{margin:0;max-width:60rem;margin-inline:auto}

@media (min-width:44rem){
  .ask{display:grid;grid-template-columns:1fr auto;gap:8px 10px;align-items:start}
  .ask .badge,.ask label,.ask .src{grid-column:1/-1}
  .ask input[type=text]{grid-column:1}
  .ask button{grid-column:2;margin-top:0;padding:13px 26px;white-space:nowrap}
}
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
    # ``or ""`` rather than a default argument: a routing-table row omits the key
    # when there is no entry point, but a serialised Route carries it as None, and
    # ``dict.get(k, "")`` returns the None rather than the default.
    if (route.get("entry_point") or "").startswith("http"):
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


#: What to say when there is no answer, keyed by why there is none, as
#: ``(reason prefix, lead, detail)``. An empty ``lead`` means no status line: a
#: greeting is not a failure and should not be dressed as one.
#:
#: This used to be a single sentence — "Not published. The University does not
#: publish this on any page this assistant can read" — applied to every
#: abstention. That is a specific factual claim, and it is false for three of the
#: four reasons the pipeline can abstain: the weather in Abuja is not a gap in the
#: University's publishing, a request to hack into someone's account is not
#: something the University declined to publish, and "hi" was never asking for a
#: fact at all. A status line that says the wrong thing is worse than no status
#: line, because the user believes it.
_ABSTENTION_STATUS: list[tuple[str, str, str]] = [
    (
        "unpublished topic",
        "Not published.",
        "The University does not publish this on any page this assistant can "
        "read, so it is not guessed at.",
    ),
    ("safety gate", "I will not help with that.", "The reason is below."),
    ("conversational", "", ""),
    (
        "outside domain",
        "Outside what I cover.",
        "I answer from the University's own published pages, so this one is not "
        "something I can answer.",
    ),
    (
        "no route above threshold",
        "No published answer.",
        "I would rather tell you that than invent one.",
    ),
]


def _status_for(result: object) -> str:
    """The one-line explanation above the answer, or nothing if there is no answer."""
    if not getattr(result, "abstained", False):
        return ""
    reason = str(getattr(result, "abstention_reason", "") or "")
    for prefix, lead, detail in _ABSTENTION_STATUS:
        if reason.startswith(prefix):
            if not lead:
                return ""
            return f'<p class="status"><strong>{_esc(lead)}</strong> {_esc(detail)}</p>'
    return (
        '<p class="status"><strong>No published answer.</strong> I would rather '
        "tell you that than invent one.</p>"
    )


_ITALIC = re.compile(r"(?<![\w*])_([^_\n]+?)_(?![\w*])")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _markdown_to_html(text: str) -> str:
    """Render the two inline conventions the composer actually uses.

    ``**bold**`` and ``_italic_``, after HTML escaping, so the answer text can
    never introduce markup of its own. Stripping ``**`` and leaving ``_`` alone
    is what produced a page reading "…before you rely on them._" with the
    underscores visible, which looked like a rendering fault even though the
    answer underneath it was correct.
    """
    escaped = _esc(text)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", escaped)


def _strip_trailing_disclaimer(body: str) -> str:
    """Remove the composer's trailing disclaimer from an answer, once.

    Matched against the configured text rather than against a pattern like
    ``_[^_]*disclaim[^_]*_``. The disclaimer reads "Check time-sensitive facts
    such as fees and deadlines ...", which does not contain the word
    "disclaimer", so the pattern never matched and the duplicate stayed on every
    answer while the code claimed to have removed it.

    Only an exact trailing match is removed. An answer that ends with genuine
    italics keeps them, because silently eating the last line of a grounded
    answer is a worse failure than a repeated warning.
    """
    text = body.rstrip()
    disclaimer = (settings.disclaimer or "").strip()
    if disclaimer and text.rstrip("_").rstrip().endswith(disclaimer):
        head = text.rstrip("_").rstrip()
        return head[: -len(disclaimer)].rstrip().rstrip("_").rstrip()
    return text


def _blocks_html(text: str) -> str:
    """Render answer text as paragraphs and lists.

    A run of ``- item`` lines becomes a real ``<ul>``, so the orientation reply
    reads as a list rather than displaying its own dashes. The pattern this
    replaces matched a list only when a line of prose came first, so a block
    that began with a dash rendered as literal ``- the right portal`` — which
    is the greeting case, the first thing a new visitor sees.
    """
    out: list[str] = []
    run: list[str] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_markdown_to_html(' '.join(para))}</p>")
            para.clear()

    def flush_run() -> None:
        if run:
            out.append(
                "<ul>" + "".join(f"<li>{_markdown_to_html(i)}</li>" for i in run) + "</ul>"
            )
            run.clear()

    for block in re.split(r"\n{2,}", text):
        if not block.strip():
            continue
        for line in block.split("\n"):
            item = re.match(r"\s*[-*]\s+(\S.*)$", line)
            if item:
                flush_para()
                run.append(item.group(1).strip())
            elif line.strip():
                flush_run()
                para.append(line.strip())
            else:
                flush_para()
                flush_run()
        flush_para()
        flush_run()
    return "".join(out)


def _answer_html(result: object) -> str:
    """Render an :class:`AskResponse` as plain HTML.

    The answer text is Markdown-flavoured, so it is escaped first and then given
    the two inline conventions it uses. Anything richer would be a Markdown
    parser, and a no-JavaScript page that silently dropped the user's own question
    on the way through would be worse than one that renders it plainly.

    The abstention state is stated in words as well as by omission, and the words
    are chosen to match the reason. A user who is told "this is not published,
    here is who to ask" and a user who is shown three paragraphs that happen not
    to contain the answer have been given very different experiences, and only
    the first one is honest about what happened.
    """
    if result is None:
        return ""

    status = _status_for(result)

    body = str(getattr(result, "answer", "") or "")
    # The page carries the disclaimer in full, in the note above the directory.
    # Repeating it verbatim under every answer put two near-identical warnings
    # one screen apart, which reads as noise and trains people to skip both.
    body = _strip_trailing_disclaimer(body).strip()

    blocks = _blocks_html(body)
    paragraphs = blocks

    route_html = ""
    route = getattr(result, "primary_route", None)
    if route is not None:
        also = getattr(result, "also_consider", []) or []
        extra = ""
        if also:
            names = ", ".join(_esc(r.label) for r in also)
            extra = f"<p><strong>Also consider:</strong> {names}</p>"
        route_html = (
            f'<div class="answer-route"><h3>Where to go</h3>'
            f"{_contact_dl(route.model_dump())}{extra}</div>"
        )

    sources = ""
    citations = getattr(result, "citations", []) or []
    if citations:
        items = "".join(
            f'<li><a href="{_esc(c.source_url)}">{_esc(c.source_url)}</a></li>'
            for c in citations
            if c.source_url
        )
        if items:
            sources = (
                "<h3>Sources</h3>"
                f'<ul class="sources">{items}</ul>'
                '<p class="src">Every factual statement above is quoted from one of '
                "these pages. Follow the link to check it.</p>"
            )

    return f'{status}<div class="answer">{paragraphs}</div>{route_html}{sources}'


def render(
    demo: bool = False,
    question: str = "",
    result: object | None = None,
    api_error: str = "",
    api_url: str = "",
) -> str:
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
        # The count is the useful half of this heading. A reader scanning ten
        # category bands needs to know whether the section they skipped held two
        # desks or nine before deciding to go back, and stating it is a fact
        # about the page rather than a claim about the University.
        plural = "destination" if len(items) == 1 else "destinations"
        sections.append(
            f'<h2 id="{_esc(anchor)}">{_esc(heading)}'
            f'<span class="count">{len(items)} {plural}</span></h2>'
            f'{"".join(cards)}'
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
        # When the API is a separate process these have to be absolute. A
        # relative /api/health on the frontend is a 404, and a demo page that
        # links to its own 404 is worse than one that omits the link.
        docs_link = f"{api_url}/docs" if api_url else "/docs"
        health_link = f"{api_url}/api/health" if api_url else "/api/health"
        routes_link = f"{api_url}/api/routes" if api_url else "/api/routes"
        banner = f"""
<div class="note">
<strong>This page works with JavaScript switched off.</strong>
Cosmopolitan University's own website returns no content to clients that do not
execute JavaScript, which excludes people on limited data, older phones, and some
assistive technology. So this directory is entirely server-rendered: everything
you can ask the assistant is listed here as plain HTML. The chat assistant is an
addition to this page, never a prerequisite for it.
</div>
<p class="service-links"><a href="/fallback">Open the plain directory without the demo header</a> &middot;
<a href="{_esc(docs_link)}">API documentation</a> &middot;
<a href="{_esc(health_link)}">Service health</a> &middot;
<a href="{_esc(routes_link)}">Routing table as data</a></p>
"""

    # The widget is loaded on the demo page only, and only as two same-origin
    # files. It used not to be loaded at all, which made the README's "the
    # assistant, with the widget" untrue: the page mentioned the widget in prose
    # and served the file for third parties to embed, but never ran it. An inline
    # <script> setting the API URL would be simpler and would break the
    # `script-src 'self'` policy this service now sets, so the value is served as
    # a file of its own.
    widget_scripts = ""
    if demo:
        widget_scripts = (
            '<script src="/widget-config.js"></script>\n'
            '<script src="/widget.js" defer></script>'
        )

    # A plain GET form, no action attribute so it submits back to whichever URL
    # it was rendered on. The submit button is styled but the form still works
    # if CSS fails to load, because the control is a real <input type=submit>.
    ask_form = (
        '<form class="ask" method="get">'
        '<p class="badge"><span class="dot"></span>Official inquiry and routing portal'
        "</p>"
        '<label for="q">Ask a question</label>'
        '<input type="text" id="q" name="question" maxlength="500" '
        f'value="{_esc(question)}" '
        'placeholder="How do I apply for undergraduate admission?">'
        '<button type="submit">Ask without JavaScript</button>'
        '<p class="src">This box needs no scripting. It is answered by the same '
        "assistant the chat widget uses, on the server, and it quotes the same "
        "sources.</p>"
        "</form>"
    )

    answer_block = ""
    if api_error:
        # A page that cannot reach the assistant says so. Rendering an empty
        # answer instead would be indistinguishable from the service declining to
        # answer a question it was never asked, and those two failures deserve
        # opposite reactions from the reader.
        answer_block = (
            '<section aria-labelledby="answer-h">'
            '<h2 id="answer-h">Your answer</h2>'
            f'<p class="status"><strong>Service unavailable.</strong> {_esc(api_error)} '
            "The question is still in the box above — try again, or use the "
            "destinations listed on this page, which need no service at all."
            "</p></section>"
        )
    elif result is not None:
        answer_block = (
            '<section aria-labelledby="answer-h">'
            '<h2 id="answer-h">Your answer</h2>'
            f"{_answer_html(result)}</section>"
        )

    # On the demo page the chat mounts inline into this element. It is always
    # present in the markup and simply stays empty when the widget is not
    # loaded, so the page is laid out the same either way and the fallback page
    # does not carry an element that does nothing.
    chat_root = '<div id="cra-root"></div>' if demo else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(settings.app_name)} — contact directory for {_esc(settings.institution)}</title>
<meta name="description" content="Find the right portal, office, email address, or phone number for Cosmopolitan University, Abuja.">
<style>{_CSS}</style>
{widget_scripts}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header><div class="wrap bar">
<span class="wordmark">{_esc(settings.institution)}</span>
<span class="appid">{_esc(settings.app_name)}</span>
<nav aria-label="This page">
<a href="#main">Ask</a>
<a href="#directory">Directory</a>
<a href="#notpublished">Not published</a>
</nav>
</div></header>
<div class="ribbon"><div class="wrap">
<span class="claim"><span class="dot"></span>Compiled from the University&rsquo;s own
published pages</span>
<span class="meta">No accounts, no cookies, no question stored &middot; read-only
service</span>
</div></div>
<main id="main">
{chat_root}
<section class="hero">
<span class="eyebrow">Contact directory</span>
<h1>{_esc(settings.app_name)}</h1>
<p class="tagline">Which door? Find the right portal, office, email address, or
phone number for {_esc(settings.institution)} &mdash; answered from the
University&rsquo;s own published pages, with the source shown for every claim.</p>
</section>
{ask_form}
{answer_block}
{banner}
<nav aria-label="Sections of this directory"><h2>Jump to a section</h2>
<ul class="jumpto">{nav_items}</ul></nav>
<div class="note">
<strong>Check fees and deadlines with the University before you rely on them.</strong>
The University does not publish tuition amounts, application deadlines, entry
requirements, scholarships, or accommodation on any page that can be read
reliably. This assistant will not guess at any of them. The contacts below are
compiled from the University's own published pages, and the page each one came
from is shown.
</div>
<div class="directory" id="directory">
{''.join(sections)}
<h2 id="notpublished">What the University does not publish</h2>
<p>These are common questions this assistant is asked and cannot answer from
published sources. It says so rather than inventing an answer.</p>
<ul>{unpublished_rows}</ul>
</div>
</main>
<footer><div class="wrap">
<p>Compiled {_esc(table.get('compiled_at', ''))} from publicly available pages.
Routing table version {_esc(table.get('routing_table_version', ''))}.
No personal data is collected by this service and no user query is stored or used
to train any model.</p>
</div></footer>
</body>
</html>"""
