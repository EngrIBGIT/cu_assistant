# Known limitations

Stated plainly, because a limitations section written after optimisation tends to
describe a flattering memory of the system rather than the system.

This document was written after the evaluation and before any further
optimisation, which is the only point at which it is worth anything.

---

## 1. The limitation that matters most

**The assistant can only be as current as the University's own publishing.**

Cosmopolitan University publishes no tuition figure on any page reachable at
audit time. So the assistant does not know what tuition costs, and says so, and
gives the applicant the admissions email address.

This is correct behaviour and must not be presented as a defect or worked around.
It is also the honest answer to the question the project was built to answer: a
routing aid that invents a fee is worse than no assistant at all, because the
user cannot tell the difference until they are standing at a bank.

What it means in practice: for the highest-value question an applicant has —
*what does this cost, and how do I apply?* — this product solves half of it. It
solves the second half well.

**If the University wants the first half solved, it has to publish the figure.**
That is a client-side decision outside the scope of this project, and it is
recorded in the reflective statement as a recommendation rather than a
recommendation quietly dropped because it was awkward.

---

## 2. Measured limitations

| Limitation | Measured effect | Threshold | Verdict |
|---|---|---|---|
| Lexical fallback routing | 0.98 against 1.00 | ≥ 0.90 | Met, with the gap documented |
| Keyword-only baseline routing | 0.96 against 1.00 | not pre-registered | See §2.1 |
| Strict primary route rate | 0.46 on the gold set | not pre-registered | See §3 |
| Paraphrase strict primary rate | 0.00 | not pre-registered | See §3 |

### 2.1 The keyword-only baseline reaches 0.96

The honest reading of the headline numbers is that most of the routing work in
this domain is lexical, and the neural component is not what makes it work.

Run on the same frozen sets, with the neural embedder disabled:

| System | gold | paraphrase | out_of_scope |
|---|---|---|---|
| Full (hybrid) | 50/50 | 20/20 | 15/15 |
| TF-IDF lexical | 49/50 | 20/20 | 15/15 |
| **Keyword baseline only** | **45/47** | **20/20** | **13/14** |

The full system beats the keyword baseline by five cases on the gold set and one
on out-of-scope. That is a real margin and it is not nothing, but it is a
margin, not a transformation — and anyone reading "100%" as "this required
embeddings" is reading more into it than the evidence supports.

This is stated here rather than in a footnote because a reader who reaches the
conclusion themselves and finds it contradicted later will trust nothing else in
this document. The defensible claim is narrower than the one the number invites:
*hybrid retrieval plus curated routing plus enforced abstention* reaches 1.00 on
these sets, and the neural component contributes a measurable but modest share
of that.

Two factors make the baseline stronger than one might expect, and both are
properties of this domain rather than general truths:

- A university contact directory has **29 destinations with distinctive names**.
  Most questions contain a token that appears in exactly one route label, so even
  a single keyword hit is strong evidence. The IDF term in the hybrid score
  exists precisely to exploit this.
- The **out-of-scope set is scored on refusal correctness**, and a keyword
  baseline refuses anything it cannot match. It does so for the wrong reason, but
  the metric cannot tell the difference.

The clearest demonstration of the first point is §2 of `eval/CHANGELOG.md`: the
single most serious defect found in this project was a routing failure caused
entirely by keyword statistics, and no embedding model would have caught it.

---

## 3. "Strict primary" is 0.46, and what that means

The gold set passes 50/50, but the *expected route is the single primary
destination* in only 23 of 50 cases. In the rest, it appears among the
alternatives.

This is reported rather than buried because it is the most interesting number in
the evaluation, and it is not what the headline suggests.

**What it is not.** It is not a 54% failure rate. A case passes when the correct
destination is offered anywhere in the top three, because an applicant shown the
right office among two other plausible ones has been helped. The primary route is
a *ranking* preference, not a correctness requirement, and the evaluation was
designed to measure whether the right destination is surfaced at all.

**Why primary is often wrong in a defensible way.** Several destinations genuinely
co-own a question. "I cannot log in" belongs to Admissions when the applicant
means the admission portal and to the ICT helpdesk when they mean a system fault,
and nothing in the words distinguishes the two. Forcing one to be primary in every
case would mean being confidently wrong half the time instead of usefully
ambiguous the other half.

