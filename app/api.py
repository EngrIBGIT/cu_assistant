"""HTTP interface.

Five things this service deliberately does not do, because each one would
violate a stated privacy commitment: it does not accept credentials, set
tracking cookies, log request bodies, persist queries, or expose an endpoint
that performs an action on an external system.

The no-JavaScript route is not a courtesy. The audit found that the University's
own site serves no content to clients that do not execute JavaScript, and a
solution to that problem which itself required JavaScript would be an
indefensible answer. ``/fallback`` is fully server-rendered and is expected to
carry the entire primary journey on its own.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .pipeline import Assistant
from .schemas import AskRequest, AskResponse, RouteEntry, RoutesResponse

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Grounded AI information and routing assistant for Cosmopolitan "
        "University, Abuja. Answers only from the University's own published "
        "pages, cites every factual claim, and abstains when the corpus does "
        "not support an answer."
    ),
)

_state: dict[str, object] = {}


def get_assistant() -> Assistant:
    """Lazily build the assistant so that a failed model load is visible, not fatal."""
    if "assistant" not in _state:
        _state["assistant"] = Assistant.build()
    return _state["assistant"]  # type: ignore[return-value]


@app.on_event("startup")
def _startup() -> None:
    get_assistant()


# --------------------------------------------------------------- rate limit

#: Fixed-window limiter, in-process. Enough to contain an accidental loop or a
#: bored script against a single-instance free-tier deployment. A multi-instance
#: deployment needs a shared store, and that limitation is recorded rather than
#: hidden behind an in-memory list that looks like more than it is.
_hits: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = 30
RATE_WINDOW_S = 60.0


def _rate_limit(client: str) -> None:
    now = time.time()
    window = _hits[client]
    while window and now - window[0] > RATE_WINDOW_S:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
    window.append(now)


def _client_key(request: Request) -> str:
    # Behind a proxy the socket address is the proxy, so the forwarded header is
    # preferred. This is a rate-limiting hint, not an identity, and it is not
    # stored or correlated with anything.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------- routes


@app.get("/api/health", tags=["ops"])
def health() -> JSONResponse:
    return JSONResponse(get_assistant().health())


@app.post("/api/ask", response_model=AskResponse, tags=["assistant"])
def ask(payload: AskRequest, request: Request) -> AskResponse:
    _rate_limit(_client_key(request))

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question must not be empty.")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Question is too long (500 character limit).")

    return get_assistant().ask(question, debug=payload.debug)


@app.get("/api/routes", response_model=RoutesResponse, tags=["assistant"])
def routes() -> RoutesResponse:
    """The full routing table, published as data.

    Exposed deliberately: the widget's no-JavaScript fallback and any third party
    can render the routing table without this service being available at all.
    A directory that only exists behind a chatbot is a worse directory.
    """
    table = get_assistant()
    return RoutesResponse(
        routes=[
            RouteEntry(
                route_id=r["route_id"],
                label=r["label"],
                category=r.get("category", ""),
                entry_point=r.get("entry_point"),
                office=r.get("office"),
                email=r.get("email"),
                phone=r.get("phone"),
                address=r.get("address"),
                hours=r.get("hours"),
                verification=r.get("verification", "published"),
                source_url=r.get("source_url"),
                notes=r.get("notes"),
            )
            for r in _routing_rows()
        ],
        unpublished_topics=table.unpublished_topics,
        routing_table_version=table.routing_table_version,
        count=len(_routing_rows()),
    )


def _routing_rows() -> list[dict]:
    from .config import ROUTING_TABLE_PATH  # noqa: PLC0415
    import json  # noqa: PLC0415

    with open(ROUTING_TABLE_PATH, encoding="utf-8") as handle:
        return json.load(handle)["routes"]


@app.get("/api/routes/{route_id}", tags=["assistant"])
def route_detail(route_id: str) -> JSONResponse:
    for row in _routing_rows():
        if row["route_id"] == route_id:
            return JSONResponse(row)
    raise HTTPException(status_code=404, detail="Unknown route id.")


# -------------------------------------------------------------------- pages


@app.get("/widget.js", include_in_schema=False)
def widget_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript")


@app.get("/widget.css", include_in_schema=False)
def widget_css() -> FileResponse:
    return FileResponse(STATIC_DIR / "widget.css", media_type="text/css")


@app.get("/fallback", response_class=HTMLResponse, include_in_schema=False)
def fallback() -> HTMLResponse:
    """The complete experience with no JavaScript.

    Server-rendered, keyboard operable, and it carries the whole primary
    journey: every destination, every contact route, and every topic the
    University does not publish. A user who never executes a line of JavaScript
    loses nothing.
    """
    from .fallback import render  # noqa: PLC0415 - imported late to keep boot fast

    return HTMLResponse(render())


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    from .fallback import render  # noqa: PLC0415

    return HTMLResponse(render(demo=True))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/privacy", tags=["ops"])
def privacy() -> dict:
    """Machine-readable statement of the data this service handles.

    Published so that the privacy claim is checkable rather than merely
    asserted in a privacy policy nobody reads.
    """
    return {
        "personal_data_collected": False,
        "accounts_required": False,
        "cookies_set": False,
        "analytics": False,
        "queries_logged": False,
        "queries_used_for_training": False,
        "third_party_transmission": (
            "Only the language-model API call required to compose an answer, and "
            "only when a language model is configured. No model is configured by "
            "default, in which case no user content leaves this process."
        ),
        "write_actions": False,
        "retention": "No user content is stored. Logs contain status, latency, and error metadata only.",
        "contact_route": "info@cosmopolitan.edu.ng",
    }
