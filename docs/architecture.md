# Architecture

How the system is put together, and why it is put together that way.

---

## 1. The shape of the problem

The University publishes its information across at least twelve separate web
properties. Each is independently built and independently branded. The same
admissions email address appears in four places, two of them inconsistent:

| Source | Address published |
|---|---|
| `admission.cosmopolitan.edu.ng` | `admission@cosmopolitan.edu.ng` |
| `www.cosmopolitan.edu.ng/contact-us` | `admissions@cosmopolitan.edu.ng` |
| `www.cosmopolitan.edu.ng/contact-us` | `pg.admissions@cosmopolitan.edu.ng` |
| `pg.admission.cosmopolitan.edu.ng` | `admission@cosmopolitan.edu.ng` |

So the task is not "answer questions about a university". It is: **given a
person's situation, which of thirty-odd destinations owns it, and what is the
current contact route to that destination?** Everything in this architecture
follows from taking that framing seriously.

The consequence worth stating up front: this is a *routing* problem with an
information problem attached. Most of the measurable value is in the routing,
and the baseline table in the README shows that a keyword matcher gets most of
the way there. The architecture spends its complexity where the difficulty
actually is — deciding when *not* to answer, and staying inside the published
record while doing it.

---

## 2. Component diagram

```
                        ┌─────────────────────────────────────────┐
   browser, no JS ────▶ │  /fallback        server-rendered HTML   │
                        │  every destination, every unpublished    │
                        │  topic, every published contact conflict │
                        └─────────────────────────────────────────┘

                        ┌─────────────────────────────────────────┐
   browser, JS ───────▶ │  /            widget: one <script> tag   │
                        │  chat, citation panel, feedback control │
                        └─────────────────────────────────────────┘
                                      │  POST /api/ask
                                      ▼
                        ┌─────────────────────────────────────────┐
                        │              pipeline.ask               │
                        │                                         │
                        │  1   safety screen        refuse        │
                        │  2   unpublished topic    decline       │
                        │  3   route                hybrid score  │
                        │  4   retrieve             vector search │
                        │  4.5 domain scope        decline       │
                        │  6   abstain              decline       │
                        │  5   compose              extract/model │
                        └─────────────────────────────────────────┘
                          │            │            │           │
              ┌───────────┘            │            │           │
              ▼                        ▼            ▼           ▼
        ┌───────────┐          ┌────────────┐ ┌─────────┐ ┌──────────┐
        │  router   │          │ retriever  │ │composer │ │ citations│
        │ neural +  │          │ exact      │ │verbatim │ │  +       │
        │ keyword   │          │ cosine     │ │ or      │ │ provenance│
        └─────┬─────┘          └──────┬─────┘ │verified │ └──────────┘
              │                       │       └────┬────┘
              ▼                       ▼            ▼
        ┌──────────────────────────────────────────────────┐
        │  data/routing_table.json   data/corpus/*.md      │
        │  29 destinations           12 published pages    │
        │  12 unpublished topics     per-page provenance   │
        └──────────────────────────────────────────────────┘
```

Every consumer — the API, the widget, the no-JavaScript page, the evaluation
harness — calls the same `Assistant.ask`. There is one decision path. A system
with a chat path and a separate "evaluation path" measures something other than
what users get, and every project that has done that has produced a number that
does not reproduce.

---

## 3. Order of operations, and why that order

The order in `pipeline.ask` is not arbitrary. Each step is placed so that the
most dangerous thing happens first and the most conservative decision has the
last word.

### Step 1 — Safety screen, before retrieval

A harmful or integrity-violating request must never become context that
something paraphrases. A filter applied after generation has to recognise and
undo text that has already been written, which is a losing position. The gate
refuses unauthorised-access requests ("hack into my roommate's account") and
academic-integrity requests ("write me a 5000-word essay I can submit"), and
carries a redirect so a refusal still ends at an office rather than a dead end.

It is conservative in one direction only: it refuses when reasonably confident,
because a false refusal costs one user a redirect while a false pass can put
harmful content in front of an applicant.

### Step 2 — Unpublished topic, before composition

If the University does not publish something, the correct answer is a templated
"that is not published, here is who to ask" — produced *without* echoing the
question's vocabulary back and without any chance of a retrieved sentence
drifting into a claim.

Twelve topics are declared in the routing table, each naming the offices that own
the missing fact. The topic names its owners; the router picks among them.
Neither half is sufficient alone:

- a single hardcoded hint is a floor, not an answer — it cannot read the
  question, so filing "how many programmes can I list" under the certificate
  programmes page sends an applicant asking about the undergraduate portal to the
  wrong office;
- the router alone is not enough either, because an unpublished question is by
  definition one the router has no good match for, and left to itself it reaches
  for whichever office sounds adjacent — which is how a scholarship enquiry ends
  up at the fees desk.

