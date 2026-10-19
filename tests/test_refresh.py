"""Tests for the corpus refresh pipeline.

The pipeline exists because the corpus is a snapshot and will go stale, and a
stale assistant that answers confidently is worse than one that admits it does
not know. What it must do is compare, report, and queue; what it must never do is
write. Every test here is either proving a classification is right or proving the
committed corpus was left alone, because those are the two ways this feature can
be harmful: mislabel a change, or apply one without a person seeing it.

Run with:

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.ingest import load_corpus
from app.refresh import (
    ADDED,
    CONTENT_CHANGED,
    METADATA_CHANGED,
    REMOVED,
    approval_queue,
    build_report,
    diff_corpus,
    find_stale,
    render_text,
    report_to_dict,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"

_AT = datetime(2026, 12, 1, tzinfo=timezone.utc)

_BODY = (
    "The university switchboard answers general enquiries during working hours "
    "on weekdays, and forwards a caller to the office that owns the question."
)


def _page(
    *,
    url: str,
    body: str = _BODY,
    retrieved: str = "2026-09-26",
    verification: str = "published",
    title: str = "Example | Cosmopolitan University",
    ctype: str = "indexer_rendered",
    route: str = "R-SWITCHBOARD",
) -> str:
    return (
        "---\n"
        f"source_url: {url}\n"
        f'source_title: "{title}"\n'
        f"retrieved_at: {retrieved}\n"
        f"verification: {verification}\n"
        f"content_type: {ctype}\n"
        f"routes: [{route}]\n"
        "---\n\n"
        f"{body}\n"
    )


def _write(directory: Path, name: str, page: str) -> None:
    (directory / name).write_text(page, encoding="utf-8")


def _tree_hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob("*.md"))
    }


class TestChangeClassification(unittest.TestCase):
    """A change is reported as exactly one kind, and the join key is the URL."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.current_dir = self.root / "corpus"
        self.snapshot_dir = self.root / "snapshot"
        self.current_dir.mkdir()
        self.snapshot_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _diff(self):
        return diff_corpus(load_corpus(self.current_dir), load_corpus(self.snapshot_dir))

    def test_an_identical_corpus_reports_nothing(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a"))
        _write(self.snapshot_dir, "a.md", _page(url="https://x/a"))
        self.assertEqual(self._diff(), ())

    def test_a_page_only_in_the_snapshot_is_added(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a"))
        _write(self.snapshot_dir, "a.md", _page(url="https://x/a"))
        _write(self.snapshot_dir, "b.md", _page(url="https://x/b"))
        (change,) = self._diff()
        self.assertEqual(change.kind, ADDED)
        self.assertEqual(change.source_url, "https://x/b")
        self.assertIsNone(change.before_fingerprint)
        self.assertIsNotNone(change.after_fingerprint)

    def test_a_page_only_in_the_corpus_is_removed(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a"))
        _write(self.current_dir, "b.md", _page(url="https://x/b"))
        _write(self.snapshot_dir, "a.md", _page(url="https://x/a"))
        (change,) = self._diff()
        self.assertEqual(change.kind, REMOVED)
        self.assertEqual(change.source_url, "https://x/b")
        self.assertIsNotNone(change.before_fingerprint)
        self.assertIsNone(change.after_fingerprint)

    def test_a_changed_body_is_a_content_change_with_a_diff(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a", body=_BODY))
        _write(
            self.snapshot_dir,
            "a.md",
            _page(url="https://x/a", body="The switchboard has moved to a new office."),
        )
        (change,) = self._diff()
        self.assertEqual(change.kind, CONTENT_CHANGED)
        self.assertIn("-" + _BODY[:20], change.body_diff)
        self.assertIn("+The switchboard", change.body_diff)
        self.assertNotEqual(change.before_fingerprint, change.after_fingerprint)

    def test_a_retrieval_date_change_is_metadata_only(self) -> None:
        """A page re-read and confirmed unchanged is the common refresh case."""
        _write(self.current_dir, "a.md", _page(url="https://x/a", retrieved="2026-01-01"))
        _write(self.snapshot_dir, "a.md", _page(url="https://x/a", retrieved="2026-12-01"))
        (change,) = self._diff()
        self.assertEqual(change.kind, METADATA_CHANGED)
        self.assertEqual(change.changed_fields, ("retrieved_at",))
        self.assertEqual(change.before_fingerprint, change.after_fingerprint)
        self.assertEqual(change.body_diff, "")

    def test_a_route_change_is_metadata_only(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a", route="R-SWITCHBOARD"))
        _write(self.snapshot_dir, "a.md", _page(url="https://x/a", route="R-LIBRARY"))
        (change,) = self._diff()
        self.assertEqual(change.kind, METADATA_CHANGED)
        self.assertEqual(change.changed_fields, ("routes",))

    def test_a_content_change_reports_the_fields_that_also_moved(self) -> None:
        _write(self.current_dir, "a.md", _page(url="https://x/a", verification="published"))
        _write(
            self.snapshot_dir,
            "a.md",
            _page(url="https://x/a", body="A replacement sentence with new content here.", verification="derived"),
        )
        (change,) = self._diff()
        self.assertEqual(change.kind, CONTENT_CHANGED)
        self.assertIn("verification", change.changed_fields)

    def test_changes_are_sorted_by_url(self) -> None:
        _write(self.current_dir, "base.md", _page(url="https://x/base"))
        _write(self.snapshot_dir, "base.md", _page(url="https://x/base"))
        for url in ("https://x/c", "https://x/a", "https://x/b"):
            _write(self.snapshot_dir, url.rsplit("/", 1)[1] + ".md", _page(url=url))
        changes = self._diff()
        self.assertEqual([c.source_url for c in changes], ["https://x/a", "https://x/b", "https://x/c"])


class TestStaleness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.directory.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_recent_page_is_not_stale(self) -> None:
        _write(self.directory, "a.md", _page(url="https://x/a", retrieved="2026-11-01"))
        stale = find_stale(load_corpus(self.directory), now=_AT, max_age_days=90)
        self.assertEqual(stale, ())

    def test_an_old_page_is_stale_with_its_age(self) -> None:
        _write(self.directory, "a.md", _page(url="https://x/a", retrieved="2026-01-01"))
        (page,) = find_stale(load_corpus(self.directory), now=_AT, max_age_days=90)
        self.assertEqual(page.source_url, "https://x/a")
        self.assertEqual(page.age_days, 334)

    def test_an_unreadable_date_is_not_treated_as_fresh(self) -> None:
        """The honest failure mode is "cannot confirm", not "assume current"."""
        _write(self.directory, "a.md", _page(url="https://x/a", retrieved=""))
        (page,) = find_stale(load_corpus(self.directory), now=_AT, max_age_days=90)
        self.assertIsNone(page.age_days)
        self.assertIn("cannot be confirmed", page.reason)


class TestTheReportAndQueue(unittest.TestCase):
    def _report(self) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "corpus"
            snapshot = root / "snapshot"
            current.mkdir()
            snapshot.mkdir()
            for url in ("https://x/a", "https://x/b"):
                _write(current, url.rsplit("/", 1)[1] + ".md",
                       _page(url=url, retrieved="2026-01-01"))
                _write(snapshot, url.rsplit("/", 1)[1] + ".md",
                       _page(url=url, retrieved="2026-11-01"))
            _write(snapshot, "c.md", _page(url="https://x/c"))
            return build_report(
                load_corpus(current), load_corpus(snapshot), now=_AT, max_age_days=90
            )

    def test_the_counts_add_up(self) -> None:
        report = self._report()
        counts = report.counts()
        self.assertEqual(counts[ADDED], 1)
        self.assertEqual(counts[CONTENT_CHANGED], 0)
        self.assertEqual(counts[METADATA_CHANGED], 2)
        self.assertEqual(counts[REMOVED], 0)

    def test_staleness_is_reported_against_the_committed_corpus(self) -> None:
        report = self._report()
        self.assertEqual(len(report.stale), 2)

    def test_every_change_in_the_queue_is_pending(self) -> None:
        queue = approval_queue(self._report())
        self.assertTrue(queue["pending"])
        for item in queue["pending"]:
            self.assertEqual(item["decision"], "pending")

    def test_the_queue_states_that_nothing_is_applied_automatically(self) -> None:
        queue = approval_queue(self._report())
        self.assertIn("Nothing in this queue is applied automatically", queue["policy"])

    def test_the_queue_carries_both_fingerprints(self) -> None:
        queue = approval_queue(self._report())
        for item in queue["pending"]:
            self.assertIn("current_fingerprint", item)
            self.assertIn("snapshot_fingerprint", item)

    def test_the_rendered_report_names_every_change(self) -> None:
        text = render_text(self._report())
        self.assertIn("https://x/c", text)
        self.assertIn("PENDING HUMAN APPROVAL", text)
        self.assertIn("Nothing here is applied automatically", text)

    def test_the_report_serialises_to_json(self) -> None:
        payload = report_to_dict(self._report())
        self.assertEqual(json.loads(json.dumps(payload))["counts"][ADDED], 1)


class TestThePipelineNeverWritesToTheCorpus(unittest.TestCase):
    """The whole safety property, checked against the real committed corpus."""

    def test_building_a_report_leaves_every_committed_page_byte_identical(self) -> None:
        before = _tree_hashes(CORPUS)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            _write(snapshot, "new.md", _page(url="https://x/new"))
            report = build_report(load_corpus(CORPUS), load_corpus(snapshot), now=_AT)
            approval_queue(report)
            render_text(report)
        self.assertEqual(_tree_hashes(CORPUS), before)


class TestTheRefreshCli(unittest.TestCase):
    def test_the_cli_writes_a_report_and_leaves_the_corpus_untouched(self) -> None:
        before = _tree_hashes(CORPUS)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            snapshot = tmp_path / "snapshot"
            snapshot.mkdir()
            _write(snapshot, "new.md", _page(url="https://x/new"))
            out = tmp_path / "out"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "refresh_corpus.py"),
                    "--snapshot",
                    str(snapshot),
                    "--out",
                    str(out),
                    "--now",
                    "2026-12-01T00:00:00",
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn("corpus refresh delta", result.stdout)

            written = sorted(out.rglob("*"))
            names = {path.name for path in written if path.is_file()}
            self.assertEqual(names, {"delta.txt", "delta.json", "approval_queue.json"})

            queue = json.loads((out.rglob("approval_queue.json")).__next__().read_text("utf-8"))
            self.assertTrue(any(item["source_url"] == "https://x/new" for item in queue["pending"]))

        self.assertEqual(_tree_hashes(CORPUS), before, "the CLI wrote to the corpus")

    def test_the_cli_refuses_a_missing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "refresh_corpus.py"),
                    "--snapshot",
                    str(Path(tmp) / "does-not-exist"),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=300,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("snapshot directory not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
