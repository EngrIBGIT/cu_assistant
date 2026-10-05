"""Tests for what the service *says*, as distinct from what it answers.

Every other test in this suite asks whether the right destination comes back.
These ask whether the words around it are true. The distinction matters because
a grounded answer wrapped in a false sentence is still a false thing said to a
user, and no amount of correct routing detects it.

The clearest example is a greeting. "hi" is not a question, so the assistant
abstains — correctly — and then rendered the abstention as:

    Not published. The University does not publish this on any page this
    assistant can read, so it is not guessed at.

Which is a claim about the University, and a false one. It is also the first
thing a visitor sees, and the first thing a sceptical reader would use to decide
this thing is not trustworthy.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import STATIC_DIR, app
from app.fallback import (
    _CSS,
    _answer_html,
    _blocks_html,
    _markdown_to_html,
    _status_for,
)
from app.orientation import conversational_reply
from app.pipeline import Assistant

ROOT = Path(__file__).resolve().parent.parent


class _Stub:
    """Minimal stand-in with the attributes the renderer reads."""

    def __init__(self, *, abstained: bool, reason: str | None, answer: str = "") -> None:
        self.abstained = abstained
        self.abstention_reason = reason
        self.answer = answer
        self.citations = []
        self.primary_route = None
        self.also_consider = []
        self.disclaimer = ""


class TestGreetingsAreNotRefusals(unittest.TestCase):
    """A greeting is not an unanswerable question about the University."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = Assistant.build()
        cls.client = TestClient(app)

    # The refusal a greeting used to get, verbatim from the old renderer. It is
    # quoted here rather than described so that the test fails if the old
    # sentence ever comes back, and passes only if it is genuinely gone.
    OLD_REFUSAL = "I did not find a published answer to that"

    def test_a_greeting_does_not_claim_the_university_published_nothing(self) -> None:
        """A greeting is not a question, so a claim about the question is false.

        The orientation reply does say the University does not publish fees and
        deadlines. That is true, and it is about fees, not about "hi". The test
        therefore looks for the old refusal rather than for the words
        "does not publish", which would flag the accurate sentence too.
        """
        for greeting in ("hi", "Hello", "hey there", "good morning", "howzuz"):
            with self.subTest(greeting=greeting):
                answer = self.assistant.ask(greeting)
                self.assertNotIn(self.OLD_REFUSAL, answer.answer)
                self.assertNotIn("any page this assistant can read", answer.answer)
                self.assertIn("Cosmopolitan University", answer.answer)

    def test_a_greeting_asks_nothing_about_publication(self) -> None:
        """The status line is the part that made the false claim.

        "Not published." rendered above a greeting is a statement about the
        greeting. This checks the greeting path emits no such line.
        """
        from app.fallback import _status_for

        answer = self.assistant.ask("hi")
        self.assertEqual(_status_for(answer), "")

    def test_a_greeting_says_what_the_service_is_for(self) -> None:
        answer = self.assistant.ask("hi")
        lowered = answer.answer.lower()
        self.assertIn("portal", lowered)
        self.assertIn("contact", lowered)
        # And it still refuses to invent a figure, which is the point of it.
        self.assertIn("not guess", lowered)

    def test_a_greeting_is_still_recorded_as_a_non_answer(self) -> None:
        """The orientation reply must not become a way to claim an answer.

        If this returned abstained=False it would inflate every score it touched,
        and it would be doing it with a message that asserts no fact at all.
        """
        answer = self.assistant.ask("hi")
        self.assertTrue(answer.abstained)
        self.assertEqual(answer.grounding, "abstained")
        self.assertEqual(answer.citations, [])
        self.assertIsNone(answer.primary_route)
        self.assertEqual(answer.intent, "conversational")

    def test_a_sign_off_is_answered_briefly(self) -> None:
        answer = self.assistant.ask("thanks")
        self.assertTrue(answer.abstained)
        self.assertIn("conversational: closing", answer.abstention_reason)
        # Not the whole orientation again, to someone who already had it.
        self.assertNotIn("I can help you find", answer.answer)

    def test_the_api_reports_the_reason_it_did_not_answer(self) -> None:
        body = self.client.post("/api/ask", json={"question": "hi"}).json()
        self.assertEqual(body["intent"], "conversational")
        self.assertEqual(body["abstention_reason"], "conversational: greeting")
        self.assertTrue(body["abstained"])


