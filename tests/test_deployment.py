"""Tests for the two-process deployment shape.

The service is one codebase deployed as two processes: a JSON API that owns the
assistant, and a frontend that owns the pages. These tests check the *separation*
rather than either half, because a deployment that quietly collapsed back into one
process would look identical from a browser's point of view and would still pass
every functional test in the suite.

Two things make the split worth testing at all:

* The frontend never imports the pipeline, so it never loads torch. That is a
  ~57 MB saving and a ~25 second cold-start saving, and it is invisible in any
  response — it is only observable by looking at what the process imported.
* The no-JavaScript page still answers, and still answers with the one decision
  path. When the answer is produced by an HTTP call to another process, there is
  a new way for it to be wrong: the frontend could approximate, cache, or invent.
  So the test compares the rendered page against the API's own response.

Run with:

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import ALLOWED_ORIGINS, API_PUBLIC_URL, WEB_PUBLIC_URL, app
from app.api import backend_app, create_app, frontend_app
from app.config import API_PORT, WEB_PORT

ROOT = Path(__file__).resolve().parent.parent

#: The ports the deployment is specified on. Asserted rather than assumed, because
#: "the server is on 8003" is a claim a reader will check against /api/health.
EXPECTED_API_PORT = 8003
EXPECTED_WEB_PORT = 5020


def _paths(application) -> set[str]:
    return {getattr(route, "path", "") for route in application.routes}


class TestTheDeploymentShape(unittest.TestCase):
    """The API serves the API. The frontend serves pages. Neither serves the other."""

    def test_the_ports_are_the_ones_specified(self) -> None:
        self.assertEqual(API_PORT, EXPECTED_API_PORT)
        self.assertEqual(WEB_PORT, EXPECTED_WEB_PORT)
        self.assertEqual(API_PUBLIC_URL, f"http://127.0.0.1:{EXPECTED_API_PORT}")
        self.assertEqual(WEB_PUBLIC_URL, f"http://127.0.0.1:{EXPECTED_WEB_PORT}")

    def test_the_api_process_has_no_directory(self) -> None:
        paths = _paths(backend_app)
        self.assertIn("/api/ask", paths)
        self.assertIn("/api/routes", paths)
        self.assertIn("/api/health", paths)
        # "/" is a notice saying this port is the API and linking onwards, not
        # the directory. The directory is the other process's job.
        for page in ("/fallback", "/widget.js", "/widget.css", "/widget-config.js"):
            self.assertNotIn(page, paths, f"the API must not serve {page}")

    def test_the_api_root_explains_itself(self) -> None:
        """It used to be a bare {"detail": "Not Found"}.

        Not wrong, but it reads as a broken service, and it is the first thing
        anyone opening the API port sees now that there are two ports.
        """
        response = TestClient(backend_app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertNotIn("Not Found", response.text)
        for link in ("/docs", "/api/health", "/api/routes", WEB_PUBLIC_URL):
            self.assertIn(link, response.text, f"the notice should point at {link}")

    def test_the_frontend_process_has_no_api(self) -> None:
        paths = _paths(frontend_app)
        self.assertIn("/", paths)
        self.assertIn("/fallback", paths)
        self.assertIn("/widget.js", paths)
        for endpoint in ("/api/ask", "/api/routes", "/api/health", "/api/privacy"):
            self.assertNotIn(endpoint, paths, f"the frontend must not serve {endpoint}")

    def test_the_frontend_does_not_advertise_an_empty_api(self) -> None:
        """A Swagger page listing zero endpoints is worse than a 404.

        The frontend mounts no routes, so FastAPI's default would have served
        /docs with an empty schema and /openapi.json with "paths": {} — a page
        inviting a reader to call an API that lives on the other port.
        """
        client = TestClient(frontend_app)
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 404, path)

    def test_the_combined_app_still_serves_everything(self) -> None:
        """The single-process deployment is the default and must not regress.

        It is what the rest of the suite exercises, so a refactor that quietly
        dropped a route from the combined app would show up here first.
        """
        paths = _paths(app)
        for route in ("/api/ask", "/api/routes", "/api/health", "/api/privacy",
                      "/", "/fallback", "/widget.js", "/widget.css"):
            self.assertIn(route, paths)

    def test_health_advertises_both_ports(self) -> None:
        """A deployment that came up on the wrong port should be obvious at once."""
        body = TestClient(backend_app).get("/api/health").json()
        deployment = body["deployment"]
        self.assertEqual(deployment["api_port"], EXPECTED_API_PORT)
        self.assertEqual(deployment["web_port"], EXPECTED_WEB_PORT)
        self.assertEqual(deployment["api_url"], API_PUBLIC_URL)
        self.assertEqual(deployment["web_url"], WEB_PUBLIC_URL)
        self.assertTrue(deployment["split"])


class TestTheFrontendLoadsNoModel(unittest.TestCase):
    """The reason for the split, measured rather than asserted.

    Checked in a subprocess because the test process has almost certainly already
    imported torch for the other tests, and ``sys.modules`` is global: the honest
    way to ask "did serving a page load the model" is to serve a page in a process
    that has done nothing else.
    """

    def test_serving_a_page_does_not_import_torch(self) -> None:
        script = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from fastapi.testclient import TestClient\n"
            "from app.api import frontend_app\n"
            "c = TestClient(frontend_app)\n"
            "assert c.get('/').status_code == 200\n"
            "assert c.get('/fallback').status_code == 200\n"
            "leaked = [m for m in ('torch', 'sentence_transformers') if m in sys.modules]\n"
            "print(','.join(leaked))\n" % ROOT
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(
            result.stdout.strip(),
            "",
            "the frontend imported the model stack while serving a page",
        )


class TestCorsIsScopedNotOpen(unittest.TestCase):
    """The API has no authentication, so an open CORS policy is an oracle for anyone."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(backend_app)

    def test_the_frontend_origin_is_allowed(self) -> None:
        response = self.client.get("/api/routes", headers={"Origin": WEB_PUBLIC_URL})
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), WEB_PUBLIC_URL
        )

    def test_an_unknown_origin_is_not(self) -> None:
        response = self.client.get("/api/routes", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_the_combined_app_needs_no_cors(self) -> None:
        """Same-origin is the default; a CORS header here would be a needless grant."""
        response = TestClient(app).get("/api/routes", headers={"Origin": WEB_PUBLIC_URL})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_the_allowlist_is_not_a_wildcard(self) -> None:
        self.assertNotIn("*", ALLOWED_ORIGINS)
        self.assertTrue(ALLOWED_ORIGINS)
        for origin in ALLOWED_ORIGINS:
            self.assertIn(str(EXPECTED_WEB_PORT), origin)


class TestForwardedClientAddressing(unittest.TestCase):
    """A page render must not make every visitor share one rate-limit bucket."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(backend_app)

    def test_a_forwarded_address_is_used_as_the_bucket(self) -> None:
        from app.api import _client_key  # noqa: PLC0415

        class _Req:  # the three attributes _client_key reads
            class client:
                host = "127.0.0.1"

            def __init__(self, headers):
                self.headers = headers

        self.assertEqual(
            _client_key(_Req({"x-forwarded-for": "203.0.113.7"})), "203.0.113.7"
        )
        self.assertEqual(
            _client_key(_Req({"x-forwarded-for": "203.0.113.7, 10.0.0.1"})),
            "203.0.113.7",
            "the leftmost address is the original client",
        )

    def test_a_header_from_an_untrusted_peer_is_ignored(self) -> None:
        """Otherwise any caller could pick its own bucket and ignore the limiter."""
        from app.api import _client_key  # noqa: PLC0415

        class _Req:
            class client:
                host = "203.0.113.99"

            def __init__(self, headers):
                self.headers = headers

        self.assertEqual(_client_key(_Req({"x-forwarded-for": "1.2.3.4"})), "203.0.113.99")


class TestTheSplitWidgetConfiguration(unittest.TestCase):
    """The widget has to be told where the API is, without an inline script."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(frontend_app)

    def test_the_page_loads_the_widget_it_never_used_to_load(self) -> None:
        """`/` claimed to be "the assistant, with the widget" and was not.

        It served /widget.js for third parties to embed and mentioned the widget
        in prose, but ran nothing. The README claim was false until now.
        """
        page = self.client.get("/").text
        self.assertIn('src="/widget.js"', page)
        self.assertIn('src="/widget-config.js"', page)
        self.assertLess(
            page.index("/widget-config.js"),
            page.index('src="/widget.js"'),
            "the API location must be set before the widget reads it",
        )

    def test_the_configuration_is_a_file_and_not_an_inline_script(self) -> None:
        """An inline assignment would need 'unsafe-inline' and break the CSP."""
        page = self.client.get("/").text
        self.assertNotIn("<script>", page)
        self.assertNotIn("CU_ROUTE_API", page)

    def test_the_configuration_names_the_api(self) -> None:
        response = self.client.get("/widget-config.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn(API_PUBLIC_URL, response.text)

    def test_the_fallback_page_still_loads_no_scripting(self) -> None:
        """The no-JavaScript guarantee is about /fallback, and must not erode."""
        self.assertNotIn("<script", self.client.get("/fallback").text.lower())


class TestTheSplitContentSecurityPolicy(unittest.TestCase):
    """One cross-origin call is granted, by name, and only where it is needed."""

    def test_the_frontend_allows_exactly_the_api_origin(self) -> None:
        csp = TestClient(frontend_app).get("/").headers["content-security-policy"]
        connect = csp.split("connect-src")[1].split(";")[0]
        self.assertIn("'self'", connect)
        self.assertIn("127.0.0.1:8003", connect)
        script_src = csp.split("script-src")[1].split(";")[0]
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", csp)

    def test_the_api_grants_itself_nothing(self) -> None:
        csp = TestClient(backend_app).get("/api/health").headers["content-security-policy"]
        self.assertIn("connect-src 'self';", csp)

    def test_the_combined_app_is_unaffected(self) -> None:
        csp = TestClient(app).get("/").headers["content-security-policy"]
        self.assertIn("connect-src 'self';", csp)


class TestTheSplitRefusesToInventAnswers(unittest.TestCase):
    """A failure to reach the API must be reported, not rendered as an empty answer."""

    def test_a_question_the_api_cannot_be_reached_is_reported(self) -> None:
        """A refusal must not be rendered as an empty answer.

        Port 1 is not the API. If the hop fails and the page still shows nothing,
        a reader concludes the service has no answer, which is a different and
        false claim from "the service is down".
        """
        broken = create_app(
            mount_api=False, mount_pages=True, ask_locally=False,
            api_base_url="http://127.0.0.1:1",
        )
        page = TestClient(broken).get(
            "/fallback", params={"question": "How do I apply?"}
        ).text

        self.assertIn("Service unavailable", page)
        self.assertIn("Your answer", page, "the reader is told, rather than shown nothing")
        # Scoped to the answer: the directory below legitimately lists what the
        # University does not publish, so the phrase appears on the page either way.
        answer = page.split('<section aria-labelledby="answer-h">')[1].split("</section>")[0]
        self.assertIn("Service unavailable", answer)
        self.assertNotIn("Not published", answer, "a dead API must not read as a refusal")
        self.assertNotIn("Sources", answer, "no citations are invented when the call fails")
        # The question is kept in the box so it can be resubmitted once the API is up.
        self.assertIn("How do I apply?", page)
        # The directory itself is unaffected by the API being down.
        self.assertEqual(page.count('<article class="card">'), 29)

    def test_the_directory_needs_no_api_at_all(self) -> None:
        """The page that the audit was about must survive the API being down."""
        broken = create_app(
            mount_api=False, mount_pages=True, ask_locally=False, api_base_url="http://127.0.0.1:1"
        )
        page = TestClient(broken).get("/fallback").text
        self.assertEqual(page.count('<article class="card">'), 29)
        self.assertIn('name="question"', page)
        self.assertNotIn("Service unavailable", page)


if __name__ == "__main__":
    unittest.main()
