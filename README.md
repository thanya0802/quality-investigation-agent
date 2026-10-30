# Quality Investigation Agent

An AI agent for vehicle quality engineers. It finds emerging defects in public complaint data,
investigates their root cause step by step, tells new problems apart from recall-driven complaint
surges, remembers what it found across sessions, and logs every action it takes.

**Data:** real NHTSA vehicle complaints (official flat files) and recalls (NHTSA recalls API) for
five Ford models: F-150, Escape, Explorer, Bronco Sport, Mustang Mach-E. 14,142 complaints,
237 recall records, Dec 2019 – Oct 2026. A small synthetic fixture is used only by unit tests and CI.

## Results on real data

**Early warning backtest:** the detector replayed week by week over history. Did it flag a rise
in complaints for the same model and component in the 26 weeks before each real recall?

| Variant | Recalls preceded by an alert | Median lead | Alerts raised | Alert precision* |
|---|---|---|---|---|
| All complaints | 31 of 226 (13.7%) | 18 weeks | 107 | 24.3% |
| Excluding complaints that cite a recall | **32 of 226 (14.2%)** | **18.5 weeks** | **70 (−35%)** | **32.9%** |

*Share of alerts followed by a recall for the same model and component within 26 weeks.

How to read it: most recalls originate in manufacturer testing and warranty data, not public
complaints, so complaints are one early signal among several. Ignoring recall-citing complaints
(owners reporting remedy or parts delays) removed a third of alerts without losing coverage.

**Two real investigations** (`python -m qia.cli ask ...`):
- *Explorer seats*: rise disappears once recall-citing complaints are excluded; 67% cite
  26V451000 (second-row seat switch). Verdict: recall-driven (parts delays), not a new defect.
- *Bronco Sport engine*: rise persists without recall-citing complaints; symptoms are no-start
  after refueling with EVAP codes. Verdict: possible new issue overlapping fuel-injector recall
  25V467000, which NHTSA files under FUEL SYSTEM rather than ENGINE, so a per-component search misses it.

**NL-to-SQL execution accuracy** (`make nl2sql`, 20 questions with hand-written gold queries):
19/20 (95%) with gemini-3.5-flash-lite; median latency 704 ms, p95 959 ms. The miss is an
ambiguous question ("mentions stalling" matched only "stalling", not "stall"/"stalled").

## How it works
```
question -> load memory -> plan -> investigate -> report -> validate -> persist
                                       |
              agentic: Gemini picks tools in a function-calling loop (max 8 steps)
              deterministic: fixed pipeline (no LLM, and the fallback on any failure)
```
**Tools** (all audited): detect_emerging (two views: with and without recall-citing complaints),
drill_down, symptom_terms, similar_complaints, recall_driven_share, rank_recalls (all of the
model's recalls, owner-cited first), run_sql (guarded), search_memory.

**Guardrails in code, not in the prompt:** read-only SQL guard (single SELECT, table allowlist, no
comments, row cap, BigQuery dry-run byte cap) plus a read-only service account; every tool, SQL
and LLM call written to the audit log; reports must cite evidence and may not mention IDs absent
from it (otherwise a deterministic report replaces the LLM's); write requests refused.

**Detection:** last-4-week complaint count vs. what the previous 26 weeks predict, using median
and MAD (robust to earlier spikes) with a Poisson floor; incomplete weeks and "UNKNOWN OR OTHER"
excluded. Every number can be recomputed by hand.

## Architecture on GCP
| Concern | Local | GCP |
|---|---|---|
| Complaints and recalls | SQLite | BigQuery (month-partitioned, clustered by model and component) |
| Memory and audit log | SQLite | Firestore |
| LLM | optional | Gemini via Secret Manager |
| Serving | uvicorn | Cloud Run (private, scale to zero) |
| Infrastructure | n/a | Terraform (`infra/`) |

Why two stores: BigQuery is built for scanning analytics data, not for small frequent updates
(changing an investigation's scope, superseding a hypothesis). Firestore handles those.

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
# real data: unzip NHTSA COMPLAINTS_RECEIVED_*.txt into data/raw/, then
make real MODELS="F-150,ESCAPE,EXPLORER,BRONCO SPORT,MUSTANG MACH E"
make backtest
python -m qia.cli ask "Find the most urgent issue and investigate its root cause"
export GEMINI_API_KEY=...   # optional: agentic mode + NL-to-SQL eval (make nl2sql)
```

## Deploy to GCP
See DEPLOY.md. Every service used is within Google Cloud's free tier at portfolio scale.

## Limitations
- Public complaints are noisy and self-reported; most recalls are not preceded by them.
- Firestore ordering and filtering happen in Python (fine for tens of investigations).
- The agentic mode's quality depends on the model; the deterministic path is always available.
