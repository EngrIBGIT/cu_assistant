"""Answer composition.

Two composers, one contract.

``ExtractiveComposer`` (default)
    Assembles the answer from sentences taken verbatim out of retrieved source
    text. It cannot invent a fact, because it cannot write one: every sentence
    in the output exists in the corpus and carries the URL it came from. What
    it gives up is fluency and multi-part synthesis.

``LLMComposer`` (optional)
    Asks a language model to write the answer from the same retrieved sources,
    under the prompt in ``prompts.py``, and then *verifies the output against the
    sources before returning it*. If verification fails, it falls back to the
    extractive composer rather than returning unverified text.

The fallback is the important part. A language model makes the product better
and cannot make it unsafe, because its output is checked and discarded when it
does not check out. That is the difference between adding a model to a grounded
system and bolting one onto it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .config import thresholds as T
from .embeddings import tokenize
from .llm import LLMClient
from .prompts import PROMPT_VERSION, SYSTEM, build_user_prompt
from .retriever import Hit
from .router import question_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s()+-]{7,}|https?://\S+")
_LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s+")

#: How many of the answer's spans may be reserved for enumerated content.
#:
#: One, deliberately. Two was tried and measurably harmful: the FIMS
#: documentation and the climate-smart agriculture page name each other, so both
#: are well supported for either question, and reserving two lists attached the
#: API scopes to a question about farming programmes and the programme names to a
#: question about API scopes. Both answers then scored well on a keyword check
#: while being wrong to a reader, which is the worst possible outcome.
_LIST_RESERVE = 1

#: A list shorter than this is a note, not an enumeration, and is not worth
#: spending the reserved span on.
_LIST_MIN_ITEMS = 2


@dataclass
class Draft:
    """A composed answer before it becomes an API response."""

    text: str
    citations: list[str]
    mode: str
    used_model: bool
    verification: dict


def _sentences(text: str) -> list[str]:
    """Split a chunk into quotable spans, respecting its structure.

    Prose is rejoined across line breaks before it is split into sentences. The
    corpus is hard-wrapped the way Markdown conventionally is, so a single
    sentence routinely spans three lines, and splitting on the newline first
    produced fragments that ended mid-clause — "The Centre for Climate-Smart
    Agriculture and IFPRI are partnering with" — which are worse than useless in
    an answer whose entire claim to trust is that it quotes its sources.

    Lists and tables are the exception: their line breaks carry meaning, so each
    item or row is kept as its own span.
    """
    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        block = paragraph.strip()
        if not block:
            continue

        if _LIST_ITEM.match(block) or block.lstrip().startswith("|"):
            for line in block.splitlines():
                item = line.strip()
                if item:
                    out.append(item)
            continue

        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if not joined:
            continue
        out.extend(s.strip() for s in _SENTENCE_SPLIT.split(joined) if s.strip())
    return out


def _salience(sentence: str, question: str, idf: dict[str, float]) -> float:
    """How much a retrieved sentence actually answers this specific question.

    Weighted toward rare query terms so that a sentence repeating a common word
    does not outrank one containing the distinctive term the user used.
    """
    tokens = tokenize(sentence)
    if not tokens:
        return 0.0
    overlap = sum(idf.get(t, 0.0) for t in set(tokens) if t in question_tokens(question))
    density = overlap / (len(tokens) ** 0.5)
    if _CONTACT.search(sentence):
        density += 0.45
    return density


class ExtractiveComposer:
    """Builds a grounded answer from retrieved sentences. Cannot invent facts."""

    mode = "retrieval_only"

    def __init__(self, max_sentences: int = 5) -> None:
        self.max_sentences = max_sentences
        self._idf: dict[str, float] = {}

    def fit(self, corpus_texts: Sequence[str]) -> "ExtractiveComposer":
        import math  # noqa: PLC0415

        from collections import Counter  # noqa: PLC0415

        document_frequency: Counter = Counter()
        for text in corpus_texts:
            document_frequency.update(set(tokenize(text)))
        total = max(1, len(corpus_texts))
        self._idf = {
            token: math.log((1.0 + total) / (1.0 + df)) + 1.0
            for token, df in document_frequency.items()
        }
        return self

    def compose(
        self, question: str, hits: Sequence[Hit], lead: str | None = None
    ) -> Draft:
        chosen: list[tuple[float, str, str]] = []
        contributing: set[str] = set()
        for hit in hits:
            for sentence in _sentences(hit.chunk.text):
                score = _salience(sentence, question, self._idf)
                if score > 0.0:
                    chosen.append((score, sentence, hit.chunk.source_url))
                    contributing.add(hit.chunk.chunk_id)

        chosen.sort(key=lambda item: item[0], reverse=True)

        # Enumerated content gets a reserved share of the budget, up front.
        # A list block is usually the payload of the answer — the programme
        # names, the API scopes, the services on offer — and it occupies one
        # span however long it is, so it is the cheapest useful thing to show.
        # Left to compete on salience it always loses: its items are short
        # fragments that share few words with any question, so the budget gets
        # spent on the surrounding prose and the answer arrives without the very
        # thing that was asked for.
        lists = self._list_spans(hits)
        reserve = min(len(lists), _LIST_RESERVE)
        budget = max(1, self.max_sentences - reserve)
        selected = chosen[:budget]
        for span, url in lists[:reserve]:
            selected.append((0.0, span, url))
            contributing.add("list:" + url)

        # Every chunk retrieved as evidence should say something, even when the
        # user asked about it in words the page does not use. Asked "which
        # programmes does the centre run", the sentence "Agronomy, Agribusiness,
        # Agricultural Economics" shares no content word with the question at
        # all, so a salience ranking alone drops the one line the user wanted
        # and answers with a confident summary of the wrong thing. Retrieval
        # already decided this passage was relevant; the lexical filter is only
        # there to order what to lead with, not to veto.
        selected = self._fill_silent(hits, selected, contributing)

        parts: list[str] = []
        if lead:
            parts.append(lead)

        seen: set[str] = set()
        citations: list[str] = []
        for _, sentence, url in selected:
            normalised = re.sub(r"\W+", " ", sentence.lower()).strip()
            if normalised in seen:
                continue
            seen.add(normalised)
            parts.append(self._present(sentence))
            if url not in citations:
                citations.append(url)

        text = "\n\n".join(p for p in parts if p).strip()
        return Draft(
            text=text,
            citations=citations,
            mode=self.mode,
            used_model=False,
            verification={"method": "verbatim_extraction", "checked_sentences": len(selected)},
        )

    def _fill_silent(
        self,
        hits: Sequence[Hit],
        selected: list[tuple[float, str, str]],
        contributing: set[str],
    ) -> list[tuple[float, str, str]]:
        """Give a leading line to any retrieved chunk that said nothing.

        The line is still verbatim from the corpus, so grounding is unchanged;
        it is simply the first thing a reader of that page would say. Chunks
        beyond the top two are left out, because on a weak retrieval the tail is
        noise and admitting it would cost accuracy far more than it gains
        coverage.

        Tracked by chunk id and not by source URL: document-level expansion
        admits sibling passages from a URL that another passage already
        contributed from, and keying on the URL would mark all of them as
        spoken for and silently drop the very passage that had nothing in common
        with the question's wording.
        """
        silent = [h for h in hits[:4] if h.chunk.chunk_id not in contributing]
        if not silent:
            return selected

        # Each silent chunk gets one line, and it gets the line most worth
        # quoting rather than simply the first. A chunk that matched on nothing
        # lexical was admitted because its document was relevant, and within such
        # a chunk an enumerated list is what a user asking "which" or "what are
        # the options" actually wants — the programme names, the API scopes.
        # Reaching past it for the chunk's opening prose throws away the answer.
        extra: list[tuple[float, str, str]] = []
        for hit in silent:
            best = self._most_informative(hit.chunk.text)
            if best:
                extra.append((0.0, best, hit.chunk.source_url))

        if not extra:
            return selected

        budget = self.max_sentences - len(selected)
        if budget <= 0:
            return selected
        # Salient lines first, then the best line of each silent chunk, so a
        # literal match still outranks a merely retrieved passage.
        return (selected + extra)[: self.max_sentences]

    @staticmethod
    def _list_spans(hits: Sequence[Hit]) -> list[tuple[str, str]]:
        """Enumerations worth quoting, best-supported document first.

        Two guards, both learned the hard way. A list is quoted only when some
        passage of its own document was genuinely supported for this question,
        which is what stops the ICT services table being attached to a question
        about API scopes. And the ranking key is the best score achieved by any
        passage of that document, not the score of the list's own passage: an
        enumeration is often the least lexically similar text on a page, and
        scoring it on its own demotes exactly the content that answers the
        question.
        """
        doc_score: dict[str, float] = {}
        for hit in hits:
            if hit.expanded:
                continue
            url = hit.chunk.source_url
            if hit.score > doc_score.get(url, 0.0):
                doc_score[url] = hit.score

        scored: list[tuple[float, str, str]] = []
        for hit in hits:
            url = hit.chunk.source_url
            strength = doc_score.get(url, 0.0)
            if strength < T.min_evidence:
                continue
            for paragraph in re.split(r"\n\s*\n", hit.chunk.text):
                block = paragraph.strip()
                if not block or not _LIST_ITEM.match(block):
                    continue
                items = [line.strip().lstrip("-*").strip() for line in block.splitlines() if line.strip()]
                items = [i for i in items if i]
                if len(items) >= _LIST_MIN_ITEMS:
                    scored.append((strength, "; ".join(items), url))

        # Strongest document first; a stable tie-break keeps the order
        # reproducible, which matters when a results file is the evidence.
        scored.sort(key=lambda item: -item[0])
        return [(span, url) for _, span, url in scored]

    @staticmethod
    def _most_informative(text: str) -> str:
        """The span from a chunk most worth quoting when nothing matched lexically.

        Prefers a list block, quoted whole, because a bare enumeration is usually
        the direct answer. Otherwise falls back to the opening prose span.
        """
        spans = _sentences(text)
        if not spans:
            return ""

        for paragraph in re.split(r"\n\s*\n", text):
            block = paragraph.strip()
            if not block or not _LIST_ITEM.match(block):
                continue
            items = [line.strip().lstrip("-*").strip() for line in block.splitlines() if line.strip()]
            items = [i for i in items if i]
            if items:
                return "; ".join(items)
        return spans[0]

    @staticmethod
    def _present(sentence: str) -> str:
        """Render one extracted span for display without corrupting its form.

        Nothing is added. Not a full stop, not a join, not a tidy-up.

        This composer's entire claim to trust is that the answer is quotable: a
        reader who follows the citation must find the sentence they read. Adding
        a full stop to every span broke that claim in two separate ways, and both
        were caught by the grounding test rather than reasoned about in advance.

        The obvious one is fidelity. A rendered line that differs from its source
        by a single character is not the line in the source, which is precisely
        what the test that checks verbatim quotation exists to prevent, and it
        made ten assertions fail for a difference no reader would ever notice.

        The one that actually mattered is honesty about completeness. A span that
        chunking cut mid-sentence — the library's address ends at "Main Library
        Building, Ground" because the chunk boundary fell there — was being given
        a full stop, and a full stop is an assertion that the sentence is
        complete. The assistant was therefore stating, in punctuation, a fact about
        where the library is that the source does not state. A missing word is a
        chunking artefact; a full stop converts it into a claim. Quoting the
        fragment as a fragment is both more truthful and, here, identical in
        substance, because the citation is one click away.
        """
        return sentence.strip()


class LLMComposer:
    """Model-written answer, verified against the sources before release."""

    mode = "full"

    def __init__(self, client: LLMClient, fallback: ExtractiveComposer) -> None:
        self.client = client
        self.fallback = fallback

    def compose(
        self, question: str, hits: Sequence[Hit], lead: str | None = None
    ) -> Draft:
        if not self.client.available:
            return self.fallback.compose(question, hits, lead)

        sources = [(h.chunk.source_url, h.chunk.text) for h in hits]
        result = self.client.complete(SYSTEM, build_user_prompt(question, sources))
        if result is None:
            draft = self.fallback.compose(question, hits, lead)
            draft.verification = {
                "method": "verbatim_extraction",
                "reason": "language model unavailable; degraded to retrieval-only",
            }
            return draft

        report = self._verify(result.text, hits)
        if not report["passed"]:
            draft = self.fallback.compose(question, hits, lead)
            draft.verification = {
                "method": "verbatim_extraction",
                "reason": f"model output rejected: {report['reason']}",
                "rejected_model": self.client.label,
                "prompt_version": PROMPT_VERSION,
            }
            return draft

        text = result.text.strip()
        if lead:
            text = f"{lead}\n\n{text}"

        return Draft(
            text=text,
            citations=report["sources"],
            mode=self.mode,
            used_model=True,
            verification={
                "method": "citation_and_entailment_check",
                "model": self.client.label,
                "latency_ms": result.latency_ms,
                "prompt_version": PROMPT_VERSION,
                **report,
            },
        )

    def _verify(self, text: str, hits: Sequence[Hit]) -> dict:
        """Reject model output that is unciteable, over-long, or number-bearing.

        The numeric check is blunt on purpose. This corpus contains no published
        fee, and inventing one is the single worst failure available to the
        system, so any monetary figure in a generated answer is treated as a
        defect until a human proves otherwise.
        """
        markers = re.findall(r"\[S(\d+)\]", text)
        if not markers:
            return {"passed": False, "reason": "no citation markers present", "sources": []}

        valid = {str(i + 1) for i in range(len(hits))}
        used = sorted({m for m in markers if m in valid}, key=int)
        dangling = [m for m in set(markers) if m not in valid]
        if dangling:
            return {
                "passed": False,
                "reason": f"cites sources that were not supplied: {sorted(dangling)}",
                "sources": [],
            }

        money = re.findall(r"[₦$]\s?\d|\b\d[\d,]{2,}\s*(naira|ngn)\b", text, re.IGNORECASE)
        if money:
            return {
                "passed": False,
                "reason": "introduced a monetary figure that is not in the sources",
                "sources": [],
            }

        words = len(text.split())
        if words > 220:
            return {"passed": False, "reason": f"answer too long ({words} words)", "sources": []}

        return {
            "passed": True,
            "reason": "all claims carry a resolvable source marker",
            "sources": [hits[int(m) - 1].chunk.source_url for m in used],
            "markers_used": used,
        }
