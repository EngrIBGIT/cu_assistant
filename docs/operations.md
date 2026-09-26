# Operations

Running, deploying, monitoring, and rolling back the CU Route Assistant.

---

## 1. Running it locally

### Requirements

Python 3.11 or later. No GPU. No database server. No paid licence.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -r requirements.txt
```

The first run downloads the sentence-embedding model (~90 MB) and caches it. If
that download fails — no network, restricted mirror, CI without a model cache —
the system falls back to a pure-NumPy lexical embedder and says so:

```
[cu-route] neural embedder unavailable (OSError: ...); fell back to the lexical embedder
```

It still works. Check `/api/health` to see which embedder is actually active.

### Start the service

```bash
uvicorn app.api:app --reload        # development
uvicorn app.api:app --host 0.0.0.0 --port 8000   # deployment
```

Open <http://127.0.0.1:8000/>. Turn JavaScript off in developer tools and reload:
the page still works, and that is the test that matters.

### Environment variables

Every one is optional. The system runs with none of them set.

| Variable | Default | Purpose |
|---|---|---|
| `CRA_EMBEDDER` | `auto` | `auto` tries neural and falls back; `neural` fails loudly; `lexical` forces the dependency-free path |
| `CRA_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Any sentence-transformers model |
| `CRA_LLM_PROVIDER` | *(empty)* | `openai` or `ollama`. Empty means no model, which is a supported configuration |
| `CRA_LLM_MODEL` | `gpt-4o-mini` | Model identifier |
| `CRA_LLM_API_KEY` | *(empty)* | **Never commit this.** Read from the platform's secret store |
| `CRA_LLM_BASE_URL` | *(empty)* | For a self-hosted or proxied endpoint |
| `CRA_LLM_TIMEOUT_S` | `12` | Model timeout before degrading to extraction |
| `CRA_LLM_MAX_TOKENS` | `320` | Output cap |
| `CRA_DATA_DIR` | `./data` | Corpus and routing table location |
| `CRA_EVAL_DIR` | `./eval` | Evaluation sets |
| `CRA_VAR_DIR` | `./var` | Index artefacts |

`.env` is git-ignored. On a free-tier host, set these in the platform's dashboard
rather than in a file.

---

## 2. Verifying a deployment

Five checks, in order. Each one catches a failure the others do not.

```bash
# 1. Is the evaluation evidence still valid?
python scripts/verify_integrity.py

# 2. Does the code still pass its own tests?
python -m unittest discover -s tests -t .          # expect 56 tests, OK

# 3. Do the frozen sets still pass?
python scripts/run_eval.py                         # expect 1.00 across all three

# 4. Is the system honest about its own configuration?
curl -s http://127.0.0.1:8000/api/health | python -m json.tool

# 5. Does it work with JavaScript disabled?
#    open http://127.0.0.1:8000/fallback, disable JS in devtools, reload
```

Step 4 deserves attention. `embedder.active` and `language_model.configured`
tell you what is really running. A result recorded against a fallback cannot be
claimed as a result against the full system, and this endpoint is how you tell
the difference.

---

## 3. Deploying

Sized for a free tier, because the running-cost ceiling is part of the project's
constraints and a deployment that needs a credit card is a deployment that will
not be running when it is demonstrated.

### As a single container

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

# Pre-download the model at build time so the container starts without egress.
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

Pre-downloading at build time matters more than it looks: a cold container that
has to fetch 90 MB on first request will time out the health check and look
broken to an orchestrator.

### Free-tier hosts that work

Render, Fly.io, Railway, and PythonAnywhere all run this shape. The
requirements are: a Python 3.11+ runtime, an outbound HTTPS call to the model API
if one is configured, and about 512 MB of memory for the neural embedder. Nothing
else. No persistent volume is needed — the index is rebuilt at boot in about
25 seconds and the corpus is in the image.

### Health checks

Point the platform at `GET /api/health`. It returns 200 with a JSON body
describing the live configuration. The platform's own health check only needs the
status code; the body is for humans and for the evidence pack.

---

## 4. Monitoring

The service is small enough that the useful signals are few.

| Signal | Where | Why it matters |
|---|---|---|
| Is it up? | `/api/health`, 200 | Availability |
| Is it degraded? | `degraded: true` in health | A silent fallback that nobody notices is worse than an outage |
| Which embedder? | `embedder.active` | Distinguishes a full run from a lexical one |
| Is a model configured? | `language_model.configured` | Needed to interpret a latency change |
| Latency | `trace.latency_ms`, and the eval's p50 | Sudden change usually means a degraded mode |
| Error rate | platform logs | 429s indicate someone hit the rate limit |
| Cost | provider dashboard | Against the monthly ceiling |