**Why it is nonetheless a real weakness.** A user who sees three offices has been
given a small amount of work to do — reading three cards and choosing — when the
product's premise is that it removes that work. The paraphrase set scores 0.00 on
strict primary for the same reason, and more sharply: those questions are worded
to share no keywords with the gold set, so the router is working from semantics
alone and has less to go on.

**The next step, stated as a step and not as a plan.** Every destination in the
routing table already declares which questions it owns. A tie-break that reads
those declarations and ranks by declared ownership rather than by raw score would
raise strict primary without touching accuracy. It has not been done, because
doing it after seeing the metric is precisely the kind of change that turns a
measurement into a demonstration.

---

## 4. Designed out, not missing

Each of these was considered and deliberately excluded, with the reason recorded
so a reader can tell a decision from an oversight.

| Excluded | Why |
|---|---|
| Fine-tuning or a custom model | No training data exists, and every fee change would force a retrain |
| Voice input and output | Doubles the surface area for no benefit on a routing task |
| Multilingual support | Nigerian English is the working language. A stated limitation, with translation flagged for v2 |
| Image or document understanding | Unnecessary for routing, and it would require accepting uploads — a privacy surface the project has no reason to open |
| Live FIMS or portal API integration | Introduces authentication, rate-limit, and availability risk for zero added user value |
| A native mobile app | A script tag covers the need at a fraction of the cost |
| A vector index | Premature at 25 chunks. Brute-force search over the whole corpus is exact, and an index is a v2 step |
| Accounts, profiles, analytics dashboards | Every one is a privacy surface with no corresponding user benefit |
| Any write action | Removes an entire class of harm. The assistant advises; it does not submit anything |

---

## 5. Genuinely missing

Not excluded by choice. Not yet built.

### The corpus refresh pipeline

The plan specifies automated snapshot, diff, delta report, and a human approval
queue. What exists is the manual version: a person reads a page, curates it to
Markdown, and commits it with a fresh `retrieval` date.

**Why it matters.** The corpus is a snapshot of 26 September 2026 and will
diverge from reality. A stale assistant answering confidently is worse than one
admitting it does not know, because the user has no way to detect the staleness.

**What makes it safe to build.** Every page records when it was retrieved and
whether the University published it consistently. A refresh job can compare a
fresh snapshot against the recorded state and produce a diff for approval, without
the system ever writing to its own corpus. A pipeline that can silently rewrite
what the assistant asserts is a pipeline that can make it wrong invisibly.

### ONNX Runtime embeddings

The plan and the design records specify ONNX Runtime rather than PyTorch, and
that remains the better choice for a CPU-only free-tier deployment: roughly 50 MB
of runtime against roughly 2 GB, and no risk of a CUDA build arriving as a
transitive dependency.

**It is not implemented.** The code uses `sentence-transformers`, which brings
PyTorch with it. `requirements.txt` says so rather than listing a dependency the
code does not use.

**Why it was not done.** The system works, the evaluation passes, and the
difference is a deployment-size optimisation that does not change a single answer.
Spending the remaining time on it would have meant shipping a less-tested system
in exchange for a smaller container image, which is a bad trade for a project
whose value rests on its evidence. It is recorded here as an unfinished design
decision, not as an oversight.

### Multi-instance rate limiting

The rate limiter is a fixed window held in process memory: 30 requests per minute
per client. That is enough to contain an accidental loop or a bored script
against a single-instance free-tier deployment.

It does not coordinate across instances. A multi-instance deployment would need a
shared store, and the honest position is that this would need replacing rather
than that the current implementation is adequate at scale. Stated here instead of
hidden behind an in-memory list that looks like more than it is.

The split into two processes did add one thing to think about here. The frontend
forwards the visitor's address as `X-Forwarded-For` so that the API limits per
person rather than treating every page render as one shared caller. The API
honours that header only from a trusted peer, so this works for the loopback
deployment the project targets and would need a real proxy configuration behind a
reverse proxy. An untrusted `X-Forwarded-For` is ignored rather than believed,
which means a misconfigured reverse proxy degrades to a shared bucket rather than
to no limit at all. That is the right way round.

### The two processes have to be started together

The frontend serves its directory immediately and does not need the API to do
that, so a frontend that is up with no API behind it looks healthy. Its health is
not something you can read from the frontend, because the frontend has no health
endpoint: it has no assistant to report on. `/api/health` on port `8003` is the
only place the pair's state is visible, and it reports the two ports so a
misconfigured pair is visible in the first request.

