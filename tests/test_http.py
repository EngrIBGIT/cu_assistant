"""End-to-end HTTP tests.

The unit and evaluation tests in ``test_system.py`` all call ``Assistant.ask``
directly. That is deliberate — it keeps the suite fast and dependency-light — but
it means nothing in it proves the *HTTP layer* works: that the routes are
mounted, that the response model serialises, that the no-JavaScript page is
actually server-rendered rather than a client-side shell.

Those are separate failure modes with separate causes, and the second one matters
more than usual here. The audit that motivated this project found that the
University's own website serves no content to clients that do not execute
JavaScript, so a regression that quietly reintroduced a client-side dependency
would reproduce the exact problem being solved. These tests check the served
bytes, not the template.

Run with:

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import re
import unittest

from fastapi.testclient import TestClient

from app.api import app


class TestHttpInterface(unittest.TestCase):
    """The service as a client actually meets it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    # ------------------------------------------------------------- contract

    def test_ask_returns_the_documented_envelope(self) -> None:
        response = self.client.post(
            "/api/ask", json={"question": "How do I apply for a bachelor's degree?"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for field in (
            "question",
            "answer",
            "intent",
            "grounding",
            "abstained",
            "primary_route",
            "also_consider",
            "citations",
            "trace",
            "disclaimer",
            "mode",
            "version",
        ):
            self.assertIn(field, body, f"response envelope is missing {field!r}")
        self.assertTrue(body["answer"].strip(), "an empty answer is not an answer")
        self.assertIn(
            body["mode"], {"full", "retrieval_only", "lexical_fallback"}
        )

    def test_every_citation_carries_its_provenance(self) -> None:
        """A citation a user cannot check is decoration, not evidence."""
        body = self.client.post(
            "/api/ask", json={"question": "Where is the library?"}
        ).json()
        self.assertTrue(body["citations"], "an answer with no citation is ungrounded")
        for citation in body["citations"]:
            self.assertTrue(citation["source_url"].startswith("https://"))
            self.assertTrue(citation["retrieved_at"], "a citation needs a retrieval date")
            self.assertIn(
                citation["evidence"],
                {"retrieved_passage", "routing_table_record"},
                "a reader must be able to tell how the citation was established",
            )

    # ------------------------------------------------------------ validation

    def test_an_empty_question_is_rejected(self) -> None:
        for payload in ({"question": ""}, {"question": "   "}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/ask", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_an_over_long_question_is_rejected(self) -> None:
        """An input length limit is the cheapest denial-of-service control there is."""
        response = self.client.post("/api/ask", json={"question": "a" * 501})
        self.assertEqual(response.status_code, 422)

    def test_an_unknown_route_id_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/api/routes/NOPE").status_code, 404)

    # --------------------------------------------------------- rate limiting

    def test_the_rate_limiter_engages(self) -> None:
        """Documented behaviour: 30 requests per minute per client.

        Asserted with the limit read from the module rather than hard-coded, so
        raising the limit does not turn this into a failing test that nobody
        reads. The point being protected is that a limit exists and is enforced,
        not what the number is.
        """
        from app.api import RATE_LIMIT, _hits

        _hits.clear()
        statuses = [
            self.client.post("/api/ask", json={"question": "Where is the library?"}).status_code
            for _ in range(RATE_LIMIT + 2)
        ]
        self.assertIn(429, statuses, "the rate limiter never engaged")
        self.assertEqual(statuses[-1], 429)
        _hits.clear()

    # --------------------------------------------------------------- the page

    def test_the_no_javascript_page_is_server_rendered(self) -> None:
        """The finding that started this project, checked as a regression test.

        A client-side-rendered shell would return almost no text to a plain HTTP
        client. Asserting on the served bytes catches that directly, and it is the
        only check in the suite that would.
        """
        response = self.client.get("/fallback")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

        body = response.text
        self.assertGreater(len(body), 5000, "the page is too small to carry the content")
        self.assertIn("info@cosmopolitan.edu.ng", body)
        self.assertIn("+234 805 208 0828", body)
        # Real content, not a loading state.
        self.assertNotIn("enable JavaScript", body)
        self.assertNotIn("please wait while", body.lower())

    def test_the_no_javascript_page_needs_no_javascript_to_be_useful(self) -> None:
        """The page must carry the whole journey, including what is not published.

        Silently omitting the topics the University does not publish would leave a
        user wondering whether the site simply forgot. Saying so is the difference
        between a directory and a brochure.
        """
        body = self.client.get("/fallback").text
        self.assertRegex(body, r"not published|does not publish", re.IGNORECASE)
        # An anchor per destination, so it is navigable without scripting.
        self.assertGreaterEqual(body.count('href="#'), 5)

    def test_the_widget_is_served_with_a_javascript_content_type(self) -> None:
        """A widget served as text/html is blocked by every browser with a CSP."""
        for path, expected in (("/widget.js", "javascript"), ("/widget.css", "text/css")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.headers["content-type"])

    # ----------------------------------------------------------------- the ops

    def test_health_reports_the_real_configuration(self) -> None:
        """A degraded run must be visible as degraded.

        A silent fallback that nobody notices is worse than an outage, because an
        outage is obviously not working.
        """
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("active", body["embedder"])
        self.assertIn("configured", body["language_model"])
        self.assertGreater(body["routes"], 0)
        self.assertGreater(body["corpus"]["chunks"], 0)
        self.assertFalse(body["privacy"]["personal_data_collected"])
        self.assertFalse(body["privacy"]["write_actions"])

    def test_privacy_is_published_as_data(self) -> None:
        """The privacy claim should be checkable, not buried in a policy page."""
        body = self.client.get("/api/privacy").json()
        for claim in (
            "personal_data_collected",
            "accounts_required",
            "cookies_set",
            "analytics",
            "queries_logged",
            "queries_used_for_training",
        ):
            self.assertIn(claim, body)
            self.assertFalse(body[claim], f"{claim} should be False")
        self.assertTrue(body["retention"])

    def test_the_routing_table_is_published_as_data(self) -> None:
        """A directory that only exists behind a chatbot is a worse directory."""
        body = self.client.get("/api/routes").json()
        self.assertEqual(body["count"], len(body["routes"]))
        self.assertGreater(body["count"], 20)
        self.assertTrue(body["unpublished_topics"], "gaps must be published too")
        for route in body["routes"]:
            with self.subTest(route=route["route_id"]):
                self.assertTrue(route["source_url"], "a destination needs a source")
                self.assertTrue(route["verification"], "a destination needs a status")


if __name__ == "__main__":
    unittest.main()
