"""Regression tests for four defects found by testing the running service.

The rest of the suite is organised by commitment, and those commitments were all
covered. These four were not: they were found by running the application, asking
it the questions a real applicant would ask, and reading the bytes it sent back.
Each had a passing suite behind it, which is the point of recording them here —
a test that asserts the router returns something protects nothing, but so does a
test that never asks whether the thing on screen was written for the user.

* :class:`TestAuthoringInstructionsNeverShip` — corpus and routing-table prose
  containing instructions to the assistant ("The assistant must never state an
  amount") was quoted straight into user-facing answers. The source was the
  problem, so these tests scan the source, not just the output.
* :class:`TestUnpublishedTopicsAreRecognised` — the abstention gate scored
  match terms, so "Is there an acceptance fee?" was answered instead of refused,
  because "acceptance" and "fee" each scored once against a ``min_hits`` of two.
  Each phrase is worth two, which is why the fix was to add phrases and not to
  lower the threshold.
* :class:`TestSecurityHeaders` and
  :class:`TestValidationDoesNotEchoTheQuestion` — ``operations.md`` listed
  security headers among the controls; the application set none. A 422 response
  repeated the entire submitted value, which for an over-length question made
  the error body larger than any answer the service gives.
* :class:`TestNoJavaScriptCarriesTheWholeJourney` — the fallback page was a
  complete directory and could not answer anything, so the claim that it carried
  the whole primary journey was true of routing and false of asking.

Run with:

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import app
from app.composer import _is_internal
from app.pipeline import Assistant

ROOT = Path(__file__).resolve().parent.parent


def _as_text(markup_or_text: str) -> str:
    """Collapse HTML or Markdown down to comparable plain text.

    Used where the question is "does the page say the same words as the API",
    which is a question about content and not about whether a renderer happened
    to leave the ``**`` in. Also folds the whitespace that inline elements
    introduce, so ``<strong>a</strong>b`` compares equal to ``ab``.
    """
    text = re.sub(r"<[^>]+>", "", markup_or_text)
    text = text.replace("**", "").replace("__", "")
    # A list marker is presentation, like the bold markers above it: the page
    # turns "- portal" into a bullet, so the marker cannot survive the
    # comparison. The words after it are what has to match.
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)
    text = (
        text.replace("&mdash;", "—").replace("&ndash;", "–")
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#x27;", "'").replace("&nbsp;", " ")
    )
    # Underscore-delimited italics only at word boundaries, so a_b_c survives.
    text = re.sub(r"(?<![\w*])_([^_\n]+?)_(?![\w*])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _squeeze(markup_or_text: str) -> str:
    """Delete all whitespace, for content comparisons.

    ``_as_text`` is the readable form. This is the strict one. The page joins
    block elements with no separator — ``</p><p>`` — while the API joins its
    paragraphs with a blank line, so the same two sentences have one space
    between them in one and none in the other. Removing every space on both
    sides compares the characters that carry meaning and ignores the layout,
    which is the thing under test here.
    """
    return re.sub(r"\s+", "", _as_text(markup_or_text))


#: Phrasing that belongs to an author writing notes to an assistant, and must
#: never appear in a user-facing answer or in the data the page renders from.
#: The application-level filter is ``app.composer._INTERNAL_NOTE``; this list is
#: deliberately a second, independent statement of the same rule, so deleting the
#: filter still fails a test rather than silently permitting the leak again.
INSTRUCTION_MARKERS = (
    "the assistant",
    "must never",
    "must not state",
    "must not say",
    "must not guess",
    "never state an amount",
    "do not state",
    "this route exists",
    "audit time",
    "designed to",
    "is explicitly",
)

#: Questions a real applicant would type, each of which the running service
#: answered confidently instead of refusing. The expected topic is the gap the
#: answer was silently papering over.
GAPS = (
    ("Is there an acceptance fee?", "U-FEES"),
    ("Do I have to pay a registration fee before I start?", "U-FEES"),
    ("Can I pay the tuition in instalments?", "U-FEES"),
    ("What does the tuition cost per year?", "U-FEES"),
    ("How much does the MBA programme cost?", "U-FEES"),
    ("When is the last day to apply?", "U-DEADLINE"),
    ("Is the application still open?", "U-DEADLINE"),
    ("How many credits do I need to apply?", "U-ENTRY"),
    ("What O-level subjects do I need?", "U-ENTRY"),
    ("What are the requirements for admission?", "U-ENTRY"),
    ("How long does the programme take?", "U-DURATION"),
    ("How many years does the course last?", "U-DURATION"),
)

#: Headers ``docs/operations.md`` asserted and the application omitted.
REQUIRED_HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "strict-transport-security",
)


class _Built(unittest.TestCase):
    """Mixin providing one built assistant, reused by the classes that need it."""

    @classmethod
    def setUpClass(cls) -> None:
        if not hasattr(_Built, "assistant"):
            from tests.test_system import PRIMARY

            _Built.assistant = Assistant.build(embedder_kind=PRIMARY)


class TestAuthoringInstructionsNeverShip(unittest.TestCase):
    """Source-level, so the note cannot be reintroduced and re-quoted."""

    def test_no_corpus_file_contains_an_instruction_to_the_assistant(self) -> None:
        offenders: list[str] = []
        for path in sorted((ROOT / "data" / "corpus").glob("*.md")):
            text = path.read_text(encoding="utf-8").lower()
            for marker in INSTRUCTION_MARKERS:
                if marker in text:
                    offenders.append(f"{path.name}: {marker!r}")
        self.assertEqual(offenders, [], "authoring notes must not live in the corpus")

    def test_no_routing_table_note_is_an_instruction(self) -> None:
        """Notes are shown to the reader, so a note can reach a user verbatim.

        ``routes`` carry ``notes`` and ``unpublished_topics`` carry ``note``, and
        the fallback page renders both. Checking only one of the two spellings
        is how the last set of instructions stayed on the page.
        """
        table = json.loads((ROOT / "data" / "routing_table.json").read_text(encoding="utf-8"))
        offenders: list[str] = []
        for group in ("routes", "unpublished_topics"):
            for row in table.get(group, []):
                name = row.get("route_id") or row.get("topic_id")
                note = row.get("notes") or row.get("note") or ""
                for marker in INSTRUCTION_MARKERS:
                    if marker in note.lower():
                        offenders.append(f"{name}: {marker!r}")
        self.assertEqual(offenders, [], "a routing note is displayed text, not a comment")

    def test_the_filter_recognises_the_phrasing_it_exists_to_remove(self) -> None:
        """The application-level filter, exercised against the real sentences."""
        for note in (
            "The assistant must never state an amount.",
            "The assistant must not guess a fee.",
            "This route exists so the assistant can be useful without inventing one.",
            "This figure must not be stated.",
            "Never state an amount.",
        ):
            with self.subTest(note=note):
                self.assertTrue(_is_internal(note))

    def test_ordinary_prose_is_not_mistaken_for_an_instruction(self) -> None:
        """A filter this broad would silently delete the corpus.

        Fees being unpublished is a fact the user needs; only the instruction
        about what to do with that fact is internal.
        """
        for sentence in (
            "The University does not publish a fee figure on any page we can read.",
            "No amount is published, so we cannot quote one.",
            "This office can tell you the figure directly.",
        ):
            with self.subTest(sentence=sentence):
                self.assertFalse(_is_internal(sentence))


class TestUnpublishedTopicsAreRecognised(_Built):
    """The live failures were confident answers to unpublished questions."""

    def test_gap_questions_are_refused_with_the_right_topic_named(self) -> None:
        missed: list[str] = []
        for question, topic in GAPS:
            with self.subTest(question=question):
                response = _Built.assistant.ask(question)
                reason = response.abstention_reason or ""
                if not response.abstained or topic not in reason:
                    missed.append(
                        f"{question!r} -> abstained={response.abstained} reason={reason!r}"
                    )
        self.assertEqual(missed, [], "a gap question was answered instead of refused")

    def test_a_refusal_does_not_invent_a_price_while_asking_about_one(self) -> None:
        for question, _ in GAPS:
            if "fee" not in question and "cost" not in question:
                continue
            with self.subTest(question=question):
                answer = _Built.assistant.ask(question).answer
                self.assertNotRegex(answer, r"(?i)\b\d[\d,]*\s*(naira|ngn)\b")
                self.assertNotRegex(answer, r"(?i)[₦#]\s?\d")


class TestSecurityHeaders(unittest.TestCase):
    """A control asserted in documentation but absent from the product."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_every_response_carries_the_headers(self) -> None:
        for path in ("/", "/fallback", "/api/health", "/api/routes", "/widget.js", "/nope"):
            with self.subTest(path=path):
                response = self.client.get(path)
                for header in REQUIRED_HEADERS:
                    self.assertIn(header, response.headers, f"{path} is missing {header}")

    def test_error_responses_carry_them_too(self) -> None:
        response = self.client.post("/api/ask", json={"question": ""})
        self.assertEqual(response.status_code, 422)
        for header in REQUIRED_HEADERS:
            self.assertIn(header, response.headers, f"the 422 is missing {header}")

    def test_the_policy_forbids_inline_and_remote_script(self) -> None:
        csp = self.client.get("/").headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        script_src = csp.split("script-src")[1].split(";")[0]
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("http://", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)

    def test_the_widget_never_needed_the_relaxation(self) -> None:
        """``style-src 'unsafe-inline'`` is justified by the fallback page alone.

        If the widget ever acquires an inline handler, that stops being a
        deliberate relaxation and the justification in ``api.py`` goes stale.
        """
        source = self.client.get("/widget.js").text
        self.assertNotIn("eval(", source)
        self.assertNotIn("new Function", source)
        self.assertNotIn("javascript:", source)

    def test_the_policy_is_not_weakened_outside_the_documentation(self) -> None:
        """The one CDN exception is scoped, and only one.

        FastAPI serves Swagger UI from jsdelivr, so a strict ``script-src 'self'``
        renders ``/docs`` as a blank page. Allowing that origin everywhere would
        hand a third party script execution on every page of the service, so it
        is allowed on the documentation routes and nowhere else.
        """
        relaxed = self.client.get("/docs")
        self.assertEqual(relaxed.status_code, 200)
        self.assertIn("cdn.jsdelivr.net", relaxed.headers["content-security-policy"])

        for path in ("/", "/fallback", "/api/health", "/widget.js"):
            with self.subTest(path=path):
                csp = self.client.get(path).headers["content-security-policy"]
                self.assertNotIn("cdn.jsdelivr.net", csp)
                self.assertIn("script-src 'self'", csp)


class TestValidationDoesNotEchoTheQuestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_an_over_length_question_is_not_returned_in_the_error(self) -> None:
        oversized = "How much is tuition? " * 200
        response = self.client.post("/api/ask", json={"question": oversized})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("input", response.text)
        self.assertNotIn("How much is tuition?", response.text)
        self.assertLess(len(response.content), 1000)

    def test_the_error_still_explains_itself(self) -> None:
        """Scrubbing the value must not scrub the diagnosis."""
        response = self.client.post("/api/ask", json={"question": ""})
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertTrue(detail)
        self.assertIn("loc", detail[0])
        self.assertIn("question", detail[0]["loc"])
        self.assertIn("msg", detail[0])
        self.assertIn("type", detail[0])

    def test_a_wrongly_typed_field_is_not_echoed_either(self) -> None:
        response = self.client.post("/api/ask", json={"question": "hello", "debug": "maybe"})
        self.assertEqual(response.status_code, 422)
        for error in response.json()["detail"]:
            self.assertNotIn("input", error)
            self.assertNotIn("ctx", error)
        # The literal the caller sent must not be anywhere in the body. Checked
        # against the substring rather than the word "input", because Pydantic's
        # own message for this error is "unable to interpret input".
        self.assertNotIn("maybe", response.text)

    def test_a_valid_request_is_untouched(self) -> None:
        response = self.client.post("/api/ask", json={"question": "How do I apply?"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("answer", response.json())


class TestNoJavaScriptCarriesTheWholeJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_the_page_offers_a_question_without_scripting(self) -> None:
        page = self.client.get("/fallback").text
        self.assertRegex(page, r'<form[^>]*method="get"')
        self.assertRegex(page, r'<input[^>]*name="question"')
        self.assertNotIn("<script", page.lower())

    def test_the_label_is_associated_with_the_input(self) -> None:
        page = self.client.get("/fallback").text
        self.assertRegex(page, r'<label[^>]*for="q"')
        self.assertRegex(page, r'<input[^>]*id="q"')

    def test_a_question_is_answered_by_the_server(self) -> None:
        page = self.client.get("/fallback", params={"question": "How do I apply for admission?"}).text
        self.assertIn("Your answer", page)
        self.assertIn("Sources", page)
        self.assertNotIn("<script", page.lower())

    def test_the_no_javascript_answer_is_the_same_answer(self) -> None:
        """The claim is that the page loses nothing, so it had better be true.

        Same question through the form and through the API. The route, the source
        and the opening paragraph all have to be the same, because if the two
        paths ever diverge the page is carrying a second, untested copy of the
        decision logic.

        Compared as plain text. The page renders the answer's Markdown into
        markup — ``**bold**`` becomes ``<strong>`` — so a raw substring check
        against the page now fails on a rendering improvement rather than on a
        divergence, which is the kind of test that gets deleted when it is
        inconvenient. Unwrapping the tags and comparing the words is what this
        test was always trying to say.

        The one deliberate difference is the trailing disclaimer, which the page
        drops because it states it in full in the note above the directory. Two
        near-identical warnings one screen apart train people to skip both, so
        the strip is intended; the second assertion below is what keeps it from
        becoming a silent removal of the only warning on the page.
        """
        from app.config import settings
        from app.fallback import _strip_trailing_disclaimer

        question = "How do I apply for undergraduate admission?"
        body = self.client.post("/api/ask", json={"question": question}).json()
        page = self.client.get("/fallback", params={"question": question}).text

        self.assertIsNotNone(body["primary_route"])
        self.assertIn(body["primary_route"]["label"], page)
        self.assertIn(body["citations"][0]["source_url"], page)

        rendered = _squeeze(page)
        # Every word of the answer, not just the opening paragraph, compared with
        # whitespace removed. A renderer that dropped a line or a citation fails
        # here.
        self.assertIn(_squeeze(_strip_trailing_disclaimer(body["answer"])), rendered)

        # The warning is still on the page, said once rather than twice.
        self.assertIn("Check fees and deadlines", page)
        self.assertEqual(page.count(settings.disclaimer), 0)

    def test_a_refusal_is_stated_rather_than_merely_omitted(self) -> None:
        page = self.client.get("/fallback", params={"question": "How much is tuition?"}).text
        self.assertIn("Not published", page)

    def test_the_page_still_renders_the_whole_directory(self) -> None:
        """The form must not have displaced the directory it was added to.

        Counted against ``/api/routes`` rather than against a few hard-coded
        names, so a card going missing fails here instead of quietly reducing
        the page to a search box over 24 of the 29 destinations.
        """
        page = self.client.get("/fallback").text
        table = self.client.get("/api/routes").json()
        self.assertEqual(page.count('<article class="card">'), table["count"])
        self.assertEqual(page.count("<li><strong>"), len(table["unpublished_topics"]))
        for topic in table["unpublished_topics"]:
            with self.subTest(topic=topic["topic_id"]):
                self.assertIn(topic["label"], page)

    def test_the_unpublished_list_shows_no_internal_instruction(self) -> None:
        """The page renders each gap's note verbatim, so the note is the page."""
        page = self.client.get("/fallback").text
        listing = page.split("What the University does not publish")[1]
        for marker in INSTRUCTION_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, listing.lower())

    def test_no_answer_is_rendered_for_an_empty_question(self) -> None:
        self.assertNotIn("Your answer", self.client.get("/fallback").text)

    def test_a_submitted_question_cannot_inject_markup(self) -> None:
        page = self.client.get(
            "/fallback", params={"question": "<script>alert(1)</script> fees?"}
        ).text
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_an_over_length_question_is_bounded_by_the_server(self) -> None:
        """``maxlength`` is a browser hint, so the server has to bound it too.

        The attribute can be removed from the request by anyone crafting a URL,
        and the API path enforces 500 characters with Pydantic. This path has no
        such schema, so if the bound were not re-applied here the form would be
        the one way in around it.
        """
        page = self.client.get("/fallback", params={"question": "x" * 4000}).text
        self.assertIn("Your answer", page)
        echoed = re.search(r'value="(x*)"', page)
        self.assertIsNotNone(echoed, "the form should echo the question back")
        self.assertLessEqual(len(echoed.group(1)), 500)


if __name__ == "__main__":
    unittest.main()
