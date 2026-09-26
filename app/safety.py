"""Safety gates.

These run *before* retrieval so that a harmful or integrity-violating request
is never turned into retrieved context that a generator might paraphrase. That
ordering is deliberate: a filter applied after generation has to try to recognise
and undo text that has already been written, which is a losing position.

The gate is intentionally conservative in one direction only. It refuses when
it is reasonably confident, because a false refusal costs one user a redirect
while a false pass can put unsafe or contractually harmful content in front of an
applicant. It does not attempt to be a general content classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Requests to bypass someone else's access. The corpus cannot help with these
#: and answering them would be a security incident, not a failed query.
_UNAUTHORISED_ACCESS = re.compile(
    r"\b(hack|break into|crack|unauthori[sz]ed|without (his|her|their) "
    r"permission|someone else'?s account|my roommate'?s|my neighbour'?s|"
    r"bypass (the )?(login|password|authentication)|steal (a )?password|"
    r"keylog|phish)\w*",
    re.IGNORECASE,
)

#: Requests for material to be submitted as assessed work. The capstone
#: guidelines treat this as academic misconduct, and the institution would
#: reasonably take the same view. Ghostwriting a project is refused even though
#: the assistant is otherwise able to discuss the topic.
_ASSESSED_WORK = re.compile(
    r"\b(write|generate|produce|draft|compose|do)\b[^.?]{0,40}\b"
    r"(essay|thesis|project|report|assignment|capstone|dissertation|"
    r"assignment|homework)\b"
    r"|\b(essay|thesis|project|report|assignment|capstone)\b[^.?]{0,30}"
    r"\b(i can|we can)?\s*submit\b",
    re.IGNORECASE,
)

#: Exam and certification cheating.
_ASSESSED_IMPERSONATION = re.compile(
    r"\b(do (my|our) (assignment|project|exam|test)|take (my|our) exam "
    r"for|impersonate|log in as (my|our)|write my (exam|test))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyVerdict:
    refused: bool
    category: str | None = None
    message: str | None = None
    #: True when the request is unsafe *and* a legitimate destination still
    #: exists, so the refusal can redirect rather than simply block.
    redirect_route_hint: str | None = None


_ALLOW = SafetyVerdict(refused=False)


def screen(question: str) -> SafetyVerdict:
    """Decide whether a request must be refused before any retrieval occurs."""
    text = question.strip()

    if _UNAUTHORISED_ACCESS.search(text):
        return SafetyVerdict(
            refused=True,
            category="harmful_request",
            message=(
                "I can't help with accessing or intercepting someone else's account "
                "or device. That's not something this assistant does under any "
                "circumstances."
            ),
            redirect_route_hint="R-ICT-HELPDESK",
        )

    if _ASSESSED_WORK.search(text) or _ASSESSED_IMPERSONATION.search(text):
        return SafetyVerdict(
            refused=True,
            category="academic_integrity",
            message=(
                "I can't write assessed work for you to submit. A capstone or "
                "project has to be your own work, and the programme guidelines "
                "require you to be able to explain everything you submit."
            ),
            redirect_route_hint="R-ABOUT",
        )

    return _ALLOW


def is_unsafe(question: str) -> bool:
    return screen(question).refused