Constrained to its declared owners, the router can only choose between plausible
destinations.

The topic's own label is deliberately not echoed back. Restating "application
deadlines and closing dates" at someone who asked about closing dates reads as
though the refusal were itself a finding, and it leaks the trigger vocabulary
back at a user who may have used a term the University does not.

### Step 3 — Route

Hybrid scoring, described in §4.

### Step 4 — Retrieve, then expand to document level

Exact cosine search over chunks, followed by document-level context expansion: a
passage that shares no topic words with the question cannot match it, but it is
still the right answer if it sits in a document that did match.

### Step 4.5 — Domain scope

A question about something other than this university is not a retrieval
failure. "What is the weather in Abuja tomorrow?" was answered with the
University's postal address and four irrelevant citations, because the word
"Abuja" saturated the keyword score on the campus-address route. Two fixes, in
`app/scope.py` and `app/router.py`:

- **Setting words carry no routing signal.** "Abuja", "campus", "university"
  appear on exactly one route each, so the rarity rule treated them as decisive.
  What they establish is that the user is talking about this university at some
  location on earth, which every question already implies. They are retained in
  the table for auditability and excluded from scoring.
- **A backstop, not a filter.** The scope gate is consulted only when no
  destination was found at all. A question that routes anywhere institutional is
  never scope-declined, however little else about it makes sense — so "Is there
  shelter on campus when it rains?" is answered, not refused.

Deliberately conservative. The medical and legal patterns are anchored on
possession ("my symptoms", "can I be sacked"), not on topic, because
"What are the symptoms of malaria?" may be asked on someone else's behalf and a
false refusal is the one error this layer must not make.

### Step 6 — Abstain, applied last

If nothing clears the evidence threshold, the system says it does not know. This
overrides everything above, including a route that scored well: a confident
destination is not a licence to state an unpublished fact.

### Step 5 — Compose

Extractive by default. Model-written only if a model is configured *and* its
output passes verification.

---

## 4. Routing: why hybrid

Routing is `0.62 × neural + 0.38 × keyword`, with a floor applied when a keyword
saturates.

**Neural similarity** between the question and a prose profile of each
destination. This carries paraphrase: "I am a working professional, I want to
deepen my qualification" reaches the postgraduate portal without sharing a single
content word with "postgraduate".

