"""REAL-DATA evaluation: would the detector have flagged a complaint rise BEFORE each real recall?

Uses exactly the detector the agent uses (qia/detect.py), replayed week by week over history.
  hit       = a flagged 4-week window for the same model + component in the 26 weeks before the recall
  lead time = weeks from the first such flag to the recall date
  alerts    = distinct flagged runs; 'followed by recall' = a recall for that model+component within 26 weeks
Caveats to state in interviews: many recalls come from manufacturer testing or warranty data rather
than public complaints, and recalls trigger complaint surges, so only the PRE-recall window counts.
Run after `make real`:   python evals/backtest.py
"""
import json, statistics, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config, warehouse            # noqa: E402
from qia.detect import window_flags          # noqa: E402
from qia.tools import weekly_series          # noqa: E402

WINDOW = 26

def run(ignore_recall_complaints):
    weeks, series = weekly_series(ignore_recall_complaints=ignore_recall_complaints)
    if len(weeks) < config.BASELINE_WEEKS + 30:
        raise SystemExit("Not enough complaint history for a backtest.")
    w0 = date.fromisoformat(weeks[0])
    flags = {k: [f[0] for f in window_flags(v)] for k, v in series.items()}
    recalls = warehouse.query("SELECT recall_id, model, component, report_date FROM recalls")
    if not recalls:
        raise SystemExit("No recalls loaded. Run data/fetch_recalls.py first.")
    first_scorable = config.BASELINE_WEEKS + config.WINDOW_WEEKS
    evaluated, hits, leads, misses, no_complaints = 0, 0, [], [], 0
    rec_idx = {}
    for rc in recalls:
        ri = (date.fromisoformat(rc["report_date"]) - w0).days // 7
        rec_idx.setdefault((rc["model"], rc["component"]), []).append(ri)
        if not (first_scorable <= ri < len(weeks)):
            continue
        evaluated += 1
        if (rc["model"], rc["component"]) not in series:
            no_complaints += 1
        pre = [i for i in flags.get((rc["model"], rc["component"]), []) if ri - WINDOW <= i < ri]
        if pre:
            hits += 1; leads.append(ri - pre[0])
        else:
            misses.append(f"{rc['recall_id']} ({rc['report_date']})")
    alerts = confirmed = 0
    for k, fl in flags.items():
        starts = [i for j, i in enumerate(fl) if j == 0 or i - fl[j - 1] > config.WINDOW_WEEKS]
        for s in starts:
            alerts += 1
            if any(s < r <= s + WINDOW for r in rec_idx.get(k, [])):
                confirmed += 1
    return {"weeks_of_history": len(weeks), "first_week": weeks[0], "last_complete_week": weeks[-1],
           "model_component_series": len(series), "recalls_loaded": len(recalls),
           "recalls_in_scorable_window": evaluated,
           "of_which_had_no_public_complaints_for_that_component": no_complaints,
           "recalls_preceded_by_alert": hits, "hit_rate": round(hits / evaluated, 3) if evaluated else None,
           "median_lead_weeks": statistics.median(leads) if leads else None,
           "alerts_raised": alerts, "alerts_followed_by_recall_within_26w": confirmed,
           "alert_precision": round(confirmed / alerts, 3) if alerts else None, "missed": misses}

def main():
    results = {"all_complaints": run(False), "ignoring_recall_citing_complaints": run(True)}
    a, b = results["all_complaints"], results["ignoring_recall_citing_complaints"]
    print(f"History: {a['first_week']} to {a['last_complete_week']} ({a['weeks_of_history']} weeks), "
          f"{a['model_component_series']} model/component series, {a['recalls_in_scorable_window']} scorable recalls\n")
    print("| Variant | Recalls preceded by alert | Hit rate | Median lead (weeks) | Alerts | Alert precision |")
    print("|---|---|---|---|---|---|")
    for name, r in results.items():
        print(f"| {name} | {r['recalls_preceded_by_alert']}/{r['recalls_in_scorable_window']} | {r['hit_rate']} | "
              f"{r['median_lead_weeks']} | {r['alerts_raised']} | {r['alert_precision']} |")
    (Path(__file__).parent / "results").mkdir(exist_ok=True)
    (Path(__file__).parent / "results" / "backtest.json").write_text(json.dumps(results, indent=1))

if __name__ == "__main__":
    main()