class TestAGreetingIsNeverSwallowedWithARealQuestion(unittest.TestCase):
    """The other direction, and the one that would be a real defect.

    If the greeting matcher is a substring search, "hi" inside a longer sentence
    is enough to discard a question about a broken portal.
    """

    REAL_QUESTIONS = [
        "hi, my portal is not working",
        "hello I need help with my admission",
        "hello there is a problem with my account",
        "thanks, but what is the application deadline?",
        "hiya I lost my password",
        "I need help",
        "ok now what about fees",
        "good morning, what time does the library open?",
        "How do I apply for undergraduate admission?",
    ]

    def test_none_of_them_are_treated_as_small_talk(self) -> None:
        for question in self.REAL_QUESTIONS:
            with self.subTest(question=question):
                self.assertIsNone(conversational_reply(question))

    def test_no_frozen_case_is_a_greeting(self) -> None:
        """The frozen set is the strongest available check on the matcher."""
        import json

        path = ROOT / "eval" / "out_of_scope_set.json"
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertIsNone(
                    conversational_reply(case["question"]),
                    "a frozen case was swallowed as small talk",
                )

    def test_a_real_question_still_reaches_a_route(self) -> None:
        assistant = Assistant.build()
        answer = assistant.ask("hi, my portal is not working")
        self.assertNotEqual(answer.intent, "conversational")


