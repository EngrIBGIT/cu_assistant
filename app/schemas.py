"""Wire-format models for the API.

The response shape is the product's contract with the widget and with the
no-JavaScript fallback. It is deliberately explicit about provenance: a client
can always see which sources were used, whether the system abstained, and why.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["answer", "route", "refuse", "unsafe", "conversational"]
Grounding = Literal["grounded", "partial", "abstained"]


class Citation(BaseModel):
    """A single retrievable source backing part of an answer."""

    source_url: str
    source_title: str = ""
    retrieved_at: str | None = None
    verification: str | None = None
    #: Short quoted span from the source, so a user can verify without opening it.
    snippet: str = ""
    #: How the citation was established: a retrieved corpus passage, or the
    #: routing table's own provenance record for a destination that has no
    #: ingested page. A user, or a reviewer of the evidence, needs to be able to
    #: tell which of the two they are looking at.
    evidence: str = "retrieved_passage"


class Route(BaseModel):
    """A destination the user can be routed to."""

    route_id: str
    label: str
    #: One line saying what the user will find there, in the user's own register.
    #: A card that gives a name and a phone number but no indication of purpose
    #: forces the user to guess whether they are in the right place.
    summary: str | None = None
    entry_point: str | None = None
    office: str | None = None
    email: str | None = None
    phone: str | None = None
    alternative_phone: str | None = None
    whatsapp: str | None = None
    hours: str | None = None
    address: str | None = None
    category: str | None = None
    verification: str = "published"
    source_url: str | None = None
    notes: str | None = None
    score: float = 0.0


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    #: Optional. Enables the opt-in diagnostic mode where a query may be
    #: inspected server-side to debug a fault. Off by default, and the logs are
    #: still redacted of anything resembling personal data.
    debug: bool = False


class AskResponse(BaseModel):
    """The full answer envelope."""

    question: str
    answer: str
    intent: Intent
    grounding: Grounding
    #: True when the system declined to state a fact rather than guess.
    abstained: bool
    abstention_reason: str | None = None
    primary_route: Route | None = None
    also_consider: list[Route] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    #: Machine-readable explanation of the decision, for the QA evidence pack.
    trace: dict = Field(default_factory=dict)
    disclaimer: str
    mode: Literal["full", "retrieval_only", "lexical_fallback"] = "full"
    version: str


class RouteEntry(BaseModel):
    """One row of the routing table, as published to clients."""

    route_id: str
    label: str
    category: str
    entry_point: str | None
    office: str | None
    email: str | None
    phone: str | None
    address: str | None
    hours: str | None
    verification: str
    source_url: str | None
    notes: str | None


class RoutesResponse(BaseModel):
    routes: list[RouteEntry]
    unpublished_topics: list[dict]
    routing_table_version: str
    count: int
