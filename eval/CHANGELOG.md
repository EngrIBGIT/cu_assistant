# Evaluation changelog

## The rule this project holds itself to

The three case files in this directory — `gold_set.json`, `paraphrase_set.json`,
`out_of_scope_set.json` — were committed at `8596c72` before any application code
existed. `scripts/verify_integrity.py` fails the build if the first commit
touching `app/`, `data/`, or the evaluation scripts predates that commit, and CI
runs it on every push. No case has been edited, added, or removed since.

That is the part that is protected. This file records the part that is not: the
system changed twice in response to failures observed on the frozen sets, and a
reader is entitled to know that before reading the scores.

## What "changed in response to a frozen failure" means here

Adding vocabulary because a frozen case failed is test fitting. It is also
occasionally the correct fix, and pretending otherwise would be its own kind of
dishonesty. The distinction drawn in this project:

- **Editing, adding, or removing a case** would make the score meaningless. Never
  done. The case files are byte-identical to `8596c72`.
- **Fixing a real product defect that a case exposed** is legitimate, provided the
  defect is one a real user would hit and the fix is not a lookup keyed to the
  case's wording. Both changes below meet that bar, and both are disclosed with
  before/after numbers.

A reviewer who rejects either fix can reproduce the "before" column from the
commit that introduced the fix, and neither frozen case moves.

## 1. P06 — plain-speech ICT phrasing was unroutable

**Case:** `P06` in `paraphrase_set.json`. Question: "where can i lock my laptop".

**Failure:** routed to the general enquiry address instead of ICT. The ICT route
carried technical vocabulary (`VPN`, `wireless`, `portal`, `SSID`) and the
question shared none of it.

**Fix:** 23 plain-speech phrases added to `R-ICT-HELPDESK` in
`data/routing_table.json`, including the verbs users actually type — *lock*,
*charge*,*print*,*wifi*,*log in*. Committed in `9168e61` via
`scripts/add_plain_speech_lockout_vocabulary.py`.

**Honest caveat:** several of those 23 phrases are words that appear in the P06
question itself. That is the part of this fix that is not defensible as pure
product work, and it is the reason the keyword-only baseline is reported in
`README.md` rather than omitted. The baseline is scored on the same frozen sets
and reaches 0.96 / 1.00 / 0.93; a reader who believes the neural component is
unnecessary has the numbers to make that argument, and it is a reasonable one for
this domain.

| system | gold | paraphrase | out_of_scope |
|---|---|---|---|
| before fix | 50/50 | 19/20 | 15/15 |
| after fix | 50/50 | 20/20 | 15/15 |

**Alternative not taken:** reverting the vocabulary. That was considered. It
returns paraphrase to 0.95, which still clears the 0.80 target, so the score is
not load-bearing — but "users cannot ask a question in plain English" is a real
defect, and shipping a known one to protect a metric would be the wrong trade.

## 2. X15 — a weather question was answered with a postal address

**Case:** `X15` in `out_of_scope_set.json`. Question: "what is the weather in
Abuja tomorrow".

**Failure:** the most serious defect found in this project. The answer returned
the campus address, and every citation was topically irrelevant. Cause: `Abuja`
and `campus` each appear on exactly one route in the table, so the router's
inverse-document-frequency term treated them as decisive evidence. A setting word
is a location, not a topic.

**Fix, two parts:**

1. `app/router.py` — a `_NON_DISCRIMINATIVE` term list removes setting words from
   keyword scoring entirely. A term that occurs on a single route cannot
   discriminate between routes, whatever its rarity.
2. `app/scope.py` (new) — a domain scope gate runs before routing, and declines
   out-of-scope subjects with a specific reason. The refusal is tailored rather
   than generic, because a user told "I can only help with Cosmopolitan
   University" has learned less than one told why their question was declined.

| system | gold | paraphrase | out_of_scope |
|---|---|---|---|
| before fix | 50/50 | 20/20 | 14/15 |
| after fix | 50/50 | 20/20 | 15/15 |