class TestTheStatusLineMatchesTheReason(unittest.TestCase):
    """Four reasons to abstain, four different sentences.

    Rendering them all as "not published" is what made a safety refusal claim
    the University had not published the request to break into someone's
    account.
    """

    def test_each_reason_gets_its_own_wording(self) -> None:
        cases = {
            "unpublished topic: U-FEES": "Not published",
            "safety gate: harmful_request": "will not help",
            "outside domain: weather": "Outside what I cover",
            "no route above threshold and nothing in the corpus": "No published answer",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                html = _status_for(_Stub(abstained=True, reason=reason))
                self.assertIn(expected, html)

    def test_a_greeting_gets_no_status_line(self) -> None:
        """A status line is an explanation of a failure, and this is not one."""
        self.assertEqual(_status_for(_Stub(abstained=True, reason="conversational: greeting")), "")

    def test_an_answer_gets_no_status_line(self) -> None:
        self.assertEqual(_status_for(_Stub(abstained=False, reason=None)), "")

    def test_only_the_unpublished_topic_may_claim_the_university_did_not_publish(self) -> None:
        reasons = [
            "unpublished topic: U-FEES",
            "safety gate: academic_integrity",
            "outside domain: weather",
            "conversational: greeting",
            "no route above threshold and nothing in the corpus",
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                html = _status_for(_Stub(abstained=True, reason=reason))
                if not reason.startswith("unpublished topic"):
                    self.assertNotIn("does not publish", html)

    def test_an_unknown_reason_still_says_something_true(self) -> None:
        html = _status_for(_Stub(abstained=True, reason="something new entirely"))
        self.assertIn("No published answer", html)
        self.assertNotIn("does not publish", html)


class TestTheWeatherQuestion(unittest.TestCase):
    """The case that shows the bug: X15, which the frozen set already contains."""

    def test_a_weather_question_is_not_reported_as_unpublished(self) -> None:
        assistant = Assistant.build()
        answer = assistant.ask("What is the weather in Abuja tomorrow?")
        self.assertTrue(answer.abstained)
        self.assertNotEqual(answer.intent, "conversational")
        html = _answer_html(answer)
        self.assertNotIn("does not publish", html)

    def test_the_frozen_case_still_passes(self) -> None:
        assistant = Assistant.build()
        self.assertTrue(assistant.ask("What is the weather in Abuja tomorrow?").abstained)


class TestMarkdownRendering(unittest.TestCase):
    """The page used to strip `**` and leave `_` visible."""

    def test_bold_and_italic_both_render(self) -> None:
        self.assertEqual(
            _markdown_to_html("**bold** and _italic_"),
            "<strong>bold</strong> and <em>italic</em>",
        )

    def test_underscores_inside_a_word_are_left_alone(self) -> None:
        self.assertEqual(_markdown_to_html("a_b_c"), "a_b_c")

    def test_answer_text_cannot_inject_markup(self) -> None:
        out = _markdown_to_html("<script>alert(1)</script> **x**")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_no_markdown_markers_leak_into_the_rendered_answer(self) -> None:
        """The page used to strip ** and leave _ visible on the page.

        A user reading "_Check time-sensitive facts..._" sees the underscores,
        which reads as a rendering fault and undermines trust in the source
        attribution printed next to it.
        """
        assistant = Assistant.build()
        html = _answer_html(assistant.ask("How do I apply?"))
        self.assertNotIn("_Check time-sensitive", html)
        self.assertNotIn("**", html)
        # No stray marker left anywhere in the answer block.
        self.assertIsNone(
            re.search(r"(?<![\w>])_[^_\n]+_(?![\w<])", html),
            f"an underscore-delimited marker survived: {html!r}",
        )

    def test_a_bullet_list_renders_as_a_list(self) -> None:
        """A block that *begins* with a dash used to keep its dashes.

        The old pattern only matched a list when a line of prose preceded it,
        which is exactly the shape of the greeting — the first thing a visitor
        reads, and the one place a literal "- the right portal" is most visible.
        """
        html = _blocks_html("- the right portal\n- who to contact")
        self.assertEqual(html.count("<ul>"), 1)
        self.assertEqual(html.count("<li>"), 2)
        self.assertNotIn("- the right portal", html)

    def test_a_list_after_a_line_of_prose_still_renders_as_a_list(self) -> None:
        html = _blocks_html("I can help you find:\n- portals\n- contacts")
        self.assertIn("<p>I can help you find:</p>", html)
        self.assertIn("<ul><li>portals</li><li>contacts</li></ul>", html)

    def test_prose_around_a_list_stays_in_order(self) -> None:
        html = _blocks_html("Before.\n- one\n- two\nAfter.")
        self.assertLess(html.index("Before."), html.index("<li>one"))
        self.assertLess(html.index("<li>two"), html.index("After."))

    def test_ordinary_answers_are_not_turned_into_a_list(self) -> None:
        """A hyphen inside prose is not a bullet.

        "Plot 432, Yakubu J. Pam Street" and any dash used as punctuation must
        not turn a sentence into a list item.
        """
        html = _blocks_html("Email me at a@b.ng - or call 0805 208 0828.")
        self.assertNotIn("<ul>", html)
        self.assertEqual(html.count("<p>"), 1)

    def test_the_disclaimer_is_not_repeated_under_every_answer(self) -> None:
        """The page states it once, in the note above the directory.

        The strip is matched against the configured text. An earlier pattern
        looked for the word "disclaimer" inside the sentence, which the sentence
        does not contain, so it never fired.
        """
        from app.config import settings

        assistant = Assistant.build()
        html = _answer_html(assistant.ask("How do I apply?"))
        self.assertNotIn(settings.disclaimer, html)

    def test_genuine_trailing_italics_are_not_eaten(self) -> None:
        """Only the disclaimer is stripped.

        Silently dropping the last line of a grounded answer would be a worse
        defect than a repeated warning, so an unrelated italic tail survives.
        """
        html = _answer_html(_Stub(abstained=False, reason=None, answer="Answer first\n\n_Optional._"))
        self.assertIn("Optional.", html)


class TestTheWidgetShipsItsOwnStylesheet(unittest.TestCase):
    """It did not, and a host page that linked only the script got an unstyled
    wall of divs: the launcher rendered as plain text and the panel was invisible.
    A widget that needs the host to know what else to link is not one tag.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.js = cls.client.get("/widget.js").text

    def test_the_script_loads_its_own_css(self) -> None:
        self.assertIn("widget.css", self.js)
        self.assertIn("rel", self.js)
        self.assertIn("stylesheet", self.js)

    def test_the_css_is_served(self) -> None:
        response = self.client.get("/widget.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["content-type"])

    def test_the_css_covers_both_mount_shapes(self) -> None:
        css = self.client.get("/widget.css").text
        self.assertIn(".cra-root", css)
        self.assertIn(".cra-panel", css)
        self.assertIn(".cra-launcher", css)

    def test_the_page_mounts_the_chat_inline(self) -> None:
        page = self.client.get("/").text
        self.assertIn('id="cra-root"', page)
        self.assertIn('src="/widget.js"', page)

    def test_the_fallback_page_has_no_chat(self) -> None:
        page = self.client.get("/fallback").text
        self.assertNotIn('id="cra-root"', page)
        self.assertNotIn('src="/widget.js"', page)
        # The chat-first layout must not leave a gap where the chat would be.
        self.assertNotIn('class="ask" id="cra', page)

    def test_the_widget_says_something_different_for_each_reason(self) -> None:
        """The same false-claim bug the server-rendered page had, in the widget.

        The widget had one string for every abstention. Each reason now gets its
        own, and this checks the distinctions exist in the shipped file rather
        than in a comment about them.
        """
        for phrase in (
            "The University does not publish this anywhere I can read",  # unpublished
            "I will not help with that",  # safety
            "Outside what I cover",  # outside the domain
            "No published answer",  # nothing retrieved
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.js)
        # A greeting gets no status line at all, which is why the table has an
        # empty entry for it rather than a fifth sentence.
        self.assertIn("conversational", self.js)

    def test_the_widget_offers_a_working_directory_when_the_api_is_down(self) -> None:
        """It used to vanish into a message with no link out of the failure."""
        self.assertIn("/fallback", self.js)


class TestTheWidgetActuallyRuns(unittest.TestCase):
    """Run app/static/widget.js, rather than grepping it for strings.

    Every other test in this file checks that the widget file *mentions* a thing.
    That is not the same as the widget doing it, and the gap between them hid a
    real defect: the renderer used for API answers had no list handling, so the
    greeting displayed its own "- " dashes on the widget while the page had
    already been fixed. No string check would have found that.

    The check is a Node script with a small fake DOM. It is skipped, not failed,
    where Node is unavailable, so the suite still runs on a machine without it.
    """

    SCRIPT = ROOT / "tests" / "widget_dom_check.mjs"

    def test_it_passes(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH; the widget DOM check was not run")
        result = subprocess.run(
            [node, str(self.SCRIPT)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        self.assertEqual(
            result.returncode,
            0,
            "widget_dom_check.mjs failed:\n"
            + (result.stdout + result.stderr)[-4000:],
        )
        self.assertIn("all checks passed", result.stdout)


class TestThePageIsDesignedForThePhone(unittest.TestCase):
    """The case the audit identified: limited data, older phones, no JavaScript."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.page = cls.client.get("/fallback").text

    def test_one_column_at_any_width(self) -> None:
        self.assertNotIn("@media", self.page.split("</style>")[0].split("{")[0])
        # A fixed multi-column grid would break below 320px.
        self.assertIn("grid-template-columns:1fr auto", self.page)
        self.assertIn("@media (min-width:44rem)", self.page)

    def test_it_costs_no_font_request(self) -> None:
        self.assertNotIn("@font-face", self.page)
        self.assertNotIn("fonts.googleapis", self.page)
        self.assertNotIn("fonts.gstatic", self.page)

    def test_it_costs_no_image_request(self) -> None:
        self.assertNotIn("<img", self.page)

    def test_it_respects_a_dark_preference(self) -> None:
        self.assertIn("prefers-color-scheme:dark", self.page)

    def test_every_interactive_element_has_a_visible_focus_ring(self) -> None:
        self.assertIn(":focus-visible", self.page)

    def test_the_jump_list_is_anchor_navigation(self) -> None:
        """The only navigation that works with scripting off."""
        self.assertIn('class="jumpto"', self.page)
        self.assertEqual(self.page.count('href="#cat-'), 10)

    def test_the_directory_still_carries_every_destination(self) -> None:
        self.assertEqual(self.page.count('<article class="card">'), 29)


def _relative_luminance(hex_colour: str) -> float:
    """WCAG 2.x relative luminance, from a ``#rgb`` or ``#rrggbb`` string."""
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class TestThePaletteIsReadable(unittest.TestCase):
    """The text/background pairs the design system names, measured.

    Two greys in the Academic Institutional system clear 4.5:1 on white and not
    on the tinted surfaces they are also used on. The system was adopted as
    drawn, and #64748B measured 4.47:1 on the #F6F8FA canvas — which is exactly
    where the route block's field labels sit, so the failure was real and visible
    rather than theoretical. The substitute is recorded in both stylesheets.

    Nothing about that is discoverable by reading the CSS, and the next person
    to copy a colour out of a design tool would reintroduce it without noticing,
    so the pairs are pinned here instead.
    """

    #: (foreground, background, minimum ratio, what it is)
    LIGHT_PAIRS = [
        ("#1f2937", "#ffffff", 4.5, "body text on a card"),
        ("#1f2937", "#f6f8fa", 4.5, "body text on the canvas"),
        ("#2c3a47", "#ffffff", 4.5, "headings on a card"),
        ("#2c3a47", "#f6f8fa", 4.5, "headings on the canvas"),
        ("#5c6a77", "#ffffff", 4.5, "muted metadata on a card"),
        ("#5c6a77", "#f6f8fa", 4.5, "muted metadata on the canvas"),
        ("#5c6a77", "#eef2f6", 4.5, "muted metadata on the ribbon band"),
        ("#0b5d3b", "#ffffff", 4.5, "a link on a card"),
        ("#0b5d3b", "#f6f8fa", 4.5, "a link on the canvas"),
        ("#0b5d3b", "#eff6f1", 4.5, "a link on the hover wash"),
        ("#ffffff", "#0b5d3b", 4.5, "a primary button"),
        ("#5c3200", "#fffbeb", 4.5, "abstention copy"),
        ("#8a4b00", "#fffbeb", 4.5, "the abstention accent"),
    ]

    DARK_PAIRS = [
        ("#e8eef3", "#141c24", 4.5, "body text on a card"),
        ("#e8eef3", "#0f151b", 4.5, "body text on the canvas"),
        ("#9fb0bf", "#141c24", 4.5, "muted metadata on a card"),
        ("#9fb0bf", "#0f151b", 4.5, "muted metadata on the canvas"),
        ("#2fa36a", "#141c24", 4.5, "a link on a card"),
        ("#2fa36a", "#0f151b", 4.5, "a link on the canvas"),
        ("#2fa36a", "#10241b", 4.5, "a link on the route block"),
        ("#f4d9b0", "#2b1f0c", 4.5, "abstention copy"),
    ]

    def _check(self, pairs: list[tuple[str, str, float, str]]) -> None:
        for fg, bg, minimum, description in pairs:
            with self.subTest(description):
                self.assertGreaterEqual(
                    _contrast(fg, bg),
                    minimum,
                    f"{description}: {fg} on {bg} is "
                    f"{_contrast(fg, bg):.2f}:1, below {minimum}:1",
                )

    def test_the_light_palette_clears_aa(self) -> None:
        self._check(self.LIGHT_PAIRS)

    def test_the_dark_palette_clears_aa(self) -> None:
        self._check(self.DARK_PAIRS)

    def test_the_focus_ring_is_visible_wherever_it_lands(self) -> None:
        """WCAG 2.2 SC 1.4.11: a focus indicator needs 3:1 against its neighbours.

        The ring is the accent green, which is also the fill of the primary
        button and the launcher. That is only safe because the ring is drawn
        with a non-zero ``outline-offset``: the gap it creates is filled by the
        page behind the control, so the green ring never sits flush against the
        green button and is never adjacent to it. Both halves of that are checked
        here, because "it is green" on its own is a genuine failure and reading
        the CSS does not show it.
        """
        for bg in ("#ffffff", "#f6f8fa", "#eef2f6"):
            with self.subTest(surface=bg):
                self.assertGreaterEqual(
                    _contrast("#0b5d3b", bg), 3.0, f"ring on {bg}"
                )

    def test_the_ring_is_offset_so_it_never_touches_the_green_fill(self) -> None:
        """Without the offset, a green ring on the green button is invisible."""
        self.assertRegex(
            _CSS,
            r":focus-visible\{[^}]*outline-offset:\s*(?!0)[^;}]+",
            "the focus ring must be offset, or it is invisible on the primary button",
        )
        widget_css = (STATIC_DIR / "widget.css").read_text(encoding="utf-8")
        self.assertRegex(
            widget_css,
            r"\.cra-send:focus-visible,\s*[^}]*\{[^}]*outline-offset:\s*(?!0)",
            "the widget's send button needs the same offset",
        )

    def test_the_page_uses_the_measured_greys(self) -> None:
        """The substituted grey is in the stylesheet, and the failing one is not.

        Asserting the value rather than the ratio means a regression is
        reported as "the colour changed" instead of as an arithmetic result
        nobody can connect to a line of CSS.
        """
        css = _CSS
        self.assertIn("--muted:#5c6a77", css)
        self.assertNotIn("#64748b", css)

    def test_the_widget_uses_the_measured_greys_too(self) -> None:
        widget_css = STATIC_DIR / "widget.css"
        text = widget_css.read_text(encoding="utf-8")
        self.assertNotIn("#64748b", text)
        self.assertIn("#5c6a77", text)

    def test_no_webfont_is_requested(self) -> None:
        """The palettes above assume a system font, which is the only font used."""
        self.assertNotIn("fonts.googleapis", _CSS)
        self.assertNotIn("@font-face", _CSS)


if __name__ == "__main__":
    unittest.main()