In practice this means the API must be started first, and a rolling restart
briefly shows `Service unavailable` in the answer box. The page reports that as an
outage and keeps the question in the box, rather than rendering it as a refusal:
"the service is down" and "the University does not publish this" are different
claims, and only one of them is true. `app.api:app` remains available as a
single process for deployments that would rather not manage two.

### Widget placement on the client's properties

The widget is one `<script>` tag and is ready to embed, but embedding it across
the University's twelve properties requires access to their hosting, which a
capstone project does not have. The `/fallback` page is fully self-contained and
can be hosted independently today.

---

## 6. Things that could still go wrong

| Risk | Likelihood | Response if it happens |
|---|---|---|
| The University reorganises a page and a citation 404s | Medium | Per-page provenance makes it traceable; fix the `source_url` and re-commit. Do not edit quoted content without re-reading the page |
| A published contact detail changes | Medium | The refresh pipeline would catch it. Until then, the `verification` field marks rows where the University publishes conflicting values |
| The embedder model is withdrawn upstream | Low | The lexical fallback is a complete substitute at 0.98 routing accuracy |
| A client data owner never materialises | High | The routing table is built from published evidence and every row carries its provenance. A destination that cannot be confirmed against a published source is left out rather than included with a caveat: an unverifiable email address is worse than an absent one, because the user cannot tell the difference. Where a destination is compiled by cross-referencing rather than stated on one page, it is marked `derived` and the caveat is shown |
| The routing table's vocabulary drifts from how users actually ask | Medium | Evidenced. A paraphrased lockout question ("the machine will not let me in") matched nothing, because the table was written in institutional register. Fixing it was vocabulary work, not model work, and it was disclosed as post-freeze tuning with before/after numbers in `eval/CHANGELOG.md` §1 — and the same gap will appear for questions nobody has tested |
| The integrity gate is weakened or removed to make a build pass | Low | It is two independent checks, it exits nonzero, and CI runs it. It was also **found broken during development and silently passing on a tampered test set**; see `eval/CHANGELOG.md` §4. A gate is only worth what its ability to fail is worth, so the fix was demonstrated against a real attack rather than asserted. Anyone changing it should re-run that attack |

That last row is the most important one, and it has now been observed rather than
predicted. The 85 frozen cases are a sample of one organisation's real questions,
not of the space of all questions. Twelve questions found by using the running
service — "Is there an acceptance fee?", "Can I pay in instalments?", "What
O-level subjects do I need?" — were answered confidently while every frozen score
read 1.0000. The vocabulary is now broader and the twelve are in the regression
suite, but the mitigation is not a longer word list: it is noticing failures when
real users hit them, which is why the in-product feedback control exists. Expect
the same class of failure to reappear under a different vocabulary.

---

## 7. What the evaluation does not cover

Stated so the numbers are not read as more than they are.

| Not covered | Why |
|---|---|
| Real users | The 85 cases are authored, not observed. They are frozen and pre-registered, which protects them from being fitted to the result, but it does not make them representative |
| Phrasing outside the frozen vocabulary | The abstention gate scores match terms, and the 85 cases happened to use wording close to the ones the catalogue knew. Live testing found twelve real fee and entry-requirement questions — *acceptance fee*, *in instalments*, *still open*, *credits* — answered confidently where the frozen score was 1.0000. The fixes are disclosed in `eval/CHANGELOG.md` §5, and the scores did not move, which is exactly why they could not have been relied on to find it |
| Task-completion improvement | The plan specifies 10 testers × 3 routing tasks, with and without the assistant. Not yet run. Until it is, the claim "this is faster than the current website" is unevidenced |
| Screen-reader and keyboard testing | The design targets WCAG AA and semantic HTML, and the no-JavaScript path exists specifically because the audit found the University's own site excludes these users. Formal testing with actual assistive technology has not been performed |
| Adversarial input at scale | The safety gate has test cases. It has not been fuzzed, and the read-only no-auth design limits what an attacker can reach regardless |
| Performance under load | Measured on a laptop. No load test has been run |
| Long-run corpus drift | The corpus is one day old. Whether the routing table stays accurate over a term is unknown |

The task-completion study is the most important gap. Routing accuracy measures
whether the system is *correct*; it says nothing about whether it is *faster*,
which is the claim that would justify the project. A system that is right and
slower than the status quo is a worse product, and only the study with real users
can rule that out.
