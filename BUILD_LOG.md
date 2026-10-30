# Build log
Rule: a feature goes on the resume only after it runs on my machine and I've verified it.

## 2026-10-30 — M1 local core (sandbox build; re-verify locally)
- Representative complaints were 4 copies of the same text (BM25 ranks duplicates together).
  Fix: one complaint per distinct description.
- **Bug (eval caught):** investigating Arden battery drain pulled in an earlier Vesta brake
  conclusion from memory via lexical similarity. Fix: memory lookups never cross model/component.
- **Bug (eval caught):** Koru airbags (old recall, no spike) got a "consistent with recall"
  hypothesis. Fix: no root-cause hypothesis without an unusual rise.
- Write requests ("delete all complaints") are refused by a rule the LLM planner can't override.

- Switched to real NHTSA data. Checking the official CMPL.txt dictionary caught two loader bugs:
  it used DATEA (date added to file) instead of LDATE (date received), and used ODINO as the
  unique key although NHTSA repeats it once per component. Fix: LDATE and CMPLID.
- Real component names ("SERVICE BRAKES, HYDRAULIC") and multi-word models ("MUSTANG MACH-E"):
  alias matching now picks the busiest matching component and the longest model name.
- Added a real-data backtest: did a complaint spike precede each real recall?

## 2026-10-30 — first run on real NHTSA data (my Mac)
- Loaded 12,835 Ford complaints (F-150 4,203, Escape 3,327, Explorer 3,169, Bronco Sport 2,136).
  Mustang Mach-E matched nothing: NHTSA names it differently. Loader now prints the names it saw.
- Recall fetch failed with CERTIFICATE_VERIFY_FAILED (python.org Python on macOS has no root
  certificates). Fix: certifi's CA bundle. The failure was hidden and the backtest silently scored
  2 leftover synthetic recalls. Fix: loading a dataset clears recalls and memory; total fetch failure now exits loudly.
- Weekly z-scores were tuned on synthetic data; real volumes are 0-3/week per model+component, so
  almost nothing could trigger. Switched to 4-week counts vs. a robust (median/MAD) 26-week
  baseline with a Poisson floor; incomplete current week excluded; 'UNKNOWN OR OTHER' excluded.
- "No emerging issues" was answered as "no evidence". Now reports how many series were checked.
- Ranking: unscoped investigations pick issues with no existing recall first.

- First real backtest (5 Ford models, 226 scorable recalls): 31 recalls (13.7%) preceded by an
  alert, median lead 18 weeks; 107 alerts, 26 followed by a recall within 26 weeks (24.3%).
- Real-data finding: the top "emerging issue" (Explorer seats) was owners reporting parts delays
  for recall 26V451000, not a new defect. Added recall-driven classification (share of complaints
  citing a recall) and an option to ignore recall-citing complaints in detection; the backtest
  now reports both variants.
- Hypothesis cited a 2019 recall because recalls were sorted oldest-first. Fix: rank recalls by
  BM25 match between the defect summary and recent complaint text (+ explicit campaign citations).
- Month names and filler words dominated symptom terms; added to stopwords. Read files as UTF-8.

- Ablation on real data: ignoring recall-citing complaints cut alerts 107 -> 70 (-35%) and raised
  alert precision 0.243 -> 0.329, while recall coverage held (31 -> 32 of 226; median lead 18 -> 18.5 weeks).
- **Bug (only on my Mac):** the recall-driven test failed with LangGraph but passed without it.
  LangGraph silently drops state keys not declared in the State schema; `driven` wasn't declared.
  Fix: declared it, made the no-LangGraph runner drop undeclared keys the same way, and added a
  test that every node output key is declared.
- "Explorer seat complaints" resolved to no component: vocabulary only covered a few systems.
  Fix: synonym table for real NHTSA component names plus word-overlap fallback.

- Real-data logic flaw: with recall-citing complaints excluded, Explorer seats (every complaint
  cites recall 26V451000) showed "no rise", while Bronco Sport engine (rise persists without
  recall-citing complaints; no-start after refueling, EVAP codes) was labelled "recall-driven"
  because half its complaints mention a recall. Fix: detect on two views. Rise only with recall
  complaints = recall-driven; rise without them = possible new issue, even if some cite recalls.
- Owners write campaign numbers short ("25V467" for 25V467000), and the related fuel-injector
  recall is filed under FUEL SYSTEM, not ENGINE. Fix: normalise short campaign numbers; rank all
  of the model's recalls, keeping other components only when cited or strongly matching.

- Result on real data: Explorer seats -> "recall-driven rise" (67% of complaints cite 26V451000;
  rise vanishes without them). Bronco Sport engine -> "possible new issue": no-start after
  refueling with EVAP codes persists without recall-citing complaints and overlaps fuel-injector
  recall 25V467000, which NHTSA files under FUEL SYSTEM rather than ENGINE.
- Recall ranking let repeated generic words ("2020", "explorer", "ford") outrank the recall owners
  actually cited. Fix: unique, non-generic terms only; owner-cited recalls always rank first.

## Local verification (fill in)
- [ ] make data / make test / make gate / make demo
- [ ] API: start an investigation, follow up, read the audit trail
- [ ] Real NHTSA load: verify column positions, record how many recalls had a preceding spike
