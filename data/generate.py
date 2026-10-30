"""Synthetic complaints + recalls in the shape of NHTSA's public data, for a FICTIONAL make
("HALCYON"), so tests and evals have known ground truth. Real NHTSA data loads with
data/load_nhtsa.py once you've downloaded it.

Planted ground truth:
  1. VESTA / SERVICE BRAKES: spike starts 12 weeks ago; a recall followed 5 weeks later.
  2. ARDEN / ELECTRICAL SYSTEM: spike starts 7 weeks ago; NO recall (unaddressed issue).
  3. KORU / AIR BAGS: an old recall with no current spike (must NOT be linked to anything).
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config, warehouse          # noqa: E402

MODELS = {"ARDEN": [2022, 2023, 2024], "VESTA": [2023, 2024, 2025], "KORU": [2024, 2025]}
COMPONENTS = {
    "SERVICE BRAKES": ["brakes squeal when stopping", "brake pedal vibrates at highway speed", "brake noise in cold weather"],
    "ELECTRICAL SYSTEM": ["dashboard lights flicker", "radio turns off by itself", "power window stops working"],
    "STEERING": ["steering wheel off center", "steering feels heavy at low speed", "clunk when turning"],
    "POWER TRAIN": ["transmission shifts hard", "hesitation when accelerating", "gear selector sticks"],
    "AIR BAGS": ["airbag warning light came on", "passenger airbag light stays off", "airbag light intermittent"],
    "SUSPENSION": ["rattle over bumps", "rear end bounces", "noise from front suspension"],
}
SPIKES = {
    ("VESTA", "SERVICE BRAKES"): dict(start_weeks_ago=12, per_week=(8, 14), years=[2024], texts=[
        "brake pedal went soft and car took much longer to stop",
        "grinding noise from rear brakes then brake warning light came on",
        "rear brake caliper seized, pulling to one side when braking",
        "increased stopping distance, dealer found corroded rear caliper piston",
        "brake warning light and soft pedal on the highway, nearly rear-ended a truck"]),
    ("ARDEN", "ELECTRICAL SYSTEM"): dict(start_weeks_ago=7, per_week=(7, 12), years=[2023], texts=[
        "12v battery drained overnight, car would not start in the morning",
        "no start after sitting two days, battery dead again, infotainment stays awake",
        "battery drain traced to infotainment module not going to sleep",
        "car dead in parking lot, third 12v battery replacement this year",
        "parasitic battery drain, telematics module keeps waking up at night"]),
}
STATES = ["MI", "OH", "CA", "TX", "FL", "NY", "IL", "WA"]

def monday(d):
    return d - timedelta(days=d.weekday())

def generate(seed=11, weeks=52, today=None):
    rng = random.Random(seed)
    today = today or date.today()
    this_week = monday(today)
    rows, n = [], 11_500_000
    for w in range(weeks, 0, -1):
        wk = this_week - timedelta(weeks=w)
        for model, years in MODELS.items():
            for comp, texts in COMPONENTS.items():
                for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 2, 3])):     # noisy baseline
                    rows.append(_row(rng, n, model, rng.choice(years), comp, rng.choice(texts), wk)); n += 1
                sp = SPIKES.get((model, comp))
                if sp and w <= sp["start_weeks_ago"]:
                    for _ in range(rng.randint(*sp["per_week"])):
                        rows.append(_row(rng, n, model, rng.choice(sp["years"]), comp, rng.choice(sp["texts"]), wk,
                                         crash=rng.random() < 0.06 and comp == "SERVICE BRAKES")); n += 1
    recalls = [
        dict(recall_id="RCL-26V-118", make="HALCYON", model="VESTA", model_years="2024", component="SERVICE BRAKES",
             report_date=str(this_week - timedelta(weeks=7)),
             defect_summary=("Rear brake caliper pistons may corrode and seize, reducing braking performance "
                             "and increasing stopping distance."),
             remedy="Dealers will inspect and replace rear brake calipers."),
        dict(recall_id="RCL-25V-077", make="HALCYON", model="KORU", model_years="2024", component="AIR BAGS",
             report_date=str(this_week - timedelta(weeks=40)),
             defect_summary="Occupant classification sensor may be miscalibrated, disabling the passenger air bag.",
             remedy="Dealers will recalibrate the occupant classification sensor."),
    ]
    truth = {
        "emerging": [{"model": "VESTA", "component": "SERVICE BRAKES", "recall": "RCL-26V-118"},
                     {"model": "ARDEN", "component": "ELECTRICAL SYSTEM", "recall": None}],
        "not_emerging_recall": "RCL-25V-077",
    }
    return rows, recalls, truth

def _row(rng, n, model, year, comp, text, wk, crash=False):
    received = wk + timedelta(days=rng.randint(0, 6))
    return dict(complaint_id=str(n), make="HALCYON", model=model, model_year=year, component=comp,
                crash=int(crash), fire=0, injured=int(crash and rng.random() < 0.3),
                fail_date=str(received - timedelta(days=rng.randint(1, 20))), received_date=str(received),
                week_start=str(monday(received)), state=rng.choice(STATES), description=text)

def load(rows, recalls):
    warehouse.init()
    with warehouse.tx() as con:
        con.execute("DELETE FROM complaints"); con.execute("DELETE FROM recalls")
        for r in rows:
            warehouse.upsert(con, "complaints", r, "complaint_id")
        for r in recalls:
            warehouse.upsert(con, "recalls", r, "recall_id")

if __name__ == "__main__":
    rows, recalls, truth = generate()
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    load(rows, recalls)
    (config.ROOT / "evals" / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"loaded {len(rows)} complaints, {len(recalls)} recalls into {config.DB_PATH}")
