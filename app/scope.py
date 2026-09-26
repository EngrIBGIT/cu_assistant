"""Domain scope.

The assistant's entire claim is that it knows one thing well: what Cosmopolitan
University publishes, and which door to knock on. A question outside that claim
is not a retrieval failure and must not be treated as one.

This module recognises the small, closed set of subjects that are wholly outside
an institutional assistant's remit — the weather, a football result, a recipe — so
that the system can decline in a sentence that is actually relevant instead of
falling through to the generic "I don't have that in the University's published
information", which is technically true and practically useless to somebody who
asked about the weather.

Two rules keep this safe, and both matter more than the coverage it provides:

1. **It is a backstop, not a filter.** The gate is consulted only when routing
   and retrieval have *already* found nothing institutional in the question. A
   question that touches a real route is answered normally even if it also
   mentions the weather, because the institutional half is the half the user
   came for. Nothing is ever blocked on the strength of a pattern match alone.

2. **Precision beats recall.** A false refusal costs a user an answer they were
   owed, which is the specific failure this project exists to fix, so the patterns
   are narrow and anchored on the *subject* of the question. "Will it rain
   tomorrow?" is declined. "Is there shelter on campus when it rains?" is not,
   and reaches the corpus, because "shelter" and "campus" are institutional
   anchors the pattern does not contain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeVerdict:
    """A question that is not about this institution at all."""

    subject: str
    message: str


#: Weather and natural conditions. The subject noun is required, because weather
#: *words* also appear in perfectly good institutional questions ("what do I wear
#: to the matriculation ceremony", "why is the field waterlogged").
_WEATHER = re.compile(
    r"\b(weather|forecast|temperature|rainfall|humidity|wind speed|"
    r"is it (going to )?rain|will it rain|umbrella)\b",
    re.IGNORECASE,
)

#: Sport results and transfer news. Scoped to results and fixtures, because the
#: University runs sport-adjacent activities and "our football team" and "who won
#: best student" are fair questions that this must not swallow.
_SPORT = re.compile(
    r"\b(match result|final score|who won the (match|game|fixture)|"
    r"league table|fixture list|transfer fee|goal tally|league standings)\b",
    re.IGNORECASE,
)

#: General current affairs. The University is an institution in a country, and a
#: user may well be asking about a national policy *as it affects admissions* —
#: so the pattern demands the subject of the question be the news itself.
_NEWS = re.compile(
    r"\b(latest news|headline|breaking news|current affairs|"
    r"election results|presidential election|in the news today)\b",
    re.IGNORECASE,
)

#: Food, cooking, and where to eat. Deliberately narrow: "where is the cafeteria"
#: is institutional and is not matched, because "cafeteria" and "on campus" are
#: absent from the pattern.
_FOOD = re.compile(
    r"\b(recipe|cook (a )?meal|what should i (cook|eat)|"
    r"best restaurant in abuja|food delivery)\b",
    re.IGNORECASE,
)

#: Entertainment.
_ENTERTAINMENT = re.compile(
    r"\b(netflix|what should i watch|who plays the|lyrics to|"
    r"celebrity|box office|episode of)\b",
    re.IGNORECASE,
)

#: Personal medical, legal, and financial advice. Included because an assistant
#: that answers these with a confident paragraph is a liability even when it is
#: read-only, and because the honest answer requires expertise this system does
#: not have. A user asking about their own situation should be told plainly that
#: this is not a service that can advise them.
#: Personal medical, legal, and financial advice. Included because an assistant
#: that answers these with a confident paragraph is a liability even when it is
#: read-only, and because the honest answer requires expertise this system does
#: not have.
#:
#: Anchored on possession throughout. "What are the symptoms of malaria?" is a
#: general question that a person may be asking on a relative's behalf, and the
#: University publishes nothing that answers it either way — so refusing it would
#: be a refusal the assistant has no standing to make, on the strength of a word
#: that merely appears in medical questions. "My symptoms", "my prescription",
#: "can I be sacked" are the user describing their own situation, which is where
#: the line sits. A false refusal is the one error this layer must not make.
_PERSONAL_ADVICE = re.compile(
    r"\b(my symptoms|diagnos(is|e) (mine|me)|should i take|my prescription|"
    r"my bp|my blood pressure|is it dangerous|legal advice|"
    r"can i be (sacked|dismissed|fired)|evict my tenant|tax advice|"
    r"invest (my|our) money)\b",
    re.IGNORECASE,
)

_SUBJECTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        _WEATHER,
        "weather",
        "I don't have live weather data, and guessing at it would be worse than "
        "useless to you.",
    ),
    (_SPORT, "sport results", "I don't follow sport results."),
    (
        _NEWS,
        "current affairs",
        "I don't read the news, and I wouldn't want to give you a half-remembered "
        "version of an event you need to get right.",
    ),
    (_FOOD, "food", "I'm not a recipe or restaurant service."),
    (
        _ENTERTAINMENT,
        "entertainment",
        "I don't have anything to say about that, and saying so is more useful "
        "than improvising.",
    ),
    (
        _PERSONAL_ADVICE,
        "personal advice",
        "This isn't a service that can advise you on your own health, legal "
        "position, or finances, and I'd be doing you a disservice if I tried.",
    ),
)


def screen_scope(question: str) -> ScopeVerdict | None:
    """Return the out-of-scope verdict for a question, or None if it is in scope.

    Deliberately *not* a refusal in the safety sense: nothing unsafe was asked,
    so the caller should decline, explain what the assistant is for, and leave
    the user with a route rather than a blocked door.
    """
    text = question.strip()
    for pattern, subject, message in _SUBJECTS:
        if pattern.search(text):
            return ScopeVerdict(subject=subject, message=message)
    return None


def in_scope(question: str) -> bool:
    return screen_scope(question) is None
