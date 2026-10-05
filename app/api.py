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

Two processes, one codebase
---------------------------
The service is deployed as a JSON API and a server-rendered frontend, on
separate ports, because the two have different exposure. The API takes untrusted
text and returns JSON; the frontend serves the pages a browser reads and is the
surface that must carry the security headers. Splitting them lets the page
surface be hardened, cached and rate-limited without touching the surface being
called.

The split is expressed as three apps built from the same routes:

* ``backend_app``  - the JSON API only. Owns the assistant, the corpus, and the
  model. This is the only process that loads the embedder.
* ``frontend_app`` - the pages and the widget only. Never imports the pipeline,
  so it never loads the model, and it reaches the API over HTTP.
* ``app``          - both, in one process. The default, and what the test suite
  exercises, because a deployment that does not need the split should not pay
  for it.

One decision path is preserved across all three. ``Assistant.ask`` runs in
exactly one place, on the API; the frontend asks that place over HTTP rather than
reimplementing or approximating it, which is why a screen-reader user, the chat
widget and the evaluation harness all still get the same answer.
"""

from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ALLOWED_ORIGINS,
    API_PORT,
    API_PUBLIC_URL,
    API_TIMEOUT_S,
    WEB_PORT,
    WEB_PUBLIC_URL,
    settings,
)
from .pipeline import Assistant
from .schemas import AskRequest, AskResponse, RouteEntry, RoutesResponse

STATIC_DIR = Path(__file__).parent / "static"

_state: dict[str, object] = {}


def get_assistant() -> Assistant:
    """Lazily build the assistant so that a failed model load is visible, not fatal."""
    if "assistant" not in _state:
        _state["assistant"] = Assistant.build()
    return _state["assistant"]  # type: ignore[return-value]


# --------------------------------------------------------------- rate limit

#: Fixed-window limiter, in-process. Enough to contain an accidental loop or a
#: bored script against a single-instance free-tier deployment. A multi-instance
#: deployment needs a shared store, and that limitation is recorded rather than
#: hidden behind an in-memory list that looks like more than it is.
_hits: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = 30
RATE_WINDOW_S = 60.0

#: Peers whose ``X-Forwarded-For`` is believed. Loopback only: the frontend runs
#: on this host and forwards the address of the human using it, and a header sent
#: by anyone further away would let a caller choose their own rate-limit bucket.
_TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost", "testclient"}


def _client_key(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _rate_limit(client: str) -> None:
    now = time.time()
    window = _hits[client]
    while window and now - window[0] > RATE_WINDOW_S:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
    window.append(now)


# ----------------------------------------------------------- security headers

#: Sent on every response.
#:
#: The project plan lists "TLS in transit, security headers, and no secrets in
#: client-side code" as a safety control. TLS and the secret check are properties
#: of the deployment and the assets; the headers are the one part that is code,
#: and they were asserted in the plan while being absent from the application.
#: A control that is claimed in a document and missing from the product is worse
#: than one that is never claimed, so they are set here where they are testable.
#:
#: ``style-src 'unsafe-inline'`` is required and is a real, accepted relaxation:
#: ``/fallback`` carries its stylesheet in a ``<style>`` block precisely so that
#: the no-JavaScript page needs no second request. ``script-src`` is not relaxed,
#: because the widget builds its DOM with ``createElement`` and ``textContent``
#: and uses no inline handler or ``eval`` — which is a property worth preserving
#: rather than a constraint worth escaping. The API base URL reaches the browser
#: through ``/widget-config.js`` rather than an inline script for the same
#: reason: a file is same-origin, an inline assignment is not.
#:
#: HSTS is included for the deployed HTTPS origin. A browser receiving it over
#: plain HTTP ignores it, so sending it from a local development server is inert
#: rather than harmful.
_CSP = (
    "default-src 'self'; "
    "script-src {script_src}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src {connect_src}; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

#: FastAPI's interactive documentation loads Swagger UI from a CDN, so the strict
#: policy above renders it as an unstyled empty page. Rather than weaken the
#: policy everywhere or vendor a third-party bundle into the repository, the one
#: origin is allowed on the two documentation routes and nowhere else.
_DOCS_CDN = "https://cdn.jsdelivr.net"
_DOCS_PATHS = ("/docs", "/redoc")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _content_security_policy(path: str, connect_origins: tuple[str, ...] = ()) -> str:
    script_src = "'self'"
    if path.rstrip("/") in _DOCS_PATHS:
        script_src = f"'self' {_DOCS_CDN}"
    connect = " ".join(["'self'", *connect_origins])
    return _CSP.format(script_src=script_src, connect_src=connect)


# ------------------------------------------------------- validation responses


def _scrub(errors: list[dict]) -> list[dict]:
    """Validation errors without the submitted value attached.

    Pydantic includes the offending input in every error it raises, so a rejected
    question came back inside the 422 body in full. For a 20 KB over-length
    submission that made the error response larger than any answer the service
    gives, and it contradicted ``/api/privacy``'s claim that queries are not
    retained: the value was not stored, but it was being handed back to whatever
    intermediary logged the response.

    The field name, the location, and the reason are kept — they are what makes
    the error actionable. The value is the part the caller already has.
    """
    cleaned: list[dict] = []
    for error in errors:
        item = {k: v for k, v in error.items() if k not in ("input", "ctx", "url")}
        item["loc"] = [str(part) for part in error.get("loc", ())]
        cleaned.append(item)
    return cleaned


# --------------------------------------------------------------- API client


def ask_via_api(
    question: str,
    *,
    api_base_url: str = API_PUBLIC_URL,
    client_ip: str | None = None,
    timeout: float = API_TIMEOUT_S,
) -> AskResponse:
    """Ask the API over HTTP, on behalf of a page render.

    The frontend has no pipeline of its own, so this is how a server-rendered
    answer happens. Two details matter:

    * The original visitor's address is forwarded, so the API rate-limits per
      person. Without it every request through the frontend would land in one
      loopback bucket and the limiter would measure the frontend, not its users.
    * A failure raises. It is not caught here and turned into an empty answer,
      because a page that cannot reach the API must say so rather than render a
      page that looks like the service declined to answer a question it was
      never asked.
    """
    headers = {"Content-Type": "application/json"}
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    request = urllib.request.Request(
        f"{api_base_url.rstrip('/')}/api/ask",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return AskResponse.model_validate(json.loads(response.read()))
    except urllib.error.HTTPError as exc:  # 4xx/5xx from the API
        raise HTTPException(status_code=exc.code, detail="The assistant could not answer.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="The assistant is not available.") from exc


# ------------------------------------------------------------- route tables


def _routing_rows() -> list[dict]:
    from .config import ROUTING_TABLE_PATH  # noqa: PLC0415

    with open(ROUTING_TABLE_PATH, encoding="utf-8") as handle:
        return json.load(handle)["routes"]


# ------------------------------------------------------------------ the apps


def create_app(
    *,
    mount_api: bool = True,
    mount_pages: bool = True,
    ask_locally: bool = True,
    api_base_url: str = API_PUBLIC_URL,
    cors_origins: tuple[str, ...] = (),
) -> FastAPI:
    """Build one of the three deployment shapes.

    :param mount_api: register the ``/api/*`` surface. This is the only process
        that builds an assistant when it is mounted.
    :param mount_pages: register the server-rendered pages and the widget.
    :param ask_locally: answer page questions by calling the in-process
        assistant. False means page questions go to ``api_base_url`` instead,
        which is what the split frontend does.
    :param cors_origins: origins to accept browser requests from. Set on the API,
        because the split makes the frontend a different origin from it.
    """
    # Swagger UI is the documentation of record, and it is the one page on this
    # service that is not self-contained: it loads swagger-ui from jsDelivr. That
    # is why /docs gets a CSP exception below. It is enabled on the API and
    # disabled on the frontend, because the frontend's own schema is empty — a
    # Swagger page listing no endpoints is a worse advertisement than a 404, and
    # it invites the reader to call an API that is on the other port.
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Grounded AI information and routing assistant for Cosmopolitan "
            "University, Abuja. Answers only from the University's own published "
            "pages, cites every factual claim, and abstains when the corpus does "
            "not support an answer."
        ),
        docs_url="/docs" if mount_api else None,
        redoc_url="/redoc" if mount_api else None,
        openapi_url="/openapi.json" if mount_api else None,
    )

    # The frontend is only a different origin from the API when the two are
    # separate processes, so the allowlist is populated on the API alone and the
    # combined app needs no CORS at all.
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            max_age=600,
        )

    connect_origins: tuple[str, ...] = ()
    if not ask_locally:
        connect_origins = (_origin_of(api_base_url),)

    @application.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            _content_security_policy(request.url.path, connect_origins),
        )
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @application.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _scrub(exc.errors())})

    if mount_api:
        # ``split`` is False only when both surfaces are mounted on one process, so
        # /api/health reports the truth about which of the two is running.
        _mount_api(application, split=not mount_pages)
    if mount_pages:
        _mount_pages(application, ask_locally=ask_locally, api_base_url=api_base_url)

    return application


def _origin_of(url: str) -> str:
    """The scheme://host:port of a URL, which is what a CSP origin is."""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]


def _mount_api_root(application: FastAPI) -> None:
    """A plain notice on the API port, in place of a bare 404.

    It used to be ``{"detail":"Not Found"}``, which is the first thing anyone
    opening the API port sees. That is not wrong, exactly, but it reads as a
    broken service rather than as an API with no pages, and the split makes it
    the more likely first impression: the ports are two, and nothing said which
    one to open first.
    """

    @application.get("/", include_in_schema=False)
    def root() -> HTMLResponse:
        return HTMLResponse(
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(settings.app_name)} API</title>"
            "<style>body{font:16px/1.65 -apple-system,BlinkMacSystemFont,"
            '"Segoe UI",Roboto,sans-serif;max-width:44rem;margin:0 auto;'
            "padding:32px 16px;color:#16202b}a{color:#0b5d3b}"
            "h1{font-size:1.35rem;margin:0 0 6px}"
            "p.tagline{color:#5a6b7b;margin:0 0 24px}"
            "ul{padding-left:20px}li{margin:.35em 0}"
            "code{background:#eef2f5;padding:1px 5px;border-radius:3px}"
            ".box{background:#f6f8fa;border:1px solid #d5dee6;border-radius:10px;"
            "padding:16px;margin:0 0 20px}"
            "@media(prefers-color-scheme:dark){body{background:#0f151b;color:#e8eef3}"
            "p.tagline{color:#9fb0bf}a{color:#7fd1a8}"
            ".box{background:#141c24;border-color:#2c3a47}"
            "code{background:#1e2a35}}</style></head><body>"
            f"<h1>{html.escape(settings.app_name)} API</h1>"
            '<p class="tagline">This port is the API only. The pages are served '
            "by the frontend.</p>"
            '<div class="box"><ul>'
            '<li><a href="/docs">API documentation</a> &mdash; every endpoint, '
            "with examples</li>"
            '<li><a href="/api/health">Service health</a> &mdash; which embedder '
            "and model are actually running, and which ports this pair expects</li>"
            '<li><a href="/api/routes">Routing table</a> &mdash; all 29 '
            "destinations as JSON, no assistant needed</li>"
            f'<li><a href="{html.escape(WEB_PUBLIC_URL)}">Open the site</a> '
            "&mdash; the contact directory and chat assistant</li>"
            "</ul></div>"
            "<p>Ask a question with "
            '<code>POST /api/ask</code> and a JSON body of '
            '<code>{"question": "..."}</code>.</p>'
            "</body></html>"
        )


def _mount_api(application: FastAPI, *, split: bool) -> None:
    """The JSON surface. The only place an assistant is ever built."""

    @application.on_event("startup")
    def _startup() -> None:
        get_assistant()

    if split:
        # Only when this process is not also serving the pages. Both mounts claim
        # "/", and FastAPI resolves to whichever is registered first, so adding
        # this unconditionally meant the combined single-process app served this
        # API notice in place of the actual contact directory. Guarded by the
        # same flag that makes /api/health honest about the deployment shape.
        _mount_api_root(application)

    @application.get("/api/health", tags=["ops"])
    def health() -> JSONResponse:
        body = dict(get_assistant().health())
        # Published so that a running pair can be confirmed from either side, and
        # so a deployment that came up on the wrong port is obvious immediately.
        # ``split`` is passed in rather than hardcoded: the combined single-process
        # app mounts the same routes, and reporting "split: true" there would be a
        # false statement in the one place an operator goes to check whether what
        # they are running is what they think they are running.
        body["deployment"] = {
            "api_port": API_PORT,
            "web_port": WEB_PORT,
            "api_url": API_PUBLIC_URL,
            "web_url": WEB_PUBLIC_URL,
            "split": split,
        }
        return JSONResponse(body)

    @application.post("/api/ask", response_model=AskResponse, tags=["assistant"])
    def ask(payload: AskRequest, request: Request) -> AskResponse:
        _rate_limit(_client_key(request))

        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=422, detail="Question must not be empty.")
        if len(question) > 500:
            raise HTTPException(
                status_code=422, detail="Question is too long (500 character limit)."
            )

        return get_assistant().ask(question, debug=payload.debug)

    @application.get("/api/routes", response_model=RoutesResponse, tags=["assistant"])
    def routes() -> RoutesResponse:
        """The full routing table, published as data.

        Exposed deliberately: the widget's no-JavaScript fallback and any third
        party can render the routing table without this service being available at
        all. A directory that only exists behind a chatbot is a worse directory.
        """
        table = get_assistant()
        rows = _routing_rows()
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
                for r in rows
            ],
            unpublished_topics=table.unpublished_topics,
            routing_table_version=table.routing_table_version,
            count=len(rows),
        )

    @application.get("/api/routes/{route_id}", tags=["assistant"])
    def route_detail(route_id: str) -> JSONResponse:
        for row in _routing_rows():
            if row["route_id"] == route_id:
                return JSONResponse(row)
        raise HTTPException(status_code=404, detail="Unknown route id.")

    @application.get("/api/privacy", tags=["ops"])
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


def _mount_pages(application: FastAPI, *, ask_locally: bool, api_base_url: str) -> None:
    """The pages a browser reads. No pipeline, no corpus, no model."""

    def answer_for(request: Request, question: str):
        """One question, one path, whichever deployment shape this is."""
        if ask_locally:
            return get_assistant().ask(question)
        client = request.client.host if request.client else None
        return ask_via_api(question, api_base_url=api_base_url, client_ip=client)

    def _page(request: Request, question: str, demo: bool) -> HTMLResponse:
        from .fallback import render  # noqa: PLC0415 - imported late to keep boot fast

        query = (question or "").strip()
        result = None
        api_error = ""
        if query:
            try:
                _rate_limit(_client_key(request))
            except HTTPException:
                query, result = "", None
            else:
                if len(query) > 500:
                    query = query[:500]
                try:
                    result = answer_for(request, query)
                except HTTPException as exc:
                    # The question stays in the box so the user can resubmit, and
                    # the failure is stated. Silently rendering an empty answer
                    # would be indistinguishable from the service declining to
                    # answer, which is a different and much more serious thing.
                    api_error = str(exc.detail)
                    result = None
        return HTMLResponse(
            render(
                demo=demo,
                question=query,
                result=result,
                api_error=api_error,
                api_url="" if ask_locally else api_base_url,
            )
        )

    @application.get("/widget.js", include_in_schema=False)
    def widget_js() -> FileResponse:
        return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript")

    @application.get("/widget.css", include_in_schema=False)
    def widget_css() -> FileResponse:
        return FileResponse(STATIC_DIR / "widget.css", media_type="text/css")

    @application.get("/widget-config.js", include_in_schema=False)
    def widget_config() -> FileResponse:
        """Tell the widget where the API is, as a file rather than an inline script.

        The widget already reads ``window.CU_ROUTE_API``. Serving the value from
        its own origin keeps ``script-src 'self'`` intact, which an inline
        assignment would not. Generated rather than static because the API URL is
        configuration, not a constant.
        """
        from fastapi.responses import Response  # noqa: PLC0415

        body = (
            "/* Generated by the CU Route Assistant frontend. */\n"
            f"window.CU_ROUTE_API = {json.dumps(api_base_url.rstrip('/'))};\n"
        )
        return Response(
            body,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/fallback", response_class=HTMLResponse, include_in_schema=False)
    def fallback(request: Request, question: str = "") -> HTMLResponse:
        """The complete experience with no JavaScript.

        Server-rendered, keyboard operable, and it carries the whole primary
        journey: every destination, every contact route, every topic the
        University does not publish, and a question box that is answered on the
        server. A user who never executes a line of JavaScript loses nothing.

        ``question`` is the no-JavaScript equivalent of ``POST /api/ask``, and it
        resolves to the same ``Assistant.ask`` call, so the answer a screen-reader
        user gets is the answer the evaluation measured.

        The directory itself is rendered from the committed routing table, so it
        still renders when the API is unreachable. Only the question needs the
        API, and its absence is reported rather than hidden.
        """
        return _page(request, question, demo=False)

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request, question: str = "") -> HTMLResponse:
        """The assistant, with the widget.

        The page is fully usable with scripting off — the question box and the
        whole directory are server-rendered — and the widget is an addition to
        it. It is loaded from two same-origin files so that no inline script is
        needed to configure where the API lives.
        """
        return _page(request, question, demo=True)

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


#: Both surfaces in one process: the default, and what the test suite exercises.
app = create_app()

#: The JSON API alone, on ``CRA_API_PORT`` (8003). The only process that loads
#: the embedder.
backend_app = create_app(
    mount_api=True,
    mount_pages=False,
    cors_origins=ALLOWED_ORIGINS,
)

#: The pages and the widget alone, on ``CRA_WEB_PORT`` (5020). Answers page
#: questions by calling :data:`backend_app` over HTTP.
frontend_app = create_app(
    mount_api=False,
    mount_pages=True,
    ask_locally=False,
)
