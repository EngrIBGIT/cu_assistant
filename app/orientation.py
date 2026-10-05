"""Conversational replies for messages that are not questions.

A greeting is not an unanswerable factual question. Sending "hi" down the normal
path makes the assistant answer:

    Not published. The University does not publish this on any page this
    assistant can read, so it is not guessed at.

That is worse than silence, because it is a false statement. The University
publishes plenty, just not the thing this message was about — the message was
about nothing. Telling a visitor that the University has not published their
greeting is both wrong and, as a first impression, alarming.

So greetings are intercepted before retrieval and answered with what the service
is *for*, which is the only question a greeting is really asking.

## The constraint that shapes this file

An orientation reply must not become a hole in the safety system. Two rules keep
it from being one:

* **The whole message must be the greeting.** The patterns are anchored
  full-matches, not substring searches. "hi, my portal is not working" is not a
  greeting and goes down the normal path to be answered properly. The failure
  mode of a substring matcher here is not an unhelpful reply, it is a portal
  problem silently discarded because it contained two letters of "hi".

  The same reasoning removed "I need help" and "I'm stuck" from the list during
  development. Both read like small talk and both are problem statements — a
  request for help is a user getting to the point, and answering it with a
  capability list would be answering a different question than the one asked.
* **Nothing is asserted, so nothing is claimed.** These replies still set
  ``abstained=True`` and ``grounding="abstained"``. They are counted as
  non-answers, they carry no citations, and they cannot move a score. An
  orientation reply that claimed to be an answer would be exactly the thing this
  project exists not to do.
"""

from __future__ import annotations

import re

#: Normalised so that "Hi!", "hi", "  HI  " and "hey ;)" all reach the patterns.
#: Underscores and hyphens become spaces so "good-morning" and "good_evening"
#: match, which is how people type on a phone keyboard.
_NOISE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    lowered = text.strip().lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = _NOISE.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


#: A greeting, or a greeting with one piece of small talk attached ("hi there",
#: "hello agent", "good morning please"). Anchored: the entire message must be
#: one of these, which is why "hi" is caught and "hi there is a problem with my
#: account" is not.
_GREETING = re.compile(
    r"^(?:"
    r"hi+|hey|hello|hiya|heya|yo|sup|salut|bonjour|ahoy|"
    r"good (?:morning|afternoon|evening|day)|"
    r"morning|afternoon|evening|"
    r"howzuz|how are you|how're you|hows it going|whats up|what's up|"
    r"anyone there|is anyone there|are you there|you there|"
    r"what can you (?:do|help with|help me with)|"
    r"who are you|what are you|what do you do|"
    r"help|help me|start|begin|go|"
    r"ok|okay|k|kk|cool|nice|fine|"
    r"hmm|huh|hm|oh|ah|eh|ohh|oops|wow|oh no|"
    r"test|testing|ping|pong|"
    r"please|hope you are well|"
    r"there|anyone|"
    r"good (?:morning|afternoon|evening) (?:agent|assistant)"
    r")"
    r"(?: (?:there|agent|assistant|all|pls|please|again|sorry|um|uh|well|"
    r"i am here|im here|guys|anyone|team))*"
    r"[.!]?$"
)

#: A sign-off. Answered briefly: repeating the whole orientation to someone who
#: has already had it is a worse reply than a short one.
_CLOSING = re.compile(
    r"^(?:thanks?|thank you|thankyou|ta|cheers|many thanks|appreciate it|"
    r"ok(?:ay)?|cool|nice|bye|goodbye|good ?night|see you|see ya|cya|"
    r"that's all|thats all|that is all|nothing else|no more questions|"
    r"done|finished|got it|that helps|helpful|perfect)"
    r"(?: (?:you|so much|very much|a lot|for the help|again|all))*"
    r"[.!]?$"
)


#: What the service can actually do, in the user's terms. Every line here is a
#: capability the routing table genuinely backs, and none of it is a fact about
#: the University that could be out of date — the capabilities do not change when
#: a contact detail does.
_ORIENTATION = (
    "Hello — I'm the contact directory for Cosmopolitan University, Abuja.\n\n"
    "I answer from the University's own published pages and show you where each "
    "answer came from, so you can check it. I can help you find:\n\n"
    "- **the right portal** to apply through, or to check an application status\n"
    "- **who to contact** — an office, an email address, or a phone number\n"
    "- **programmes** offered, including the certificate programmes\n"
    "- **the library** — catalogue, contact, and how to reach it\n"
    "- **technical support**, if a portal is not working for you\n\n"
    "I will not guess at tuition fees, deadlines, entry requirements, "
    "scholarships, or accommodation. The University does not publish those "
    "anywhere I can read them, and a wrong figure from me could cost you an "
    "application. If you ask, I will say so plainly instead of inventing a "
    "number.\n\n"
    "Try asking: *“how do I apply?”, “who do I email about my certificate?”,* "
    "or *“the library phone number”*.\n\n"
    "For anything I cannot help with: **info@cosmopolitan.edu.ng** or "
    "**+234 805 208 0828**."
)

_CLOSING_REPLY = (
    "You're welcome. If you need anything else — a portal, an office, a phone "
    "number — ask me and I will look it up from the University's published "
    "pages.\n\n"
    "**info@cosmopolitan.edu.ng** or **+234 805 208 0828** if you would rather "
    "ask a person directly."
)


def conversational_reply(question: str) -> tuple[str, str] | None:
    """Return ``(answer, kind)`` if the message is a greeting or a sign-off.

    ``kind`` is ``"greeting"`` or ``"closing"`` and is recorded as the
    abstention reason, so the interface can say what kind of non-answer this is
    instead of claiming the University failed to publish a greeting.
    """
    text = _normalise(question)
    if not text:
        return None
    if _GREETING.match(text):
        return _ORIENTATION, "greeting"
    if _CLOSING.match(text):
        return _CLOSING_REPLY, "closing"
    return None
