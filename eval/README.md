# Frozen pre-registered evaluation evidence

These three files are the project's measurement instrument. They were authored
and committed **before the first line of implementation existed** so that the
evaluation cannot be fitted to the result.

## Why this matters

An evaluation written after the system is built proves nothing. A test set
adjusted until the system passes is a demonstration, not a measurement, and the
capstone guidelines require genuine testing evidence rather than a persuasive
claim. Committing these files first, and recording that commit hash, is the only
way to make the distinction checkable by an assessor.

## Verification

```powershell
git log --format="%H %ad %s" --date=iso -- eval/
```

The latest commit above must predate the first commit that adds application code.
`scripts/verify_integrity.py` performs this check automatically and is run in CI.

## The three sets

| File | Cases | Measures | Target |
|---|---|---|---|
| `gold_set.json` | 50 | routing accuracy, answer accuracy, groundedness | routing >= 0.90, groundedness = 1.00 |
| `paraphrase_set.json` | 20 | robustness under lexical mismatch | >= 0.80 |
| `out_of_scope_set.json` | 15 | refusal correctness | 1.00 |

## Rules of engagement

1. **Do not edit a case to make it pass.** If a case is genuinely ambiguous,
   remove it and log the removal, the reason, and the QA Engineer's signature in
   `eval/CHANGELOG.md`.
2. **Do not add cases after implementation begins** for the same reason.
3. **Run failures verbatim.** Results are written to
   `eval/<set>.result.json`, including failures, with the prompt version, corpus
   version, and model identifier stamped on every run.
4. **The keyword baseline is always run.** If the AI approach cannot be shown to
   beat a keyword matcher, that is reported as the finding.
5. **Fee questions have no expected numeric answer.** Cosmopolitan University
   publishes no fee figure on any page reachable at audit time. The correct
   behaviour is to route to Admissions and say the figure is not published. Any
   invented amount is a critical failure even when routing is correct.
