"""The assistant pipeline.

One method, ``ask``, that every entry point calls: the JSON API, the widget,
the no-JavaScript fallback, and the evaluation harness. There is exactly one
decision path, so what the evaluation measures is what a user gets.

Order of operations, and why this order:

1.  **Safety screen.** Before retrieval. A harmful or integrity-violating
    request must never become context that something paraphrases.
2.  **Unpublished-topic check.** Before composition. If the user is asking for
    something the University does not publish, the correct answer is a
    templated "that is not published, here is who to ask", produced without
    echoing the question's own vocabulary back at them and without any chance
    of a retrieved sentence drifting into a claim.
3.  **Route.** Hybrid neural plus keyword scoring, with a threshold below which
    no destination is offered at all.
4.  **Retrieve.** Exact vector search over the corpus.
5.  **Compose.** Extractive by default; model-written only if a model is
    configured *and* its output passes verification.
6.  **Abstain if unsupported.** Applied last, and it overrides everything
    above: if nothing clears the evidence threshold, the system says it does not
    know rather than dressing a weak match up as an answer.

Two gates sit outside that order, because they change what the rest of the
pipeline is even allowed to consider: the safety screen at step 1 refuses a
harmful request before retrieval can launder it, and the domain-scope check at
step 4.5 declines a question that is not about this institution at all. The scope
check runs *after* routing and retrieval on purpose — it is a backstop for
"nothing institutional was found here", not a filter that pre-empts the corpus.
A question that mentions the weather and also asks about registration is an
answerable question.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .composer import Draft, ExtractiveComposer, LLMComposer
from .config import EMBEDDER, settings, thresholds as T
from .embeddings import tokenize
from .ingest import Corpus, load_corpus, load_routing_table
from .llm import LLMClient
from .orientation import conversational_reply
from .retriever import Retriever
from .router import Router, phrase_matches as _phrase_matches
from .safety import screen
from .schemas import AskResponse, Citation, Intent, Route
from .scope import screen_scope

_LABELS = re.compile(r"\s+", re.MULTILINE)

#: Shown when a destination was found but the corpus does not answer the question.
#:
#: Public because it is fixed system text rather than evidence, and the grounding
#: test needs to tell the two apart. It asserts that every *quoted* line in an
#: answer appears verbatim in a cited document; a sentence the pipeline wrote about
#: its own behaviour is not a quotation and cannot satisfy that. Keeping the string
#: here, as a named constant, is what stops the test and the pipeline disagreeing
#: about its wording — a disagreement that would have shown up as a mysterious
#: grounding failure rather than as an out-of-date reference.
NO_PUBLISHED_ANSWER = (
    "I don't have a published answer to that specific question, so I "
    "won't invent one. But this is the right place to take it."
)


@dataclass
class Assistant:
    """Assembled, ready to serve. Constructed once per process."""

    corpus: Corpus
    retriever: Retriever
    router: Router
    extractive: ExtractiveComposer
    llm: LLMClient
    composer: Any
    unpublished_topics: list[dict[str, Any]]
    #: Routing rows indexed by the URL they were compiled from, so a
    #: destination with no ingested page can still be cited from its provenance
    #: record instead of losing its citation.
    route_by_url: dict[str, dict[str, Any]]
    #: When the routing table was compiled, used as the retrieval date for
    #: citations that come from a routing row rather than an ingested page.
    compiled_at: str | None
    embedder_note: str | None
    routing_table_version: str

    # ---------------------------------------------------------------- build

    @classmethod
    def build(cls, embedder_kind: str = EMBEDDER) -> "Assistant":
        started = time.perf_counter()

        from .embeddings import build_embedder  # noqa: PLC0415 - keeps import cost lazy

        embedder, note = build_embedder(embedder_kind)

        corpus = load_corpus()
        retriever = Retriever(corpus.chunks, embedder)

        table = load_routing_table()
        router = Router(table["routes"], embedder)

        extractive = ExtractiveComposer().fit([c.text for c in corpus.chunks])
        llm = LLMClient()
        composer = LLMComposer(llm, extractive) if llm.available else extractive

        build_ms = int((time.perf_counter() - started) * 1000)
        if note:
            print(f"[cu-route] {note} ({build_ms} ms)")

        return cls(
            corpus=corpus,
            retriever=retriever,
            router=router,
            extractive=extractive,
            llm=llm,
            composer=composer,
            unpublished_topics=table.get("unpublished_topics", []),
            embedder_note=note,
            routing_table_version=table.get("routing_table_version", "unknown"),
            route_by_url={
                row["source_url"]: row
                for row in table.get("routes", [])
                if row.get("source_url")
            },
            compiled_at=table.get("compiled_at"),
        )

    # ------------------------------------------------------------- helpers

    def _route_object(self, route: dict[str, Any], score: float) -> Route:
        return Route(
            route_id=route["route_id"],
            label=route["label"],
            summary=route.get("summary"),
            entry_point=route.get("entry_point"),
            office=route.get("office"),
            email=route.get("email"),
            phone=route.get("phone"),
            alternative_phone=route.get("alternative_phone"),
            whatsapp=route.get("whatsapp"),
            hours=route.get("hours"),
            address=route.get("address"),
            category=route.get("category"),
            verification=route.get("verification", "published"),
            source_url=route.get("source_url"),
            score=round(score, 4),
        )

    def _route_block(self, route: Route) -> str:
        """Human-readable contact block for a destination.

        Notes are deliberately excluded. They are engineering provenance written
        for the team, and several of them contain the very vocabulary — "hostel",
        for instance — that a correct refusal must not repeat back.
        """
        lines = [f"**{route.label}**"]
        if route.summary:
            lines.append(route.summary)
        if route.office:
            lines.append(f"Office: {route.office}")
        if route.entry_point and route.entry_point.startswith("http"):
            lines.append(f"Where to go: {route.entry_point}")
        if route.email:
            lines.append(f"Email: {route.email}")
        if route.phone:
            phone = f"Phone: {route.phone}"
            if route.alternative_phone and route.alternative_phone != route.phone:
                phone += f" (the University also publishes {route.alternative_phone})"
            lines.append(phone)
        if route.whatsapp:
            lines.append(f"WhatsApp: {route.whatsapp}")
        if route.hours:
            lines.append(f"Opening hours: {route.hours}")
        if route.address:
            lines.append(f"Address: {route.address}")
        if route.verification == "published_conflicting":
            lines.append(
                "Note: the University publishes more than one contact detail for "
                "this. Try the second one if the first does not answer."
            )
        return "\n".join(lines)

    def _unpublished_match(self, question: str) -> dict[str, Any] | None:
        """Detect a question about something the University does not publish.

        This is a guard against the most damaging possible failure: a confident,
        fluent, entirely invented answer to a question the user has every reason
        to expect a real answer to.
        """
        lowered = f" {_LABELS.sub(' ', question.lower())} "
        best: tuple[int, dict[str, Any]] | None = None

        for topic in self.unpublished_topics:
            hits = 0
            for term in topic.get("match_terms", []):
                if " " in term:
                    if _phrase_matches(term.lower(), tokenize(question)):
                        hits += 2
                elif re.search(rf"\b{re.escape(term)}\b", lowered):
                    hits += 1
            # The bar is per topic, and the default is a single hit. The earlier
            # uniform "at least two" rule was set to stop an incidental common
            # word causing a refusal, but word-boundary matching already does
            # that job, and the uniform rule was letting real gaps through: a
            # user asking about "accommodation" or "population" got a confident
            # answer built from a page that never mentions either. A topic can
            # raise its own bar where its vocabulary really is ambiguous.
            if hits < int(topic.get("min_hits", 1)):
                continue
            if best is None or hits > best[0]:
                best = (hits, topic)

        return best[1] if best else None

    def _route_for_topic(
        self, topic: dict[str, Any], question: str
    ) -> dict[str, Any] | None:
        """Choose among the offices a topic declares as its owners.

        The first declared destination that the router considers plausible at
        all wins. Falling back to the first declared destination when none does
        means the user is still shown an office that owns the question, which is
        the one thing a refusal must never fail to do.
        """
        ranked = {s.route["route_id"]: s for s in self.router.rank(question)}
        hints = list(topic.get("route_hints") or [])
        single = topic.get("route_hint")
        if single and single not in hints:
            hints.append(single)

        for route_id in hints:
            if route_id not in self.router:
                continue
            score = ranked.get(route_id)
            if score is not None and score.score >= T.min_route_secondary:
                return score.route
        for route_id in hints:
            row = self.router.get(route_id)
            if row:
                return row
        return None

    def _citations(self, urls: list[str]) -> list[Citation]:
        """Build the citation list, always including the route's own source.

        A route card is a factual claim — an address, a phone number, a claim
        about which office handles what — and it is every bit as citeable as a
        retrieved sentence. Leaving it uncited would mean the most load-bearing
        part of the answer had no source, which is the opposite of the point.
        """
        ordered: list[str] = []
        for url in urls:
            if url and url not in ordered:
                ordered.append(url)

        out: list[Citation] = []
        for url in ordered:
            document = self.corpus.by_url.get(url)
            if document is not None:
                snippet = ""
                for chunk in self.corpus.chunks:
                    if chunk.source_url == url and chunk.carries_contact_detail:
                        snippet = chunk.text[:220].strip()
                        break
                out.append(
                    Citation(
                        source_url=url,
                        source_title=document.source_title,
                        retrieved_at=document.retrieved_at,
                        verification=document.verification,
                        snippet=snippet,
                    )
                )
                continue

            # No ingested page for this URL, which is the normal case for the
            # student, admissions and staff portals: they are destinations, not
            # documents, so there is nothing to chunk. The routing table still
            # records where each came from and how it was verified, and that
            # record is sufficient to cite. Dropping the citation instead would
            # mean the portal answers were the only ones in the system with no
            # source shown at all, which is precisely backwards.
            row = self.route_by_url.get(url)
            if row is None:
                continue
            out.append(
                Citation(
                    source_url=url,
                    source_title=row.get("label", url),
                    retrieved_at=self.compiled_at,
                    verification=row.get("verification", "published"),
                    snippet=(row.get("summary") or row.get("entry_point") or "")[:220],
                    evidence="routing_table_record",
                )
            )
        return out

    # ----------------------------------------------------------------- ask

    @property
    def embedder_name(self) -> str:
        """Which embedding model is actually in use.

        Reported rather than assumed, because the system falls back to a lexical
        model when the neural one cannot load and a caller recording evidence
        must not record it as though the neural model answered.
        """
        return self.retriever.embedder.name

    def ask(self, question: str, debug: bool = False) -> AskResponse:
        started = time.perf_counter()
        trace: dict[str, Any] = {"prompt": None, "embedder": self.retriever.embedder.name}
        if self.embedder_note:
            trace["embedder_note"] = self.embedder_note

        # 0. Conversational -------------------------------------------------
        # A greeting is not an unanswerable question. Left on the normal path it
        # reaches the abstain gate and is answered "the University does not
        # publish this", which is a false statement about a message that was
        # never asking for a fact. Intercepted here, and still recorded as
        # abstained with no citations, so it cannot be counted as an answer.
        chat = conversational_reply(question)
        if chat:
            body, kind = chat
            return AskResponse(
                question=question,
                answer=f"{body}\n\n_{settings.disclaimer}_",
                intent="conversational",
                grounding="abstained",
                abstained=True,
                abstention_reason=f"conversational: {kind}",
                primary_route=None,
                also_consider=[],
                citations=[],
                trace={
                    **trace,
                    "gate": "conversational",
                    "kind": kind,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
                disclaimer=settings.disclaimer,
                mode=self._mode(),
                version=settings.version,
            )

        # 1. Safety --------------------------------------------------------

        verdict = screen(question)

        if verdict.refused:
            route = None
            if verdict.redirect_route_hint:
                raw = self.router.get(verdict.redirect_route_hint)
                if raw:
                    route = self._route_object(raw, 1.0)
            body = verdict.message or "I can't help with that."
            if route:
                body = f"{body}\n\nIf it is a problem with your own account or your own device, this is who can help:\n\n{self._route_block(route)}"
            return AskResponse(
                question=question,
                answer=f"{body}\n\n_{settings.disclaimer}_",
                intent="unsafe",
                grounding="abstained",
                abstained=True,
                abstention_reason=f"safety gate: {verdict.category}",
                primary_route=route,
                also_consider=[],
                citations=[],
                trace={**trace, "gate": "safety", "latency_ms": int((time.perf_counter() - started) * 1000)},
                disclaimer=settings.disclaimer,
                mode=self._mode(),
                version=settings.version,
            )

        # 2. Unpublished topic ---------------------------------------------
        topic = self._unpublished_match(question)
        if topic:
            # A topic names the offices that own the missing fact, in order of
            # preference, and the router picks among them. Neither half is
            # sufficient alone. A single hardcoded hint is a floor, not an
            # answer: it cannot read the question, so filing "how many programmes
            # can I list" under the certificate programmes page sends an
            # applicant asking about the undergraduate portal to the wrong
            # office. But the router alone is not enough either, because an
            # unpublished question is by definition one the router has no good
            # match for, and left to itself it reaches for whichever office
            # merely sounds adjacent — which is how a scholarship enquiry ends up
            # at the fees desk. Constrained to the declared owners, it can only
            # choose between plausible destinations.
            raw = self._route_for_topic(topic, question)
            route = self._route_object(raw, 1.0) if raw else None
            # The topic's own label is deliberately not echoed back. Restating
            # "application deadlines and closing dates" at someone who asked
            # about closing dates reads as though the refusal were itself a
            # finding, and it leaks the trigger vocabulary back at a user who
            # may have used a term the University does not. The route card
            # below carries the context instead.
            body = (
                "I can't give you a reliable answer to that. Cosmopolitan "
                "University does not publish it on any page I can read, and I "
                "am not going to invent one. This is who to ask."
            )
            if route:
                body = f"{body}\n\n{self._route_block(route)}"
            citations = self._citations([route.source_url] if route and route.source_url else [])
            return AskResponse(
                question=question,
                answer=f"{body}\n\n_{settings.disclaimer}_",
                intent="refuse",
                grounding="abstained",
                abstained=True,
                abstention_reason=f"unpublished topic: {topic['topic_id']}",
                primary_route=route,
                also_consider=[],
                citations=citations,
                trace={
                    **trace,
                    "gate": "unpublished_topic",
                    "topic_id": topic["topic_id"],
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
                disclaimer=settings.disclaimer,
                mode=self._mode(),
                version=settings.version,
            )

        # 3. Route ----------------------------------------------------------
        top = self.router.best(question)
        primary = self._route_object(top.route, top.score) if top and top.score >= T.min_route else None
        alternatives = [
            self._route_object(s.route, s.score)
            for s in self.router.alternatives(question, exclude=top.route_id if top else "")
        ]

        # 4. Retrieve -------------------------------------------------------
        # Document-level context expansion: a passage that shares no topic words
        # with the question cannot match it, but it is still the right answer if
        # it sits in a document that did match.
        direct = self.retriever.search(question, top_k=T.top_k)
        hits = self.retriever.expand(direct)
        best = direct[0] if direct else None
        supported = bool(best and best.supported)
        trace["retrieval"] = {
            "top_score": round(best.score, 4) if best else 0.0,
            "top_raw_cosine": round(best.raw, 4) if best else 0.0,
            "supported": supported,
            "hit_urls": [h.chunk.source_url for h in direct[:3]],
            "context_expanded": len(hits) - len(direct),
        }
        if top:
            trace["routing"] = {
                "best": top.route_id,
                "score": round(top.score, 4),
                "neural": round(top.neural, 4),
                "keyword": round(top.keyword, 4),
            }

        # 4.5 Domain scope ---------------------------------------------------
        # Consulted only when no destination was found at all, which makes it a
        # backstop rather than a filter: a question that routes anywhere
        # institutional is never scope-declined, however little else about it
        # makes sense.
        #
        # The condition deliberately ignores whether retrieval found supporting
        # text. "What is the weather in Abuja tomorrow?" retrieves a page that
        # mentions Abuja with a perfectly respectable cosine score, because
        # similarity to a page is not the same as relevance to the question, and
        # the corpus cannot contain tomorrow's forecast however well it matches.
        # Requiring absent retrieval support let that question through to a
        # confident answer built from the University's postal address.
        scope = screen_scope(question) if primary is None else None
        if scope is not None:
            body = (
                f"{scope.message} I only know what Cosmopolitan University "
                "publishes: applications, programmes, fees enquiries, portal "
                "access, the library, and who to contact at the university."
            )
            return AskResponse(
                question=question,
                answer=f"{body}\n\n_{settings.disclaimer}_",
                intent="refuse",
                grounding="abstained",
                abstained=True,
                abstention_reason=f"outside domain: {scope.subject}",
                primary_route=None,
                also_consider=[],
                citations=[],
                trace={
                    **trace,
                    "gate": "domain_scope",
                    "subject": scope.subject,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
                disclaimer=settings.disclaimer,
                mode=self._mode(),
                version=settings.version,
            )

        # 6. Abstain --------------------------------------------------------
        if not primary and not supported:
            return AskResponse(
                question=question,
                answer=(
                    "I don't have that in Cosmopolitan University's published "
                    "information, and I would rather say so than guess. I'm built "
                    "to help with applications, programmes, fees enquiries, portal "
                    "access, the library, and who to contact at the university.\n\n"
                    f"For anything else: **info@cosmopolitan.edu.ng** or "
                    f"**+234 805 208 0828**.\n\n_{settings.disclaimer}_"
                ),
                intent="refuse",
                grounding="abstained",
                abstained=True,
                abstention_reason="no route above threshold and no corpus evidence above threshold",
                primary_route=None,
                also_consider=[],
                citations=[],
                trace={**trace, "gate": "abstain", "latency_ms": int((time.perf_counter() - started) * 1000)},
                disclaimer=settings.disclaimer,
                mode=self._mode(),
                version=settings.version,
            )

        # 5. Compose --------------------------------------------------------
        lead = self._route_block(primary) if primary else None
        draft: Draft = self.composer.compose(question, hits, lead=lead)
        citations = self._citations(draft.citations)

        if primary and not supported:
            answer = f"{NO_PUBLISHED_ANSWER}\n\n{lead}\n\n_{settings.disclaimer}_"
        else:
            answer = f"{draft.text}\n\n_{settings.disclaimer}_"

        if not citations and primary and primary.source_url:
            citations = self._citations([primary.source_url])

        # The route's own source is cited whether or not retrieval also found it.
        if primary and primary.source_url:
            citations = self._citations(
                [primary.source_url, *[c.source_url for c in citations]]
            )

        trace["verification"] = draft.verification
        trace["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if debug:
            trace["hit_count"] = len(hits)
            trace["chunk_ids"] = [h.chunk.chunk_id for h in hits]

        return AskResponse(
            question=question,
            answer=answer,
            intent="route" if primary and not supported else "answer",
            grounding="grounded" if supported else "partial",
            abstained=False,
            abstention_reason=None,
            primary_route=primary,
            also_consider=alternatives,
            citations=citations,
            trace=trace,
            disclaimer=settings.disclaimer,
            mode=draft.mode,
            version=settings.version,
        )

    def _mode(self) -> str:
        if self.llm.available:
            return "full"
        if self.retriever.embedder.name == "tfidf-lexical":
            return "lexical_fallback"
        return "retrieval_only"

    # -------------------------------------------------------------- health

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": settings.version,
            "corpus": {
                "documents": len(self.corpus.documents),
                "chunks": self.retriever.size,
                "source_urls": sorted(self.retriever.source_urls()),
            },
            "routing_table_version": self.routing_table_version,
            "routes": len(self.router),
            "unpublished_topics": len(self.unpublished_topics),
            "embedder": {
                "active": self.retriever.embedder.name,
                "requested": EMBEDDER,
                "note": self.embedder_note,
            },
            "language_model": {
                "configured": self.llm.available,
                "provider": self.llm.label,
                "degradation": None
                if self.llm.available
                else "no language model configured; serving retrieval-only answers",
            },
            "degraded": bool(self.embedder_note) or not self.llm.available,
            "privacy": {
                "personal_data_collected": False,
                "accounts": False,
                "queries_logged": False,
                "write_actions": False,
            },
        }
