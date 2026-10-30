# Quality Investigation Agent

An AI agent that helps vehicle quality engineers find emerging defects and investigate their root
cause. It writes and runs SQL over complaint and recall data, works through a multi-step
investigation, remembers what it found across sessions, and logs everything it does.

> **Data:** real, public NHTSA vehicle complaints (official flat files, public domain) and real
> recalls (NHTSA's recalls API) for Ford models. A small synthetic fixture with planted issues is
> used only by unit tests and CI, where answers must be known in advance and nothing is downloaded.

**Status:** M1 (local core) complete. M2: BigQuery adapter, Terraform, Cloud Run.

## What it does
```
question ─► load memory ─► plan ─► investigate ─► report ─► validate ─► persist
                │                     │                          │           │
        resume scope, findings   detect spike → drill down   citations     findings +
        and prior conclusions    → symptoms → similar        checked in    hypothesis
                                 complaints → recalls        code          saved
```
- **Emerging-issue detection:** weekly complaint counts per model/component, flagged with a rolling z-score. Simple on purpose, so an engineer can check it by hand.
- **Explainable root cause:** which symptom words are over-represented since the spike began, representative complaints (cited by ID), model-year and safety breakdown, recall linkage with timing ("complaints rose 6 weeks before the recall").
- **SQL agent:** templated or Gemini-written SQL, passed through a guard (read-only, table allowlist, no comments or multi-statements, row cap; bytes-billed cap via dry run in M2) and run on a read-only connection.
- **Investigation memory:** investigations, findings and hypotheses persist; follow-ups like "which model years?" inherit the scope; new investigations surface earlier conclusions for the same scope only.
- **Governance:** every tool call, SQL statement and LLM call is written to an audit log; write requests are refused; reports cite evidence and invented IDs are rejected.

## Run on real data
1. From nhtsa.gov/nhtsa-datasets-and-apis → Complaints, download `COMPLAINTS_RECEIVED_2020-2024.zip`
   and `COMPLAINTS_RECEIVED_2025-2026.zip`; unzip the `.txt` files into `data/raw/`.
2. `make real` loads Ford complaints (default: F-150, Escape, Explorer, Bronco Sport, Mustang Mach-E)
   and fetches their recalls from NHTSA's API. Change models with `make real MODELS="..."`.
3. `make backtest` measures, on real history, how often a complaint spike preceded a real recall.
4. `python -m qia.cli ask "Are there any emerging issues?"`

## Run tests (synthetic fixture)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
make data && make test && make gate && make demo
python -m qia.cli ask "Which model years are affected?" --inv <inv_id from the demo>
python -m qia.cli audit <inv_id>
make api   # http://localhost:8000/docs
```
Optional: `export GEMINI_API_KEY=...` for Gemini planning, NL-to-SQL and report drafting (validated, with deterministic fallback).

## Real-data results
Fill in from `make backtest` once run: recalls evaluated, share preceded by a spike, median lead
time, and alert precision. Expect modest numbers: many recalls come from manufacturer testing,
not public complaints, and that limitation is worth explaining in interviews.

## Test-fixture results (synthetic, no LLM)
| Category | Pass |
|---|---|
| Detection | 1/1 |
| Root cause (recall-linked, unaddressed, no-spike, unscoped) | 4/4 |
| Memory across sessions | 2/2 |
| SQL | 1/1 |
| Governance (write refusal, off-topic) | 2/2 |

10/10 on data where the issues were planted by the same author is a sanity check, not a benchmark.
The meaningful test is real NHTSA data: does a spike precede a real recall for the same component?

## Not built yet (don't claim these)
BigQuery adapter and dry-run byte caps, Terraform, Cloud Run deployment, embedding-based memory
search, a UI. See BUILD_LOG.md.