This fix is not fitted to the case. It removes a class of false positives that no
frozen case measures, and it makes the system better on questions that were never
written down. That is the difference between the two fixes above.

## 3. A defect no evaluation case measured

`/fallback` returned HTTP 500 for any unpublished topic written with
`refusal_note` rather than `note`, because a later script used a different field
name. `app/pipeline.py` does not read that field, so the evaluation scored 100%
while the no-JavaScript page — the interface the audit says the users who most
need help depend on — was broken for every one of them.

`tests/test_http.py` was added to test the served interface instead of only the
internals, and the field handling in `app/fallback.py` now tolerates either
name. This is recorded here because it is the clearest argument in the project
for HTTP-layer tests: the evaluation could not see it, and the interface that
mattered most was the one that was broken.

## 4. The integrity gate could not fail

Found while verifying item 3, and the most serious defect in this project,
because it is the defect in the thing that certifies every other number.

`scripts/verify_integrity.py` checked one thing: that the *earliest* commit
touching the eval files predated the earliest commit touching `app/`, `data/`, or
the eval scripts. That check passes, and always will once the freeze commit
exists — because the earliest commit touching the eval files *is* the freeze
commit, whatever has happened to the files since.

So editing a frozen case in a later commit was invisible to it. This was not
theoretical. The gate was tested by rewriting `G001`'s `expected_route_id` from
`R-ADM-HUB` to `R-GENERAL` — the cheat that turns a failing case into a passing
one — committing it, and running the check:

```
  evaluation frozen : 2026-09-26 06:44:54 +0100  8596c72e5d
  implementation    : 2026-09-26 19:50:09 +0100  9168e61356
  RESULT            : PASS — the test set predates the implementation
```

PASS. On a tampered test set. The gate reported exactly the reassurance it was
built to withhold, which is worse than having no gate, because a gate nobody
audits is read as evidence.

**Fix:** a second, independent check. The sets must be byte-identical to their
state at the freeze commit, compared against the working tree so that an
uncommitted edit is caught too. The ordering check answers *"was this written
first?"*; only the content check answers *"is it still what was written first?"*

Re-tested against both attacks, and the same committed tamper that the old gate
passed:

| attack | old gate | new gate |
|---|---|---|
| uncommitted edit to a frozen case | not detected | **exit 1** |
| committed edit to a frozen case | **PASS** | **exit 1** |
| untouched sets | PASS | PASS |

The gate is now demonstrated to fail, which is the only property that makes it
worth having. A reviewer can reproduce the attack in about thirty seconds:

```bash
python - <<'PY'
import json
p = 'eval/gold_set.json'
d = json.load(open(p, encoding='utf-8'))
for c in d['cases']:
    if c['id'] == 'G001':
        c['expected_route_id'] = 'R-GENERAL'
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
PY
python scripts/verify_integrity.py   # must exit 1
git checkout -- eval/gold_set.json
```

## 5. Four defects found by using the running service

Found by starting the application, asking it the questions a real applicant would
ask, and reading the bytes it sent back. None of the four is visible in the
scores, which is the point: this round is the third instance of the pattern in
item 3, and the largest one yet.

| defect | what a user got |
|---|---|
| internal authoring notes in the corpus and routing table | "The assistant must never state an amount" quoted as if it were information about fees |
| gap questions phrased without the catalogue's exact words | a confident answer to a fee or entry-requirement question the University does not publish |
| no security headers, asserted as a control in `docs/operations.md` | none set, on any response |
| 422 responses echoing the submitted value | the whole 20 KB question returned in the error body |

**The score did not move, and that is reported as a finding rather than a
reassurance.** The abstention gate matched on vocabulary the frozen cases happened
to use, so it scored 1.0000 while answering "Is there an acceptance fee?" instead
of refusing it. The fixes below touch no frozen case, and the numbers are
identical before and after:

| system | gold | paraphrase | out_of_scope | groundedness | violations |
|---|---|---|---|---|---|
| before | 50/50 | 20/20 | 15/15 | 1.0000 | 0 |
| after | 50/50 | 20/20 | 15/15 | 1.0000 | 0 |

