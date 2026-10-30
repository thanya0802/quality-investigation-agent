"""Agent evals with deterministic checks against planted ground truth. --gate fails CI below threshold."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config                                  # noqa: E402

HERE = Path(__file__).resolve().parent

def check(case, st):
    text = (st.get("answer") or "") + " " + (st.get("hypothesis") or "")
    fails = []
    for s in case.get("must_contain", []):
        if s not in text: fails.append(f"missing '{s}'")
    for s in case.get("must_not_contain", []):
        if s in text: fails.append(f"contains '{s}'")
    if (h := case.get("hypothesis_contains")) and h not in (st.get("hypothesis") or ""):
        fails.append(f"hypothesis lacks '{h}'")
    if case.get("hypothesis_is_none") and st.get("hypothesis"):
        fails.append("unexpected hypothesis")
    if (c := case.get("confidence")) and st.get("confidence") != c:
        fails.append(f"confidence {st.get('confidence')} != {c}")
    if (sc := case.get("scope")) and st.get("scope") != sc:
        fails.append(f"scope {st.get('scope')} != {sc}")
    if (i := case.get("intent")) and st.get("intent") != i:
        fails.append(f"intent {st.get('intent')} != {i}")
    if (s := case.get("steps_include")) and s not in (st.get("steps") or []):
        fails.append(f"step {s} not used")
    if "answered" in case and st.get("answered") != case["answered"]:
        fails.append(f"answered={st.get('answered')}")
    if not st.get("validation", {}).get("passed", False):
        fails.append(f"validation {st.get('validation')}")
    return fails

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--gate", action="store_true"); a = ap.parse_args()
    from data.generate import generate, load
    config.DB_PATH = Path(tempfile.mkdtemp()) / "eval.db"          # isolated, reproducible run
    rows, recalls, _ = generate(); load(rows, recalls)
    from qia.agent.graph import ask
    cases = json.loads((HERE / "cases.json").read_text())
    invs, results = {}, []
    for i, c in enumerate(cases):
        st = ask(c["q"], inv_id=invs.get(c.get("after")), session_id=f"eval-{i}")
        invs[c["id"]] = st["inv_id"]
        fails = check(c, st)
        results.append({"id": c["id"], "category": c["category"], "passed": not fails, "fails": fails})
    rate = sum(r["passed"] for r in results) / len(results)
    print(f"Agent evals ({len(results)} cases, llm={'on' if config.USE_LLM else 'off'}): pass rate {rate:.2f}")
    cats = {}
    for r in results: cats.setdefault(r["category"], []).append(r["passed"])
    for k, v in cats.items(): print(f"  {k:<12} {sum(v)}/{len(v)}")
    for r in results:
        if not r["passed"]: print(f"  FAIL {r['id']}: {r['fails']}")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "latest.json").write_text(json.dumps(results, indent=1))
    th = json.loads((HERE / "thresholds.json").read_text())["agent_pass_rate"]
    if a.gate:
        if rate < th:
            print(f"GATE FAILED: {rate:.2f} < {th}"); sys.exit(1)
        print("GATE PASSED")

if __name__ == "__main__":
    main()