**Curated keyword overlap** against phrases recorded in the routing table. This
carries colloquial phrasing the embedding model handles poorly ("leaver", "walk
through", "put my name down", "the machine will not let me in"). It is also
auditable — a non-engineer can read a route's keyword list and see exactly why
that destination was offered.

Neither term alone is defensible. Neural-only routing is unexplainable: a user
who gets the wrong office has no way to find out why. Keyword-only routing is
brittle, and the baseline table in the README is the measurement of how brittle.

### Keyword weighting

Three properties, each of which exists because its absence caused a specific
failure.

**Rarity.** A keyword is worth what it discriminates. A term every route claims —
"student", "university" — is evidence of nothing; a term exactly one route claims
is evidence on its own. Weighting by rarity rather than length also removed an
accident of vocabulary: saturation used to be reachable only by a three-word
phrase, so the single word "hostel" earned a third of a unit however clearly it
indicated the accommodation route, and a hostel question lost to whichever route
scored better on "fee".

**Proximity.** Every word of a multi-word phrase must be present, within a window
only slightly wider than the phrase. "I forgot my portal password" matches
"forgot my password"; a long document that happens to contain every word does not.
Counting *distinct words* rather than positions is what stops a single "is" plus
two "the"s from satisfying all three slots of "who is the".

**Word boundaries.** "ug" must not match inside "drug", "art" inside "article".

A bare substring test produced exactly that, and the quiet nonsense it caused is
what makes a routing table untrustworthy.

---

## 5. Grounding: structural, not aspirational

### The extractive composer cannot hallucinate

It assembles answers from spans retrieved verbatim from cited documents. It
cannot invent a fact because it cannot write one.

This is enforced by a test that compares every rendered line of every answer
against the body of a cited document, character for character. That test found a
real defect: the composer was appending a full stop to each span, which made ten
assertions fail and — more seriously — turned chunk-truncated fragments into
apparent complete sentences. The library's address ends at "Main Library
Building, Ground" because the chunk boundary fell there; a full stop asserted a
completeness the source does not state. Quoting a fragment as a fragment is more
truthful, and the citation is one click away.

### The model composer is verified before release

When a model is configured, its output must carry citation markers resolving to
supplied sources, must not exceed a length bound, and must contain no monetary
figure. The numeric check is blunt on purpose: this corpus contains no published
fee, and inventing one is the single worst failure available to the system. Any
generated figure is a defect until a human proves otherwise.

If verification fails, the output is **discarded** and extraction is used
instead. A model can make the product better and cannot make it ungrounded. That
is the whole difference between adding a model to a grounded system and bolting
one onto it.

### Graded lists get a reserved budget

A list block is usually the payload of an answer — programme names, API scopes,
services offered — and it occupies one span however long it is. Left to compete
on salience it always loses, because its items are short fragments sharing few
words with any question, and the answer arrives without the thing that was asked
for. One reserved span, deliberately: two was tried and measurably harmful,
because the FIMS documentation and the climate-smart agriculture page name each
other, and reserving two lists attached API scopes to a question about farming
programmes. Both answers then scored well on a keyword check while being wrong
to a reader.

---

## 6. Abstention thresholds

Frozen in `app/config.py` as named constants, not tuned per query, so behaviour
is auditable and any change is a version bump.

| Constant | Value | Meaning |
|---|---|---|
| `min_evidence` | 0.34 | Below this a retrieved claim is not considered supported |
| `min_route` | 0.26 | Below this no destination is offered as primary |
| `min_route_secondary` | 0.20 | Below this a destination is not offered at all |
| `weight_neural` | 0.62 | Weight of semantic similarity in routing |
| `weight_keyword` | 0.38 | Weight of curated keyword overlap |

Retrieval scores are **calibrated**, not raw cosine. Raw cosine over a small
curated corpus clusters in a narrow band, and a threshold set on raw values is
meaningless; calibration maps the observed score distribution onto a usable range
so the thresholds mean what they say.

---

## 7. Data

### The corpus

Twelve published pages, curated to Markdown by hand, each carrying front-matter
recording `source_url`, `source_title`, `retrieval_at`, `verification`, and
`content_type`.

Manual curation rather than crawling, for three reasons: it avoids a
terms-of-service question entirely, it keeps a human judgement on what counts as
a page worth quoting, and it cannot accidentally become a scraping operation
against a client's server.

The front matter is what makes provenance possible. Every citation in the
response carries the retrieval date and the verification status, so a user can
see how current a fact is and whether the University published it once or
publishes it inconsistently.

### The routing table

29 destinations, each with an office, an entry point, contact details, opening
hours where published, a `source_url` the row was compiled from, and a
`verification` field:

| Value | Meaning |
|---|---|
| `published` | The University publishes this and it is consistent |
| `published_conflicting` | The University publishes more than one value, and the row carries both |
| `unverified` | Compiled from public evidence; no client data owner has confirmed it |

Disclosed conflicts are not resolved silently. Where the University publishes two
numbers, the response says so and tells the user to try the second if the first
does not answer. Picking one and presenting it as fact would be a small lie with
a support ticket behind it.

Plus 12 unpublished topics, each naming its match terms and the offices that own
the missing fact.

---

## 8. Privacy and threat model

### What the service does not do

Five things, each of which would violate a stated commitment:

1. accept credentials — there is no authentication;
2. set tracking cookies;
3. log request bodies;
4. persist queries;
5. expose an endpoint that acts on an external system.

### Threats and responses

| Threat | Response | Residual risk |
|---|---|---|
| Prompt injection via a user query | Pre-retrieval safety screen; system-prompt isolation; output verification; the design is read-only, so the blast radius of a bad response is bad text on one screen | Accepted and documented |
| Hallucinated fee, deadline, or contact | Citation enforcement; abstention; 100% groundedness threshold; out-of-scope set; monetary-figure rejection in model output | None known |
| Denial of service | Input length limit; fixed-window rate limiter at 30 requests/minute per client | In-process limiter does not coordinate across instances — documented, not hidden |
| Corpus poisoning | Manual curation only; per-page provenance and retrieval date; a human reads every page before it ships | Requires a compromised curator |
| Client data corruption | No write actions anywhere in the system | Structurally impossible |
| Cost overrun on a free tier | Model optional; retrieval-only mode; caching; documented monthly estimate | Operational discipline required |

---

## 9. Degradation is a designed state

The system has three configurations and each is a real product state, not an
error path:

| Configuration | `mode` | Measured routing accuracy | Latency |
|---|---|---|---|
| Neural embedder | `full` | 1.00 | 53 ms p50 |
| Lexical embedder | `lexical_fallback` | 0.98 | 7 ms p50 |
| No model configured | `retrieval_only` | as above | as above |

`/api/health` reports the active embedder, whether a model is configured, and
whether the service is degraded. Evaluation results stamp the embedder name, the
model identifier, and the routing-table version into `eval/results.json`, so a
degraded run cannot be presented as a full one — which is the failure mode that
makes an evaluation worthless.

`mode` is also returned on every single response, so a client can degrade its own
presentation without inferring anything.