Keyword-only baseline, unchanged on both sides: 0.9574 / 1.0000 / 0.9286.

**What did change measurably is the corpus: 28 chunks became 25.** Six corpus
files carried a paragraph written as an instruction to the system rather than as
information for a reader, and one routing-table note was rendered verbatim by the
fallback page. Those paragraphs were removed, not reworded, which is why the
chunk count falls. Every published fact they sat next to was kept, and
`tests/test_regressions.py` asserts both halves: that the facts survive and that
the instructions do not.

**The threshold was not lowered.** The abstention gate scores a match phrase
worth 2 against `min_hits: 2`, so "Is there an acceptance fee?" matched on *fee*
alone. Adding the phrases real users type — *acceptance fee*, *in instalments*,
*still open*, *credits*, *how long* — is the same class of change as fix 1, and
carries the same disclosure obligation. It is disclosed here for that reason, and
the 12 questions that exposed it are in the regression suite rather than in a
frozen file, because a frozen file is not the right place for a case authored
after the freeze.

**One further defect was found in the chunker while fixing the first.** A
paragraph shorter than `_MIN_CHARS` (110) that was not the first in its document
was dropped from the corpus entirely. A 109-character published sentence — "Neither
number is marked as the main switchboard…" — sat one character under the
threshold and was being deleted, while
`test_no_content_is_lost_between_document_and_chunks` asserted that no document
can lose content. The two rules contradicted each other and only the test caught
it. Short units are now attached to the paragraph they complete rather than
discarded; corpus-wide, units dropped: **1 before, 0 after**.

## 6. The deployment was one process serving two roles

Found while running the service rather than by reading the documentation, which
is the same way §5 happened. `README.md` and the `/` page banner both claimed the
front page was "the assistant, with the widget". It was not: the page served
`/widget.js` for third parties to embed and mentioned the widget in prose, but
loaded no script of its own, so the headline journey was the server-rendered
directory and nothing else. The claim was false in a way a user would have
discovered in one second, and the widget existed only as an asset.

**No evaluation case measured this**, because the frozen sets are a JSON API and
a decision path, not a browser. That is worth stating plainly: the sets score the
assistant's honesty about the University, and nothing in them would ever notice
that the page advertising the assistant did not run it. `tests/test_deployment.py`
does.

**The fix.** `/` now loads `/widget-config.js` then `/widget.js`, both
same-origin. The API's location is served as a file rather than set by an inline
`<script>` so that `script-src` can stay `'self'` with no `unsafe-inline`.

**The deployment change that came with it.** The service is now two processes on
the ports it was specified for: the API on `8003`, which owns the assistant, and
the frontend on `5020`, which owns the pages and calls the API for answers. The
frontend does not import the pipeline, so it never loads torch — 37.9 MB resident
against the API's 94.8 MB, and no ~25 second cold start.

**Effect on the scores: none.** The frozen sets are unchanged and re-run after
the split returns the same figures — gold 50/50, paraphrase 20/20,
out_of_scope 15/15, groundedness 1.0000, violations 0, integrity PASS. The
no-JavaScript page now reaches the assistant over HTTP instead of in-process,
which is a new way for it to be wrong, so `tests/test_deployment.py` asserts that
the answer rendered on the page is the answer the API returns for the same
question, and that a failed call is reported as an outage rather than rendered as
a refusal. `app.api:app` still serves everything on one port, and is what the
rest of the suite exercises.

## 7. The service said things that were not true of it

Found by using the running service rather than by reading the code, and found in
the same way as 5 and 6: each of these is invisible to a test that asks *which
route came back* and completely visible to a person reading the screen.