**What is deliberately not logged:** the content of a user's question. Logs
carry status, latency, model identifier, token count, and error code. There is a
`debug: true` request flag that permits a query to be inspected server-side to
diagnose a fault; it is off by default, and even with it on, the logs are
redacted of anything resembling personal data.

That is a privacy commitment with an operational consequence, and the consequence
is real: some faults will be harder to debug than they would be with full request
logging. That trade is made deliberately, in favour of a system where no
applicant's question about their own application is retained anywhere.

---

## 5. Keeping the corpus current

The corpus is a snapshot. It will go stale, and a stale assistant that answers
confidently is worse than one that admits it does not know.

### What the pipeline does now

`scripts/build_index.py` rebuilds the retrieval index and writes a manifest
recording document count, chunk count, embedder identity, and every source URL.
It is a manual step:

```bash
python scripts/build_index.py
```

The manifest is written to `var/`, which is git-ignored, because it is a
regenerated artefact and a stale one in version control is misleading.

### The refresh pipeline is specified, not built

The plan calls for automated snapshot, diff, delta report, and a human approval
queue. What exists today is the manual version of the last step: a person reads
a page, curates it to Markdown, and commits it with a fresh `retrieved_at`.

The automation is a v2 item, and `docs/known-limitations.md` records it as
missing rather than describing it in the present tense.

**The provenance fields are what make it safe to do later.** Every page records
when it was retrieved and whether the University published it consistently. A
change-detection job can compare a fresh snapshot against the recorded state and
produce a diff a human approves, without the system ever writing to the corpus on
its own. A pipeline that can silently rewrite what the assistant asserts is a
pipeline that can make it wrong in a way nobody notices.

---

## 6. Rollback

Because the corpus and routing table are version-controlled and the service holds
no state, rollback is a checkout and a restart.

```bash
git log --oneline -5
git checkout <last-good-commit>
uvicorn app.api:app --host 0.0.0.0 --port 8000
```

There is no migration to reverse and no database to reconcile, precisely because
there is no database. A deployment that can be rolled back in one command is a
deployment that can be rolled back under pressure.

---

## 7. Cost

Running cost, at the configuration the project was built for:

| Item | Cost |
|---|---|
| Hosting (free tier) | £0 |
| Embedding model, in-process | £0 — 22 MB of weights on CPU |
| Vector store | £0 — in-memory, 28 chunks |
| Language model API | £0 if unset; the product is fully functional without it |
| Domain | ~£10/year |

**The monthly ceiling is enforced by design rather than by alert.** With no model
configured the service makes no outbound API call, so there is no per-token cost
to overrun. A model can be added for better phrasing, and it degrades to
extraction when it is unavailable, rate-limited, or out of credit — which is also
what happens at demonstration time if the network fails.

Worth being explicit about: this makes the product's cost predictable by
*subtraction* rather than by monitoring. That was a design choice, not an
accident, and it is why the cost risk on this project is rated low.

---

## 8. Troubleshooting

**"neural embedder unavailable" on startup.** The model download failed. The
service is still running in lexical mode and `/api/health` will say
`degraded: true`. Either restore network access for the first run, or set
`CRA_EMBEDDER=lexical` to make the fallback deliberate and stop expecting
otherwise.

**Answers cite the wrong page.** Check `embedder.active` in health first. If it
says `tfidf-lexical` while you expected `minilm-l6-v2`, the routing differences
you are seeing are the documented 2% degradation, not a new defect. Re-run
`python scripts/run_eval.py --embedder lexical` to reproduce it deliberately.

**The widget loads but the chat does nothing.** Almost always a rate limit. The
limiter is 30 requests per minute per client; the response is 429 with
`{"detail": "Too many requests. Try again shortly."}`.

**A citation 404s.** The University reorganised a page after curation. The
`source_url` in the corpus front matter is the page the content came from; find
the current location, update the front matter, and commit. Do not edit the quoted
content without re-reading the page — the retrieval date is the only thing that
makes the citation honest.

**`verify_integrity.py` fails.** Read the whole message. It means the evaluation
sets were modified after implementation began, which voids every result from the
repository. The correct response is to restore the sets from the frozen commit
and re-run the evaluation against a **new** set, not to repair the existing one.
An evaluation fitted to its result is worth nothing, and this check is the only
thing standing between the project and that outcome.
