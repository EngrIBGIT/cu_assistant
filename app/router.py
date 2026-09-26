"""Destination routing.

Routing is a hybrid score rather than a single signal:

* **Neural similarity** between the user's question and a prose profile of each
  destination. This is what carries paraphrase — "I am a working professional,
  I want to deepen my qualification" reaches the postgraduate portal without
  sharing a single content word with "postgraduate".
* **Curated keyword overlap** against phrases recorded in the routing table.
  This carries the colloquial and institutional phrasing the embedding model
  handles poorly ("leaver", "walk through", "put my name down", "the machine
  will not let me in"), and it is auditable: a non-engineer can read the keyword
  list and see exactly why a destination was offered.

Using only the neural term would make routing unexplainable. Using only keywords
would make it brittle. The evaluation harness exists to measure how much each
contributes, including against a keyword-only baseline, so the claim that both
are needed is evidenced rather than asserted.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .config import thresholds as T
from .embeddings import Embedder, tokenize
from .retriever import calibrate

#: Saturation constant for keyword accumulation, in word units.
#:
#: A matched keyword is worth one unit per word, and three units saturates the
#: term. The reasoning is evidential: a three-word exact phrase such as "forgot
#: my password" is very unlikely to coincide, so it should be close to decisive,
#: whereas a single common word like "result" is weak evidence on its own. The
#: earlier linear ramp under-weighted exactly the precise phrases that carry the
#: most information, which is backwards.
_KEYWORD_SATURATION = 3.0


@dataclass
class RouteScore:
    route: dict[str, Any]
    score: float
    neural: float
    keyword: float

    @property
    def route_id(self) -> str:
        return self.route["route_id"]


def _profile(route: dict[str, Any]) -> str:
    """Prose description of a destination, used as its embedding profile.

    Keywords are excluded on purpose. They are short fragments chosen for exact
    matching; including them would let lexical bait dominate the semantic
    signal and inflate every route they appear on.

    The one-line summary *is* included, because a route card that does not say
    what the destination is for is a worse route card, and the summary is
    written in the language a user would use to ask for it.
    """
    parts = [route.get("label", ""), route.get("summary", ""), route.get("category", "").replace("_", " ")]
    if route.get("office"):
        parts.append(f"Office: {route['office']}")
    if route.get("notes"):
        parts.append(route["notes"])
    return ". ".join(p for p in parts if p)


#: Terms that name the setting rather than the matter.
#:
#: These words are kept in the routing table, because a reader auditing the table
#: should be able to see every phrase that has ever been considered, but they are
#: excluded from scoring. A keyword earns its power from being *rare across the
#: table*, and that measure misfires here: "abuja" and "campus" appear on exactly
#: one route each, so rarity scoring treated them as near-decisive evidence. What
#: they actually establish is that the user is talking about this university at
#: some location on earth — which every question in the product already implies.
#:
#: The failure this caused was concrete. "What is the weather in Abuja tomorrow?"
#: saturated the keyword term on R-ADDRESS, the saturation floor lifted that route
#: above the routing threshold, and the assistant answered a question about the
#: weather with the University's postal address and four irrelevant citations.
#: The word "Abuja" is not evidence of which door the user needs; it is evidence
#: of where they are, which was never in doubt.
#:
#: Setting terms are the general case of a word that is true of the whole
#: institution. "University" is on exactly one route for the same reason and is
#: excluded here too.
_NON_DISCRIMINATIVE = frozenset(
    {
        "abuja",
        "campus",
        "university",
        "cosmopolitan",
        "nigeria",
        "fct",
        "main campus",
        "the university",
    }
)

#: How far apart the words of a multi-word keyword may sit and still count as a
#: match. Users insert words: "I forgot my portal password" should match the
#: curated phrase "forgot my password". Zero slack would demand exact adjacency
#: and fail on perfectly natural phrasing.
_PROXIMITY_SLACK = 3


def phrase_matches(phrase: str, tokens: list[str]) -> bool:
    """Proximity match for a multi-word keyword.

    Every word of the phrase must be present, and they must fall inside a window
    only slightly wider than the phrase itself. This tolerates the insertion
    above without accepting a long document that happens to contain every word
    somewhere.

    Public rather than private because the abstention gate in the pipeline
    matches its own curated terms and must tolerate insertions the same way, or
    a question can fail to abstain while a near-identical one abstains.
    """
    parts = [p for p in phrase.split() if p]
    if not parts:
        return False

    positions = [i for i, token in enumerate(tokens) if token in parts]
    # Distinct *words* matched, not distinct positions. Counting positions let a
    # repeated token satisfy two slots at once, which is how "who is the" came
    # to match "What is the link to the e-learning platform" — the single "is"
    # plus the two "the"s satisfied all three words — and how "how do i pay"
    # matched a sentence with no "pay" in it at all. Both errors landed in the
    # abstention layer, where a false positive refuses an answerable question
    # and a false negative invents an answer to an unanswerable one.
    if len({tokens[i] for i in positions}) < len(set(parts)):
        return False
    return (max(positions) - min(positions)) <= len(parts) - 1 + _PROXIMITY_SLACK


def _scoring_keywords(route: dict[str, Any]) -> list[str]:
    """The route's keywords that carry routing signal, normalised and deduplicated.

    Filtering happens here rather than at each call site so that the rarity
    weighting and the match test always see exactly the same term set. If they
    disagreed, a filtered term would still be counted in the document-frequency
    table and would depress the weight of the terms that remain.
    """
    seen: dict[str, None] = {}
    for keyword in route.get("keywords", []):
        phrase = keyword.lower().strip()
        if not phrase or phrase in _NON_DISCRIMINATIVE:
            continue
        seen.setdefault(phrase, None)
    return list(seen)


def _keyword_factors(routes: Sequence[dict[str, Any]]) -> dict[str, float]:
    """How much each keyword is worth, by how many destinations claim it.

    A keyword every route claims is nearly evidence of nothing: "student" or
    "university" appears across the table, so matching one tells us only that the
    user is talking to a university. A keyword exactly one route claims is
    evidence on its own, and that route should win on it.

    Weighting by rarity rather than length also removes an accident of the
    vocabulary. Saturation used to be reachable only by a three-word phrase, so
    the single word "hostel" earned a third of a unit however clearly it
    pointed at the accommodation route, and a question about hostel rooms lost
    to whichever route happened to score better on "fee". A rare word now earns
    the same decisive credit a long phrase does.
    """
    frequency: Counter[str] = Counter()
    for route in routes:
        for keyword in _scoring_keywords(route):
            frequency[keyword] += 1

    total = len(routes)
    if total <= 1:
        return {k: _KEYWORD_SATURATION for k in frequency}

    factors: dict[str, float] = {}
    for keyword, df in frequency.items():
        if df == 1:
            factors[keyword] = _KEYWORD_SATURATION
        else:
            spread = (df - 1) / (total - 1)
            factors[keyword] = 1.0 + (_KEYWORD_SATURATION - 1.0) * (1.0 - spread)
    return factors


def _keyword_score(
    question: str, route: dict[str, Any], factors: dict[str, float]
) -> float:
    """Accumulated, saturated keyword overlap for one route."""
    lowered = f" {question.lower()} "
    tokens = tokenize(question)
    matched = 0.0

    for phrase in _scoring_keywords(route):
        words = len(phrase.split())
        # Word boundaries for short keys, proximity matching for phrases. A bare
        # substring test would match "ug" inside "drug" and "art" inside
        # "article", which is precisely the kind of quiet nonsense that makes a
        # routing table untrustworthy.
        if " " in phrase:
            if phrase_matches(phrase, tokens):
                matched += min(_KEYWORD_SATURATION, words * factors.get(phrase, 1.0))
        elif re.search(rf"\b{re.escape(phrase)}\b", lowered):
            matched += factors.get(phrase, 1.0)

    return min(1.0, matched / _KEYWORD_SATURATION)


class Router:
    """Scores every destination in the routing table against a question."""

    def __init__(self, routes: Sequence[dict[str, Any]], embedder: Embedder) -> None:
        self.routes = list(routes)
        self.embedder = embedder
        self._profiles = self.embedder.encode([_profile(r) for r in self.routes])
        self._by_id = {r["route_id"]: r for r in self.routes}
        self._factors = _keyword_factors(self.routes)

    def rank(self, question: str) -> list[RouteScore]:
        """Return all routes scored, best first."""
        if not self.routes:
            return []

        question_vector = self.embedder.encode([question])[0]
        neural = np.clip(calibrate(self._profiles @ question_vector), 0.0, 1.0)

        scored: list[RouteScore] = []
        for index, route in enumerate(self.routes):
            keyword = _keyword_score(question, route, self._factors)
            score = T.weight_neural * float(neural[index]) + T.weight_keyword * keyword

            # A saturated keyword match is strong, positive evidence that this is
            # the destination the user means: it means a curated phrase for this
            # specific route matched, which no amount of generic topical
            # similarity should be allowed to outvote. The floor is applied only
            # at saturation, so a single incidental word never earns it.
            if keyword >= 1.0:
                score = max(score, T.min_route + 0.04)

            scored.append(
                RouteScore(
                    route=route,
                    score=round(score, 6),
                    neural=round(float(neural[index]), 6),
                    keyword=round(keyword, 6),
                )
            )

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def best(self, question: str) -> RouteScore | None:
        ranked = self.rank(question)
        return ranked[0] if ranked else None

    def confident(self, question: str) -> RouteScore | None:
        """Best route, but only if it clears the routing threshold."""
        top = self.best(question)
        if top is None or top.score < T.min_route:
            return None
        return top

    def alternatives(self, question: str, exclude: str, limit: int = 2) -> list[RouteScore]:
        """Secondary routes that clear the lower threshold."""
        return [
            s
            for s in self.rank(question)
            if s.route_id != exclude and s.score >= T.min_route_secondary
        ][:limit]

    def get(self, route_id: str) -> dict[str, Any] | None:
        return self._by_id.get(route_id)

    def __contains__(self, route_id: object) -> bool:
        return route_id in self._by_id

    def __len__(self) -> int:
        return len(self.routes)


def question_tokens(question: str) -> set[str]:
    return set(tokenize(question))
