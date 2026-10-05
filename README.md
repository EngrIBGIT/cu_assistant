# CU Route Assistant

A grounded AI assistant that answers questions about Cosmopolitan University,
Abuja, and routes people to the right office — using only what the University
itself publishes, and citing every factual claim.

The problem it solves is specific and evidenced. The University runs at least
twelve separate web properties, publishes the same admissions email address
differently in four of them, and serves no content at all to clients that do not
execute JavaScript. An applicant asking "how much is tuition and who do I email"
cannot get a reliable answer from the public web. This is a routing problem, and
that is what the product solves.

---

## The two things it does

1. **Answers** a question in plain language, quoting the source and printing a
   citation for every factual claim.
2. **Routes** the user to the correct portal, office, email, or phone number,
   from a single reconciled routing table with per-row provenance.

And, just as importantly, what it does *not* do: it never states a fact the
University has not published, and it never sends a user to an office that does
not own their question. Cosmopolitan University publishes no tuition figure on any
page reachable at audit time, so the assistant says so and gives the applicant
the admissions email. That is the correct behaviour, and the evaluation set
treats any invented amount as a critical failure.

---

## Quick start

```bash
git clone <repository-url>
cd cu-route-assistant
python -m venv .venv && .venv\Scripts\activate     # Windows
# source .venv/bin/activate                          # macOS / Linux
pip install -r requirements.txt

# two processes: the API on 8003, the frontend on 5020
uvicorn app.api:backend_app  --port 8003     # terminal 1
uvicorn app.api:frontend_app --port 5020     # terminal 2
```

Then open <http://127.0.0.1:5020/>. The API is at
<http://127.0.0.1:8003/>; its health check is
<http://127.0.0.1:8003/api/health>, which reports both ports so you can tell at a
glance whether the pair came up as specified.

For a single process instead, `uvicorn app.api:app --reload` serves everything on
one port. That is still supported and is what the test suite exercises.

The split exists for one reason: the frontend does not need the assistant to
render its pages, and loading it anyway costs about 57 MB of resident memory and
roughly 25 seconds of cold start. Measured on this machine, the frontend process
resides at 37.9 MB and answers immediately, while the API process resides at
94.8 MB. A test asserts that serving a page imports neither `torch` nor
`sentence_transformers`, so the saving cannot be lost quietly.

That page works with JavaScript disabled. It is not a degraded error state — it
carries the whole primary journey, and it is the interface the audit says most
people need.

Both `/` and `/fallback` answer questions as well as list them. The question box
is a plain `GET` form, so it works with scripting off, and it calls the same
`Assistant.ask` as the widget and the API. It used to be a directory you could
browse but not ask, which made the sentence above half true; the regression
suite now asserts that the answer rendered on the page is the answer the API
returns for the same question, so the two cannot drift apart silently. When the
API is unreachable the page says the service is unavailable and keeps the
question in the box — it never renders a refusal in place of an outage, because
"the service is down" and "the University does not publish this" are different
claims and only one of them is true.

| Route | What it is |
|---|---|
| `/` | The assistant, with the widget |
| `/fallback` | The complete experience, no JavaScript required |
| `/api/ask` | `POST {"question": "..."}` — the only write-shaped endpoint, and it writes nothing |
| `/api/routes` | The routing table as data, so any page can render a directory without this service |
| `/api/health` | Build state, active embedder, degradation status |
| `/api/privacy` | Machine-readable statement of the data this service handles |
| `/docs` | OpenAPI UI, on the API process only |

`/docs` and `/openapi.json` are `404` on the frontend. They are FastAPI's defaults
and the frontend mounts no routes, so they served a Swagger page listing zero
endpoints — a page inviting a reader to call an API that lives on the other port.
On the API process, `/` is a short notice naming the API, its docs, its health
check and the frontend; it used to be `{"detail": "Not Found"}`, which is correct
in the same way that a door with no handle is a door.

### Embedding the widget

One script tag, on any page:

```html
<script src="https://your-host/widget.js" defer></script>
```

That is the whole integration. The script loads its own stylesheet relative to its
own URL, mounts inline if the page provides `<div id="cra-root">` and as a floating
launcher otherwise, and posts to `window.CU_ROUTE_API` — served by this service as
`/widget-config.js` so that `script-src` can stay `'self'` with no `unsafe-inline`.
To point it at a different API host, set `window.CU_ROUTE_API` before the tag and
add that origin to the CORS allow-list.

The styling, the mounting, the keyboard behaviour and the failure message are all
covered by `tests/widget_dom_check.mjs`, which runs the real file against a small
fake DOM. A test that greps a script for a string proves the file was written, not
that it works.

### No model download? It still runs.

The neural embedder is a 90 MB download on first use. Without it the system
falls back to a pure-NumPy lexical embedder and reports `mode:
lexical_fallback` in every response and in `/api/health`, so a degraded run can
never be mistaken for a full one. Measured cost of that degradation: routing
accuracy 0.98 against 1.00, and 7 ms median latency against 53 ms.

