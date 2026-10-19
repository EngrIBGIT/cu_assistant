"""Corpus refresh: compare a proposed snapshot against the committed corpus.

The corpus is a snapshot of published pages, and a stale assistant that answers
confidently is worse than one that admits it does not know, because the user has
no way to detect the staleness. The plan calls for automated snapshot, diff,
delta report, and a human approval queue.

Three of those four are here. The fourth is deliberately absent: this module
never writes to ``data/corpus``. It reads the committed corpus and a proposed
snapshot, reports what differs, and emits a queue of decisions that a human
resolves by editing the corpus and committing. A pipeline that can silently
rewrite what the assistant asserts is a pipeline that can make it wrong
invisibly, so the write step is a person.

The snapshot is supplied rather than fetched. Curation is manual on purpose (see
``docs/architecture.md``: it avoids a terms-of-service question and keeps a human
judgement on what is worth quoting), so "automated snapshot" here means the
comparison and the queue are automated, not the reading of the web.

Nothing in this module imports an embedder, a network client, or the pipeline.
Refreshing the corpus must never load torch, for the same reason serving a page
must not: the work is string comparison over a directory of Markdown.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from .ingest import Corpus

#: Change kinds. A page can be added, removed, have its content changed, or have
#: only its provenance metadata changed while the prose is byte-identical.
ADDED = "added"
REMOVED = "removed"
CONTENT_CHANGED = "content_changed"
METADATA_CHANGED = "metadata_changed"

#: Front-matter fields compared when the body is unchanged. ``source_url`` is the
#: join key, so it is never a "changed field": a page whose URL changed is an
#: added page and a removed page, which is the honest reading.
_METADATA_FIELDS = (
    "source_title",
    "retrieved_at",
    "verification",
    "content_type",
    "routes",
)

#: Pages older than this are reported as stale. A term is longer than the
#: academic term it describes, so a page that has not been re-read in a quarter
#: is the first thing a curator should re-check.
DEFAULT_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class PageChange:
    """One page-level difference between the corpus and a proposed snapshot."""

    kind: str
    source_url: str
    path: str
    before_fingerprint: str | None
    after_fingerprint: str | None
    changed_fields: tuple[str, ...] = ()
    body_diff: str = ""


@dataclass(frozen=True)
class StalePage:
    """A committed page whose retrieval date is old or unreadable."""

    source_url: str
    retrieved_at: str
    age_days: int | None
    reason: str


@dataclass(frozen=True)
class DeltaReport:
    """The full comparison, ready to render or to serialise for a reviewer."""

    generated_at: str
    now: str
    max_age_days: int
    current_documents: int
    snapshot_documents: int
    changes: tuple[PageChange, ...]
    stale: tuple[StalePage, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def counts(self) -> dict[str, int]:
        result = {ADDED: 0, REMOVED: 0, CONTENT_CHANGED: 0, METADATA_CHANGED: 0}
        for change in self.changes:
            result[change.kind] += 1
        return result


def _changed_fields(before: Any, after: Any) -> tuple[str, ...]:
    """Provenance fields that differ between two versions of the same page."""
    return tuple(
        name
        for name in _METADATA_FIELDS
        if getattr(before, name) != getattr(after, name)
    )


def _body_diff(before: Any, after: Any, context: int) -> str:
    """Unified diff of the two bodies, so a reviewer reads the change, not the page."""
    diff = difflib.unified_diff(
        before.body.splitlines(),
        after.body.splitlines(),
        fromfile=f"{before.source_url} (retrieved {before.retrieved_at or 'unknown'})",
        tofile=f"{after.source_url} (proposed snapshot)",
        lineterm="",
        n=context,
    )
    return "\n".join(diff)


def diff_corpus(
    current: Corpus, snapshot: Corpus, *, diff_context: int = 1
) -> tuple[PageChange, ...]:
    """Classify every page-level difference, sorted by source URL.

    A page is keyed by ``source_url`` rather than by filename: the URL is what a
    citation resolves to, and a rename in the corpus that leaves the URL alone is
    not a change to what the assistant asserts.
    """
    current_by_url = current.by_url
    snapshot_by_url = snapshot.by_url

    changes: list[PageChange] = []
    for url in sorted(set(current_by_url) | set(snapshot_by_url)):
        before = current_by_url.get(url)
        after = snapshot_by_url.get(url)

        if before is None:
            changes.append(
                PageChange(ADDED, url, after.path, None, after.fingerprint)
            )
        elif after is None:
            changes.append(
                PageChange(REMOVED, url, before.path, before.fingerprint, None)
            )
        elif before.sha256 != after.sha256:
            changes.append(
                PageChange(
                    CONTENT_CHANGED,
                    url,
                    after.path,
                    before.fingerprint,
                    after.fingerprint,
                    changed_fields=_changed_fields(before, after),
                    body_diff=_body_diff(before, after, diff_context),
                )
            )
        else:
            fields = _changed_fields(before, after)
            if fields:
                changes.append(
                    PageChange(
                        METADATA_CHANGED,
                        url,
                        after.path,
                        before.fingerprint,
                        after.fingerprint,
                        changed_fields=fields,
                    )
                )

    return tuple(changes)


def _age_days(retrieved_at: str, now: datetime) -> int | None:
    """Days since a retrieval date, or ``None`` when it cannot be read.

    An unreadable date is not treated as fresh: a page whose provenance cannot be
    parsed is a page whose freshness cannot be confirmed, and the refresh report
    says exactly that rather than assuming the best case.
    """
    try:
        retrieved = date.fromisoformat(retrieved_at.strip()[:10])
    except (ValueError, AttributeError):
        return None
    return (now.date() - retrieved).days


def find_stale(
    corpus: Corpus, *, now: datetime, max_age_days: int
) -> tuple[StalePage, ...]:
    """Committed pages whose retrieval date is old, or missing, as at ``now``."""
    stale: list[StalePage] = []
    for document in corpus.documents:
        age = _age_days(document.retrieved_at, now)
        if age is None:
            stale.append(
                StalePage(
                    document.source_url,
                    document.retrieved_at,
                    None,
                    "retrieved_at missing or unreadable; freshness cannot be confirmed",
                )
            )
        elif age > max_age_days:
            stale.append(
                StalePage(
                    document.source_url,
                    document.retrieved_at,
                    age,
                    f"retrieved {age} days ago, older than {max_age_days}",
                )
            )
    return tuple(sorted(stale, key=lambda page: page.source_url))


def build_report(
    current: Corpus,
    snapshot: Corpus,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    diff_context: int = 1,
) -> DeltaReport:
    """Compare the corpus against a snapshot and gather staleness in one report."""
    moment = now or datetime.now(timezone.utc)
    return DeltaReport(
        generated_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=moment.date().isoformat(),
        max_age_days=max_age_days,
        current_documents=len(current.documents),
        snapshot_documents=len(snapshot.documents),
        changes=diff_corpus(current, snapshot, diff_context=diff_context),
        stale=find_stale(current, now=moment, max_age_days=max_age_days),
    )


def render_text(report: DeltaReport) -> str:
    """A human-readable delta report, for a terminal or an evidence pack."""
    counts = report.counts()
    lines: list[str] = [
        "CU Route Assistant - corpus refresh delta",
        "=" * 66,
        f"  committed corpus : {report.current_documents} pages",
        f"  proposed snapshot: {report.snapshot_documents} pages",
        f"  generated at     : {report.generated_at}",
        "",
        f"  added            : {counts[ADDED]}",
        f"  removed          : {counts[REMOVED]}",
        f"  content changed  : {counts[CONTENT_CHANGED]}",
        f"  metadata changed : {counts[METADATA_CHANGED]}",
        "",
    ]

    if report.changes:
        lines.append("CHANGES PENDING HUMAN APPROVAL")
        lines.append("-" * 66)
        for change in report.changes:
            lines.append(f"  [{change.kind}] {change.source_url}")
            if change.changed_fields:
                lines.append(f"      fields: {', '.join(change.changed_fields)}")
            for diff_line in change.body_diff.splitlines():
                lines.append(f"      {diff_line}")
        lines.append("")
    else:
        lines.append("  No page differs from the committed corpus.")
        lines.append("")

    if report.stale:
        lines.append(
            f"STALE PAGES (older than {report.max_age_days} days as at {report.now})"
        )
        lines.append("-" * 66)
        for page in report.stale:
            lines.append(f"  {page.source_url}")
            lines.append(f"      {page.reason}")
        lines.append("")
    else:
        lines.append(
            f"  No page is older than {report.max_age_days} days as at {report.now}."
        )
        lines.append("")

    lines.append(
        "Nothing here is applied automatically. Edit data/corpus by hand and commit."
    )
    return "\n".join(lines)


#: The rule the queue exists to enforce, printed into every queue file so that a
#: reader of the artefact alone knows the write step is a person.
APPROVAL_POLICY = (
    "Nothing in this queue is applied automatically. A human reviews each item, "
    "reads the cited page, edits data/corpus by hand, and commits. The refresh "
    "pipeline is read-only by construction: a pipeline that can silently rewrite "
    "what the assistant asserts is a pipeline that can make it wrong invisibly."
)


def report_to_dict(report: DeltaReport) -> dict[str, Any]:
    """Machine-readable form of the whole report, for ``delta.json``."""
    return {
        "schema_version": "1.0.0",
        "generated_at": report.generated_at,
        "now": report.now,
        "max_age_days": report.max_age_days,
        "current_documents": report.current_documents,
        "snapshot_documents": report.snapshot_documents,
        "counts": report.counts(),
        "changes": [asdict(change) for change in report.changes],
        "stale": [asdict(page) for page in report.stale],
    }


def approval_queue(report: DeltaReport) -> dict[str, Any]:
    """The decisions a human must resolve, each one ``pending`` by construction."""
    return {
        "schema_version": "1.0.0",
        "generated_at": report.generated_at,
        "now": report.now,
        "max_age_days": report.max_age_days,
        "policy": APPROVAL_POLICY,
        "counts": report.counts(),
        "pending": [
            {
                "decision": "pending",
                "kind": change.kind,
                "source_url": change.source_url,
                "changed_fields": list(change.changed_fields),
                "current_fingerprint": change.before_fingerprint,
                "snapshot_fingerprint": change.after_fingerprint,
            }
            for change in report.changes
        ],
        "stale": [
            {
                "decision": "pending",
                "source_url": page.source_url,
                "retrieved_at": page.retrieved_at,
                "age_days": page.age_days,
                "reason": page.reason,
            }
            for page in report.stale
        ],
    }
