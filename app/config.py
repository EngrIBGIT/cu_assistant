"""Runtime configuration.

Every value can be overridden by an environment variable so that the same build
runs in development, in CI, and in the free-tier deployment without edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CRA_DATA_DIR", BASE_DIR / "data"))
CORPUS_DIR = DATA_DIR / "corpus"
ROUTING_TABLE_PATH = DATA_DIR / "routing_table.json"
EVAL_DIR = Path(os.getenv("CRA_EVAL_DIR", BASE_DIR / "eval"))
VAR_DIR = Path(os.getenv("CRA_VAR_DIR", BASE_DIR / "var"))

EMBEDDING_MODEL = os.getenv("CRA_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

#: "auto" tries the neural embedder and silently falls back to the lexical one.
#: "neural" requires the neural embedder and raises if unavailable. "lexical"
#: forces the dependency-free path, which is what CI uses.
EMBEDDER = os.getenv("CRA_EMBEDDER", "auto")

#: Optional language model. Absent by default: the product must work without it.
LLM_PROVIDER = os.getenv("CRA_LLM_PROVIDER", "")  # "", "openai", "ollama"
LLM_MODEL = os.getenv("CRA_LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("CRA_LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("CRA_LLM_API_KEY", "")
LLM_TIMEOUT_S = float(os.getenv("CRA_LLM_TIMEOUT_S", "12"))
LLM_MAX_TOKENS = int(os.getenv("CRA_LLM_MAX_TOKENS", "320"))

# ---------------------------------------------------------------- deployment
#
# The service is one ASGI application, but it is deployed as two processes: a
# JSON API and a server-rendered frontend. They are the same code with different
# routes mounted, which is why a single-process deployment is still the default
# and the split costs nothing when it is not wanted.
#
# The split exists because the two have genuinely different exposure. The API
# takes untrusted text and returns JSON; the frontend serves the pages that
# carry the security headers a browser actually reads, and it must reach the API
# without a page reload to do it. Splitting them means the surface that renders
# to a user can be hardened, rate-limited and put behind a cache without touching
# the surface that is being called.

#: Port the JSON API listens on.
API_PORT = int(os.getenv("CRA_API_PORT", "8003"))

#: Port the server-rendered frontend listens on.
WEB_PORT = int(os.getenv("CRA_WEB_PORT", "5020"))

#: Host both processes bind to. 127.0.0.1 rather than 0.0.0.0: the pair is a local
#: or reverse-proxied deployment, and binding every interface by default would
#: expose a read-only service that nobody asked to expose.
BIND_HOST = os.getenv("CRA_BIND_HOST", "127.0.0.1")

#: The URL a *browser* uses to reach the API. Different from the bind host when
#: the API sits behind a proxy, a different hostname, or TLS termination, and the
#: widget cannot be told the difference: it only knows the URL it was given.
API_PUBLIC_URL = os.getenv("CRA_API_PUBLIC_URL", f"http://127.0.0.1:{API_PORT}").rstrip("/")

#: The URL a *browser* uses to reach the frontend. Advertised in /api/health so
#: that a running pair can be confirmed from either side.
WEB_PUBLIC_URL = os.getenv("CRA_WEB_PUBLIC_URL", f"http://{BIND_HOST}:{WEB_PORT}").rstrip("/")

#: Origins the API accepts browser requests from. The frontend is a separate
#: origin from the API, so without this the widget is blocked by the same-origin
#: policy the moment the pair is split. Scoped to an explicit list rather than
#: "*": the API has no authentication, so an open CORS policy would let any site
#: on the internet use this deployment as an oracle.
ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "CRA_ALLOWED_ORIGINS",
        f"{WEB_PUBLIC_URL},http://localhost:{WEB_PORT},http://127.0.0.1:{WEB_PORT}",
    ).split(",")
    if origin.strip()
)

#: How long the frontend waits for the API before giving up. Short on purpose: a
#: user waiting on a page render should be told the service is unavailable
#: quickly, and the frontend never invents an answer to fill the gap.
API_TIMEOUT_S = float(os.getenv("CRA_API_TIMEOUT_S", "20"))


@dataclass(frozen=True)
class Thresholds:
    """Decision thresholds for abstention and routing.

    These are the numbers that implement commitment 1 and 3 above. They are
    frozen in code rather than tuned per query so that behaviour is auditable:
    the AI Engineer owns changing them, and any change is a version bump.
    """

    #: Minimum retrieval score for a claim to be considered supported by the
    #: corpus. Below this the system abstains on the factual claim.
    min_evidence: float = 0.34

    #: Minimum score for a route to be offered as the primary destination.
    min_route: float = 0.26

    #: Minimum score for a route to appear in the "also consider" list.
    min_route_secondary: float = 0.20

    #: How many retrieved chunks are placed in the generation context.
    top_k: int = 5

    #: Weight of the neural similarity term in the routing score.
    weight_neural: float = 0.62

    #: Weight of the curated keyword term in the routing score.
    weight_keyword: float = 0.38

    #: Scores below this are treated as no match at all rather than a weak match.
    noise_floor: float = 0.05


@dataclass(frozen=True)
class Settings:
    thresholds: Thresholds = field(default_factory=Thresholds)
    app_name: str = "CU Route Assistant"
    institution: str = "Cosmopolitan University, Abuja"
    version: str = "1.0.0"

    #: Shown to users on every answer. The single most important safety control.
    disclaimer: str = (
        "Check time-sensitive facts such as fees and deadlines directly with the "
        "University before you rely on them."
    )


settings = Settings()
thresholds = settings.thresholds