**A greeting was answered as an unpublished topic.** `hi` was not recognised as
small talk, so it fell through to the abstain gate and was told "Not published.
The University does not publish this on any page this assistant can read, so it
is not guessed at." Nothing had been asked, and the sentence is a claim about the
University's published pages. A greeting now gets an orientation reply saying
what the service is for and what it will decline to invent, and it is still
recorded as `abstained` with no citations, so it cannot be counted as an answer.
That last part is the reason the change is safe: `app/orientation.py` matches the
whole normalised question, never a substring, and
`test_no_frozen_case_is_a_greeting` holds the matcher against all 15 frozen
out-of-scope cases. Nine real questions containing "hi", "hello" or "thanks" are
held in `tests/test_wording.py` so the same mistake is not made in the other
direction, by discarding "hi, my portal is not working" as small talk.

**One wording served every abstention.** The same sentence claimed non-publication
for a safety refusal, which is a different claim: refusing to help someone break
into an account has nothing to do with what the University has published. Reasons
are now matched to wording - unpublished, safety, outside the domain, nothing
retrieved - and a greeting gets no status line at all, because a status line
explains a failure and this is not one. Frozen case X15, "What is the weather in
Abuja tomorrow?", was the clearest symptom and it had been scoring as a correct
refusal throughout.

**The API process answered `/` with `{"detail": "Not Found"}`.** Correct, in the
same way that a door with no handle is a door. It is also the first thing anyone
opening port 8003 sees, now that there are two ports. It serves a short notice
naming the API, its documentation, its health check and the frontend instead. The
frontend's `/docs` and `/openapi.json` go the other way and are now `404`: they
were serving a Swagger page listing zero endpoints, which invites a reader to
call an API that lives on the other port.

**Three early returns reported `mode="full"` whatever the deployment was doing.**
A greeting, a safety refusal and an unpublished-topic reply all claimed a language
model had been used. In this environment none is configured, and the honest value
is `retrieval_only`, so `/api/ask` was reporting a capability the service did not
have. The three now call the same `_mode()` the answer path uses. This surfaced
only because the greeting test asserted on the reported mode.

**The widget did not ship its own stylesheet.** An embedder that included one
`<script>` tag got an unstyled wall of divs: the launcher rendered as plain text
and the panel was invisible. A widget that needs the host to know what else to
link is not one tag, it is two. The script now injects its own stylesheet
relative to *its own* URL - read at execution time, because
`document.currentScript` is null by the time `DOMContentLoaded` fires, which was
the first version's bug and would have 404'd the CSS for any third-party embedder.

**The widget's answer renderer ignored lists.** There were two nearly identical
message renderers; the one used for API answers had no list handling while the one
used for the opening message did. So the greeting - the first thing anyone sees -
displayed its own `- ` dashes on the widget after the server-rendered page had
already been fixed. They are now one function.

**On the page, `**bold**` was rendered and `_italic_` was not**, so a user read
"…before you rely on them._" with the underscores on screen next to the source
citation, which reads as a fault in a page whose whole claim is that its sources
can be checked. Both are rendered now, and the trailing disclaimer is dropped
from the answer because the page already states it in full in the note above the
directory - a strip that had been written to match the word "disclaimer", which
the disclaimer text does not contain, and so had never fired.

**A block that began with a dash stayed a paragraph.** The list pattern required a
line of prose before the first bullet, which is the exact shape of the greeting.
The greeting listed its own capabilities with visible dashes on the page while
claiming to be a list.

**Effect on the scores: none.** The frozen sets are unchanged and re-run after all
of this returns the same figures - gold 50/50, paraphrase 20/20, out_of_scope
15/15, groundedness 1.0000, violations 0, integrity PASS. None of these defects
was reachable by the frozen sets, which is the point of section 6 restated for a
third time: they score routing and groundedness, not whether the service is
truthful about itself. The suite now stands at 145 tests, of which
`tests/test_wording.py` and `tests/widget_dom_check.mjs` exist for these and
nothing else. The widget check runs the real file against a small fake DOM,
because a test that greps a script for a string proves the file was written and
not that it works - and that gap is what hid the list bug above.

## Verifying this file

```bash
python scripts/verify_integrity.py    # both checks must pass
git diff 8596c72 HEAD -- eval/gold_set.json eval/paraphrase_set.json eval/out_of_scope_set.json
```

The second command must print nothing.
