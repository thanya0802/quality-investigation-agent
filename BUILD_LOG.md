# Build log

Notes on what broke, why, and what changed. Newest last.

## Milestone 1: local core
- Representative complaints came back as four copies of the same text, because BM25 ranks
  duplicates together. Now one complaint per distinct description.
- Investigating Arden battery drain pulled in an earlier Vesta brake conclusion from memory
  through lexical similarity. Memory lookups no longer cross model or component.
- Koru airbags (an old recall, no spike) received a "consistent with recall" hypothesis. No
  root-cause hypothesis is produced without an unusual rise.
- Write requests ("delete all complaints") are refused by a rule the LLM planner can't override.

## Switching to real NHTSA data
- The official CMPL.txt dictionary showed two loader bugs: DATEA (date added to the file) was used
  instead of LDATE (date received), and ODINO was used as the unique key although NHTSA repeats it
  once per component. Now LDATE and CMPLID.
- First load: 14,142 Ford complaints across five models. Mustang Mach-E matched nothing because
  NHTSA spells it "MUSTANG MACH E"; the loader now prints the names it saw.
- The recall fetch failed with CERTIFICATE_VERIFY_FAILED (python.org Python on macOS ships without
  root certificates), and the failure was silent: the backtest scored two leftover synthetic
  recalls. Fixed with certifi's CA bundle; loading a dataset now clears recalls and memory, and a
  total fetch failure exits with an error.
- Weekly z-scores tuned on synthetic data almost never fired on real volumes (0-3 complaints a
  week per model and component). Replaced with 4-week counts against a robust (median/MAD)
  26-week baseline with a Poisson floor; incomplete weeks and "UNKNOWN OR OTHER" are excluded.
- "No emerging issues" was reported as "no evidence". It now says how many series were checked.

## What the real data showed
- First backtest (226 scorable recalls): 31 preceded by an alert, median lead 18 weeks; 107 alerts,
  26 followed by a recall within 26 weeks.
- The top "emerging issue", Explorer seats, was owners reporting parts delays for recall
  26V451000, not a new defect. Added recall-driven classification and an option to ignore
  recall-citing complaints in detection. Ablation: alerts 107 -> 70 (-35%), precision 24.3% ->
  32.9%, coverage 31 -> 32 recalls, median lead 18 -> 18.5 weeks.
- The recall-driven test failed under LangGraph but passed without it. LangGraph silently drops
  state keys missing from the State schema, and `driven` was missing. Declared it, made the
  plain runner drop undeclared keys the same way, and added a test that every node output key
  is declared.
- With recall-citing complaints excluded, Explorer seats showed no rise, while Bronco Sport engine
  was labelled recall-driven because half its complaints mention a recall. Detection now runs
  on two views: a rise only with recall-citing complaints is recall-driven; a rise without them
  is a possible new issue even if some complaints cite recalls.
- Bronco Sport engine complaints (no-start after refueling, EVAP codes) cite fuel-injector recall
  25V467000 in short form ("25V467"), and NHTSA files that recall under FUEL SYSTEM. Short
  campaign numbers are normalised and all of a model's recalls are ranked.
- Repeated generic words ("2020", "explorer", "ford") outranked the recall owners actually cited.
  Ranking now uses unique, non-generic terms, and owner-cited recalls come first.

## Milestone 2: Gemini, BigQuery, Firestore, Terraform
- Memory and the audit log sit behind one state interface: SQLite locally, Firestore on GCP.
  A test runs the same multi-session investigation on both and checks identical behaviour.
- BigQuery adapter: positional parameters, default dataset, maximum_bytes_billed on every query,
  and a dry-run byte estimate enforced by the SQL guard. Read-only through IAM (dataViewer only).
- Gemini function-calling loop: the model chooses tools for up to 8 steps; tool outputs become
  numbered evidence the report must cite. Tested with a scripted model for tool order, a guard
  refusal the model sees, a hallucinated recall ID replaced by the deterministic report, and
  fallback when the model call fails.
- NL-to-SQL: the prompt lists the exact model and component values (schema linking) and requires
  portable SQL, since dates are strings in both SQLite and BigQuery.
- Terraform: BigQuery with month partitions and clustering, Firestore, Secret Manager, Artifact
  Registry with a cleanup policy, least-privilege service accounts, private scale-to-zero Cloud
  Run, optional budget alert.

## First NL-to-SQL run (Gemini, real data)
- 16/20 exact. Two failures filtered `make = 'F-150'`: the prompt listed model values but never said
  what `make` holds. The prompt now says make is the manufacturer and names like F-150 are models.
- One answer was right but returned an extra column; the eval now reports strict and lenient
  (extra columns allowed) accuracy separately.
- "Mentions stalling" matched '%stalling%' against a gold '%stall%'. The question is ambiguous;
  kept as a miss rather than rewording it to pass.
- One call took 50 s (likely a retry); the eval now reports median and p95 latency.
- A rerun hit the free-tier rate limit (429 RESOURCE_EXHAUSTED) after 12 questions, and one reply
  had text after its JSON object, which crashed parsing. Added retries with exponential backoff
  for 429/503 (each retry written to the audit log), a parser that takes the first JSON object,
  and pacing between eval calls.
- With the quota exhausted, the agent fell back to the deterministic pipeline and still returned a
  validated report.
- Gemini cited evidence as "[E5, E10]", which the validator didn't parse; comma-separated
  citations are now checked. Stored conclusions no longer keep evidence numbers from the session
  that produced them.
- Tests failed on a machine with GEMINI_API_KEY exported: the suite switched to agentic mode and
  called the real API. An autouse fixture now forces offline mode; Gemini tests opt in with a fake client.

## Deploying to GCP
- First `terraform apply` failed before creating anything: Terraform needs the Cloud Resource
  Manager API to enable APIs, and a new project has it off. Added a one-time bootstrap step.
- Second apply created the APIs, BigQuery, Firestore, the secret and Artifact Registry, then failed
  creating service accounts: the IAM API had been enabled 22 seconds earlier and hadn't propagated.
  A rerun succeeded; service accounts now depend on the API resources.
- Cloud Build with the custom build service account failed because the default logs bucket isn't
  accessible to it, and the error suggested granting storage.admin. Chose Cloud Logging-only build
  logs (cloudbuild.yaml) instead, keeping the build account least-privilege. Build took 50 s.
- Smoke test on Cloud Run: health check reports BigQuery, Firestore and agentic mode; an agentic
  investigation of Bronco Sport engine complaints returned 200 in ~31 s with validation passing.
- In that run Gemini passed `since = 2024-01-01` to the symptom tools instead of the onset week the
  detector returned, so the symptom terms came out generic. The agent instructions now require the
  onset week.
- Terraform's provider lock file is now committed so every machine uses the same provider version.
