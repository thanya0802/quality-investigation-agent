"""NL-to-SQL execution accuracy on REAL data: does the model's SQL return the same rows as a
hand-written gold query? Run after `make real` with GEMINI_API_KEY set:  python evals/run_nl2sql.py
Rows are compared as sorted lists of rounded values (column names and row order ignored)."""
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config, llm, warehouse        # noqa: E402
from qia.sql_guard import UnsafeSQL, guard     # noqa: E402

def norm(rows):
    out = []
    for r in rows:
        vals = []
        for v in r.values():
            vals.append(round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
        out.append(tuple(vals))
    return sorted(out, key=str)

def lenient_match(got_rows, want_rows):
    """Correct answer with extra columns: every gold row's values appear, in order, in the matching
    predicted row (rows compared after sorting)."""
    if len(got_rows) != len(want_rows):
        return False
    g, w = norm(got_rows), norm(want_rows)
    return all(set(wr) <= set(gr) for gr, wr in zip(sorted(g, key=str), sorted(w, key=str))) or \
        all(any(set(wr) <= set(gr) for gr in g) for wr in w)

def main():
    if not config.USE_LLM:
        raise SystemExit("Set GEMINI_API_KEY to run the NL-to-SQL eval.")
    from qia.agent.nodes import SQL_PROMPT
    from qia.tools import vocab
    models, comps = vocab()
    cases = json.loads((Path(__file__).parent / "nl2sql_cases.json").read_text())
    results, ctx = [], {"inv_id": "nl2sql-eval", "session_id": "eval"}
    for i, c in enumerate(cases):
        if i:
            time.sleep(config.EVAL_PACE_S)          # stay under free-tier requests-per-minute
        t0 = time.perf_counter()
        status, sql = "wrong", None
        try:
            sql = llm.generate_json(ctx, SQL_PROMPT.format(q=c["q"], scope={}, models=models, comps=comps), "nl_to_sql")["sql"]
            got_rows, want_rows = warehouse.query(guard(sql)), warehouse.query(c["gold"])
            status = ("correct" if norm(got_rows) == norm(want_rows) else
                      "correct_extra_columns" if lenient_match(got_rows, want_rows) else "wrong")
        except UnsafeSQL as e:
            status = f"blocked: {e}"
        except Exception as e:
            status = f"error: {type(e).__name__}: {str(e)[:80]}"
        results.append({"q": c["q"], "sql": sql, "status": status, "ms": round((time.perf_counter() - t0) * 1000)})
    n = len(results)
    strict = sum(r["status"] == "correct" for r in results)
    lenient = strict + sum(r["status"] == "correct_extra_columns" for r in results)
    lat = sorted(r["ms"] for r in results)
    p95 = lat[min(n - 1, int(round(0.95 * n)) - 1)]
    print(f"NL-to-SQL execution accuracy ({config.GEMINI_MODEL}, warehouse={config.WAREHOUSE})")
    print(f"  strict  (exact rows):            {strict}/{n} = {strict / n:.0%}")
    print(f"  lenient (extra columns allowed): {lenient}/{n} = {lenient / n:.0%}")
    print(f"  latency per question: median {statistics.median(lat):.0f} ms, p95 {p95:.0f} ms")
    for r in results:
        if r["status"] != "correct":
            print(f"  {r['status'][:60]:<60} | {r['q']}\n      {r['sql']}")
    (Path(__file__).parent / "results").mkdir(exist_ok=True)
    (Path(__file__).parent / "results" / "nl2sql.json").write_text(json.dumps(results, indent=1))

if __name__ == "__main__":
    main()