#### Use Python 3.12 for the neural embedder

On Windows, the neural embedder needs PyTorch, and PyTorch does not currently
load on Python 3.14. The oldest release published for `cp314` is 2.9.0, and
2.9.0 introduced a DLL-initialisation failure on Windows that is still open at
2.14.1 ([pytorch#166628](https://github.com/pytorch/pytorch/issues/166628),
[#169429](https://github.com/pytorch/pytorch/issues/169429)). The fix everyone
reports is 2.8.0, which has no `cp314` wheel. So `pip install -r requirements.txt`
on 3.14 installs a torch that cannot be imported, and the failure surfaces as an
`OSError` about `c10.dll` rather than as anything about Python versions.

```bash
py install 3.12
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install "torch==2.8.0" --index-url https://download.pytorch.org/whl/cpu
```

That last line is the one that matters: `requirements.txt` deliberately does not
pin a torch version, and without it `pip` picks 2.14. `verify.yml` pins 3.12,
which is why CI is green — the pin is load-bearing and did not look it.

Nothing is lost if you skip all of this. The lexical fallback is a designed state,
not a broken one, and CI runs the whole suite in lexical mode on purpose.

---

## Verifying the claims

The evaluation sets in `eval/` were committed **before the first line of
implementation**. That is the difference between a measurement and a
demonstration, and it is checkable:

```bash
python scripts/verify_integrity.py     # automated, also runs in CI
git diff 8596c72 HEAD -- eval/*_set.json    # must print nothing
```

The gate runs two independent checks, because one of them was found to be
insufficient. It verifies that the sets *predate* the implementation, **and**
that they are still byte-identical to their state at the freeze commit. The
ordering check alone passed on a test set whose expected answers had been
rewritten in a later commit — the exact failure the gate exists to prevent. See
`eval/CHANGELOG.md` §4, which includes the attack and the fix.

Changes made *in response to* observed failures are disclosed in
`eval/CHANGELOG.md` with before/after numbers. The frozen cases themselves have
never been edited, and the system was changed twice after the freeze.

### Results

All figures from `python scripts/run_eval.py`, on the frozen sets, with the
versions stamped into `eval/results.json`.

| Measure | Set | Result | Threshold |
|---|---|---|---|
| Routing accuracy | 50 gold questions | **1.00** (50/50) | ≥ 0.90 |
| Paraphrase robustness | 20 reworded questions | **1.00** (20/20) | ≥ 0.80 |
| Refusal correctness | 15 out-of-scope questions | **1.00** (15/15) | 1.00 |
| Groundedness | every answered case | **1.00** | 1.00 |
| Violations | forbidden strings and patterns | **0** | 0 |

### The baseline, run every time

An evaluation that never compares against a simpler alternative is a
demonstration. The keyword-only matcher — no embeddings, no language model — is
scored on the same frozen sets:

| Set | Full system | Keyword baseline |
|---|---|---|
| gold | 1.00 | 0.96 |
| paraphrase | 1.00 | 1.00 |
| out-of-scope | 1.00 | 0.93 |

**This is the most important table in the repository, and it does not flatter
the project.** A keyword matcher gets within 4 points on routing accuracy and
ties on paraphrase robustness. The honest reading is that most of the routing
work in this domain is lexical, because the destinations are named things and
users use the institutional words for them.

The measurable value the semantic component adds is narrower and specific:

- it closes the paraphrase gap that the vocabulary could not (`P06`, a user
  describing a lockout without saying "password" or "portal", is the case that
  motivated the plain-speech vocabulary work);
- it is what makes the *abstention* decisions defensible, since it can tell that
  a question with no institutional content is not merely a question phrased in
  unfamiliar words;
- it holds up on questions nobody anticipated, which a curated keyword list
  structurally cannot.

A project brief claiming "AI is essential here" would not survive this table.
The claim this repository makes is the narrower one that the evidence supports.

### Two independent configurations

```bash
 python -m unittest discover -s tests -t .    # 152 tests
python scripts/run_eval.py                   # neural embedder
python scripts/run_eval.py --embedder lexical   # no model, degraded mode
python scripts/run_eval.py --baseline-only   # keyword matcher alone
```

---

## How it works

```
question
   │
   ├─ 1. safety screen          harmful / academic-integrity request → refuse + route
   ├─ 2. unpublished topic      University has not published this → decline + who to ask
   ├─ 3. route                  hybrid: 0.62 neural + 0.38 keyword, with a threshold
   ├─ 4. retrieve               exact vector search, then document-level expansion
   ├─ 4.5 domain scope          not about this university at all → decline with the reason
   ├─ 6. abstain                nothing cleared the evidence bar → say so
   └─ 5. compose                verbatim extraction, or a model that is verified first
                                     │
                              answer + citation + destination
```

Every consumer — the API, the widget, the no-JavaScript page, the evaluation
harness — calls the same `Assistant.ask`. There is exactly one decision path, so
what the evaluation measures is what a user gets.

**Grounding is structural, not aspirational.** The default composer cannot invent
a fact because it cannot write one: every sentence it emits is a span retrieved
from a cited document, and a test asserts that character for character. When a
language model is configured, its output must carry resolvable citation markers
and is discarded in favour of extraction if verification fails. A model can make
the product better and cannot make it ungrounded.

**Abstention overrides everything.** It is applied last and wins over all of the
above. When nothing clears the evidence threshold the system says it does not
know, rather than dressing a weak match up as an answer.

### Why these choices

| Decision | Rationale |
|---|---|
| RAG, no fine-tuning | No training data exists, and grounding becomes inspectable |
| Brute-force search, no vector index | An index is premature at 25 chunks; documented as a v2 step |
| SQLite-free, in-memory index | No database server to operate |
| Extractive by default | A composer that cannot write cannot hallucinate |
| Model optional | The product works at demo time with the model down; degradation is a designed state |
| No auth, read-only, no accounts | Removes the entire credential and data-protection surface |
| `all-MiniLM-L6-v2` | 22 MB of weights, CPU-only, no GPU, no paid licence |

---

## Privacy and safety

Stated as commitments, and each one is checkable rather than asserted:

- **No personal data is collected.** No accounts, no cookies, no analytics, no
  IP-based identity, no cross-session profiling.
- **No personal data is logged.** Logs carry status, latency, model identifier,
  token counts, and error codes. Not the content of a question.
- **No user query is used to train or fine-tune any model.**
- **No write actions.** The assistant cannot submit an application, alter a
  record, or call a live portal API. A defect cannot corrupt client data.
- **No consent is required, because there is no personal data to consent to.**

`GET /api/privacy` publishes all of this as JSON, so the claim does not depend on
anyone reading a policy page.

Security controls: input length limits, a fixed-window rate limiter, TLS in
transit, a safety gate that refuses unauthorised-access and academic-integrity
requests *before* retrieval, so a harmful request never becomes context a
generator might paraphrase, and a set of response security headers —
`Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security` — set by
`app/api.py` on every response including errors.

The CSP is `script-src 'self'` with no `unsafe-inline` and no `unsafe-eval`,
which holds because the widget builds its DOM with `createElement` and
`textContent` rather than innerHTML. `style-src` does allow `'unsafe-inline'`, a
real and accepted relaxation: `/fallback` carries its stylesheet in a `<style>`
block so the no-JavaScript page needs no second request. TLS remains the
deployment's responsibility, and `Strict-Transport-Security` is inert when served
over plain HTTP.

Rejected questions are answered with a 422 that names the field, the location and
the reason but **not the value**. Pydantic attaches the submitted input to every
validation error it raises, so a 20 KB over-length question used to come back
inside the error body — larger than any answer the service gives, and handed to
whatever intermediary logged the response. The value is the part the caller
already has.

---

## Layout

```
app/
  api.py          HTTP interface. Five things it deliberately does not do.
  pipeline.py     The one decision path. Order of operations, and why.
  router.py       Hybrid neural + keyword destination scoring.
  retriever.py    Exact vector search, calibration, document expansion.
  composer.py     Verbatim extraction; the optional model composer.
  scope.py        Questions that are not about this university at all.
  safety.py       Pre-retrieval refusal gates.
  fallback.py     The complete no-JavaScript experience, server-rendered.
  static/         The embeddable widget: one script tag, no framework.
data/
  corpus/         12 curated pages, with retrieval date and verification per page
  routing_table.json   29 destinations, 12 unpublished topics, per-row provenance
eval/             The three frozen sets, and the results stamped with versions
scripts/          Build the index, run the evaluation, verify the integrity gate
tests/            152 tests. test_system.py needs no fixtures;
                  test_http.py exercises the served interface;
                  test_deployment.py checks the two-process split;
                  test_wording.py checks the service is truthful about itself,
                  and runs the widget against a fake DOM via node
docs/             Architecture, operations, limitations, attribution
```

---

## Documentation

| Document | What is in it |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the system is put together, and the decisions behind it |
| [docs/operations.md](docs/operations.md) | Running it, deploying it, monitoring it, rolling it back |
| [docs/known-limitations.md](docs/known-limitations.md) | What it cannot do, stated plainly |
| [docs/attribution.md](docs/attribution.md) | Every library, model, and data source with its licence |

---

## Project context

An AI capstone by a team, for Cosmopolitan University, Abuja, under the
AIPIL / IDEAS programme. The problem was chosen from an audit of the
University's public web presence rather than assigned, and the scope was
deliberately narrowed to a single user task — *find the correct route and contact
for my matter* — because that task is demonstrably failing, solvable by a small
team, and measurable.

This is a student project. It is not an official University service and does not
speak for the institution.
