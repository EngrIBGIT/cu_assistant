# Sources and attribution

Every library, model, and data source used by the CU Route Assistant, with its
licence. Produced as capstone submission item 9.

Nothing here is used under a commercial licence, and nothing requires an account
or a payment method to run.

---

## 1. AI and machine-learning components

| Component | Version | Licence | Use | Notes |
|---|---|---|---|---|
| [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | as resolved by `sentence-transformers` | **Apache-2.0** | Sentence embeddings for retrieval and routing | 22 MB, 384 dimensions, CPU-only, no GPU required |
| `sentence-transformers` | ≥ 3.3 | **Apache-2.0** | Loads and serves the embedding model | Brings PyTorch as a transitive dependency — see §6 |
| PyTorch | as resolved transitively | **BSD-3-Clause** | Tensor operations for the embedder | Not a direct dependency; see §6 |
| NumPy | ≥ 1.26 | **BSD-3-Clause** | Vector arithmetic, cosine similarity | Also implements the lexical fallback embedder |

**On the embedding model's licence.** Apache-2.0 permits commercial use,
modification, and redistribution with attribution. It carries no restriction on
use, no copyleft obligation on this repository, and no requirement to disclose
this project's source. Attribution is given here as a matter of practice rather
than obligation.

**No model is fine-tuned.** The embedding model is used exactly as published. No
weights were modified, and no user query was ever used for training.

---

## 2. Language model

**None is configured by default, and none is required.**

The system is fully functional with no language model. It answers by extracting
sentences verbatim from cited source pages, which is why it cannot hallucinate a
fee figure. Model-written answers are an optional enhancement, and every one is
verified against its sources before being returned; if verification fails, the
output is discarded and extraction is used instead.

| Component | Licence | Retention | Use |
|---|---|---|---|
| OpenAI API (`gpt-4o-mini`) — *optional* | Per OpenAI's terms | Not used for training by default | Optional answer composition |
| Ollama — *optional* | Varies by model | Local | Optional answer composition, self-hosted |

The provider's terms and data-retention policy were reviewed before selection and
are recorded in the relevant design decision. If the provider changes them, they
must be re-reviewed before the model is re-enabled.

---

## 3. Application dependencies

| Package | Licence | Use |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | MIT | HTTP interface |
| [Uvicorn](https://www.uvicorn.org/) | BSD-3-Clause | ASGI server |
| [Pydantic](https://docs.pydantic.dev/) | MIT | Request and response validation |
| [httpx](https://www.python-httpx.com/) | BSD-3-Clause | HTTP client for the optional model call |

Every one is MIT or BSD-3-Clause: permissive, patent-granting in the BSD case,
with no copyleft obligation on this repository.

**No test framework is a dependency.** The 56-test suite runs on `unittest` from
the standard library, so it works on a cohort member's machine with no install
step.

---

## 4. Source material

The corpus is **12 pages published by Cosmopolitan University**, curated to
Markdown by hand. Every page is listed in `data/corpus/` with front-matter
recording its source URL, retrieval date, and verification status.

| Source URL | Retrieved | Content type |
|---|---|---|
| `https://www.cosmopolitan.edu.ng/` | 2026-09-26 | Single-page application shell |
| `https://www.cosmopolitan.edu.ng/contact-us` | 2026-09-26 | Contact routes |
| `https://www.cosmopolitan.edu.ng/services/ict` | 2026-09-26 | ICT services |
| `https://www.cosmopolitan.edu.ng/services/library` | 2026-09-26 | Library services |
| `https://admission.cosmopolitan.edu.ng/` | 2026-09-26 | Admission landing page |
| `https://ug.admission.cosmopolitan.edu.ng/` | 2026-09-26 | Undergraduate application |
| `https://pg.admission.cosmopolitan.edu.ng/` | 2026-09-26 | Postgraduate application |
| `https://certificates.cosmopolitan.edu.ng/` | 2026-09-26 | Certificate programmes |
| `https://apply.cosmopolitan.edu.ng/` | 2026-09-26 | Programme application |
| `https://ccsa.cosmopolitan.edu.ng/` | 2026-09-26 | Climate-smart agriculture |
| `https://fims.cosmopolitan.edu.ng/docs` | 2026-09-26 | API documentation |
| `https://www.films.cosmopolitan.edu.ng/` | 2026-09-26 | Film school |

### Rights and permissions

- **Nominative, attributed use.** Content is quoted for the purpose of answering
  questions about those pages, and **every page is cited in the product's
  output**. Nothing is presented as this project's own work.
- **No bulk republication.** The corpus is the minimum needed to answer the
  questions the product exists to answer. It is not a mirror of the site.
- **Publicly accessible pages only.** No authentication was attempted, no
  paywalled or restricted content was accessed, and no page was crawled at a rate
  that could burden the server. Curation is manual and deliberate.
- **Contact details are a factual directory.** An institution's published phone
  number and email address are facts, not its copyrighted expression. Each row in
  the routing table is nonetheless cited to the page it was compiled from, so any
  correction is traceable to a person rather than to an anonymous assertion.

### Not used

No third-party aggregator's content was copied into the corpus. A fee-aggregation
site was identified during the audit as evidence that the University's own site
leaves an information gap, and its content was deliberately **not** used as a
source — a third party's restatement of a fee is not a more authoritative source
than the institution, and importing it would have made the assistant's grounding
untraceable to the University.

---

## 5. Trademarks

Cosmopolitan University, the IDEAS programme, FIMS, and the names of individual
offices are the marks of their respective owners. They are used here
**descriptively** — to identify the correct destination for a user's enquiry.

This project is a **student capstone**. It is not an official University service,
is not endorsed by the University, and does not speak for the institution. The
product says so in its own interface, because an unofficial service that appears
official is a misrepresentation even when every fact in it is correct.

---

## 6. Disclosed deviations

Recorded because a source list that claims a cleaner dependency tree than the code
has would be a lie told in the one document whose entire job is accuracy.

| Claimed in the design | Actually used | Why |
|---|---|---|
| ONNX Runtime for embeddings | `sentence-transformers`, which brings PyTorch | The ONNX path is specified but not implemented. ONNX remains the better choice for a CPU-only deployment — roughly 50 MB of runtime against roughly 2 GB. `requirements.txt` states what is actually installed. See `docs/known-limitations.md` §5 |

---

## 7. AI tools used in producing this work

Disclosed as required by the project guidelines. Any generative AI used as a
learning or production aid is named here, its output was verified, and the
submitting member remains accountable for the result.

| Tool | Used for | Verification |
|---|---|---|
| Claude (Anthropic), via a coding assistant | Code generation, debugging, test authoring, documentation drafting | Every claim is checked against a run: all evaluation figures come from `scripts/run_eval.py`, all test results from `python -m unittest discover`. Documentation claims about behaviour were read from the source or confirmed by execution |

**What this means for the evidence in this repository:**

- No AI-generated output is presented as independently authored research.
- **No AI-generated result is submitted as a test outcome that was not actually
  run.** Every number in the README and in `eval/results.json` comes from an
  execution of the frozen evaluation sets against the built system, with the
  prompt version, corpus state, and model identifier stamped into the results
  file.
- Defects found during development were fixed and the full evaluation re-run,
  rather than individual results being adjusted. The frozen sets were never edited
  to make a case pass; `scripts/verify_integrity.py` enforces that they predate
  the implementation, and it runs in CI.
- Where an AI assistant was wrong, the correction is documented in the code
  comments and the limitations file rather than quietly removed.

---

## 8. Verification

To reproduce the dependency inventory on your own machine:

```bash
pip install -r requirements.txt
pip list --format=freeze
```

The versions this project was developed and measured against are recorded in
`eval/results.json` on every run, so a result can always be traced to the
configuration that produced it.
