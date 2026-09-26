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
