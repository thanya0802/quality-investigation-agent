"""Load REAL NHTSA complaints into the warehouse.

Field positions are from NHTSA's official dictionary (static.nhtsa.gov/odi/ffdd/cmpl/CMPL.txt,
last updated April 30, 2026): tab-delimited, no header, dates YYYYMMDD.

Download from nhtsa.gov/nhtsa-datasets-and-apis -> Complaints:
  COMPLAINTS_RECEIVED_2020-2024.zip and COMPLAINTS_RECEIVED_2025-2026.zip  (smaller than FLAT_CMPL.zip)
Unzip the .txt files into data/raw/, then:
  python data/load_nhtsa.py --make FORD --models "F-150,ESCAPE,EXPLORER,BRONCO SPORT,MUSTANG MACH-E" --years 2020-2026
"""
import argparse, csv, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config, warehouse        # noqa: E402

csv.field_size_limit(10_000_000)
# 0-based index = (field number in CMPL.txt) - 1
F = dict(cmplid=0, odino=1, make=3, model=4, year=5, crash=6, faildate=7, fire=8, injured=9, compdesc=11,
         state=13, ldate=16, cdescr=19)

def ymd(s):
    s = (s or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() and s[:4] != "0000" else None

def monday(iso):
    d = date.fromisoformat(iso)
    return str(d - timedelta(days=d.weekday()))

def component(c):
    """Top-level system, so complaints and recalls line up:
    'SERVICE BRAKES, HYDRAULIC:FOUNDATION COMPONENTS' -> 'SERVICE BRAKES'"""
    return c.strip().upper().split(":")[0].split(",")[0].strip()

def check_layout(rows):
    """Stop loudly if NHTSA changes the layout instead of silently loading garbage."""
    sample = rows[:500]
    ok = sum(1 for r in sample if ymd(r[F["ldate"]]) and (r[F["year"]].isdigit()) and r[F["crash"]] in ("Y", "N"))
    if sample and ok / len(sample) < 0.8:
        raise SystemExit(f"Only {ok}/{len(sample)} sample rows look valid. The CMPL.txt layout may have changed; "
                         "compare the positions in F with the current dictionary.")

def parse_rows(lines, make, models, y0, y1, seen_models=None):
    rows = [r for r in csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE) if len(r) > F["cdescr"]]
    check_layout(rows)
    out = []
    for r in rows:
        if r[F["make"]].strip().upper() != make:
            continue
        model = r[F["model"]].strip().upper()
        if seen_models is not None:
            seen_models.add(model)
        if models and model not in models:
            continue
        if not r[F["year"]].isdigit() or not (y0 <= int(r[F["year"]]) <= y1):
            continue
        received = ymd(r[F["ldate"]])
        if not received:
            continue
        out.append(dict(complaint_id=r[F["cmplid"]].strip(), make=make, model=model, model_year=int(r[F["year"]]),
                        component=component(r[F["compdesc"]]), crash=int(r[F["crash"]] == "Y"),
                        fire=int(r[F["fire"]] == "Y"),
                        injured=int(r[F["injured"]]) if r[F["injured"]].strip().isdigit() else 0,
                        fail_date=ymd(r[F["faildate"]]), received_date=received, week_start=monday(received),
                        state=r[F["state"]].strip(), description=r[F["cdescr"]].strip()))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--make", default="FORD")
    ap.add_argument("--models", default="", help="comma-separated; empty = all models of the make")
    ap.add_argument("--years", default="2020-2026", help="model-year range")
    ap.add_argument("--raw", default=str(config.DATA_DIR / "raw"))
    a = ap.parse_args()
    y0, y1 = map(int, a.years.split("-"))
    models = {m.strip().upper() for m in a.models.split(",") if m.strip()}
    files = sorted(Path(a.raw).glob("*.txt"))
    files = [f for f in files if f.name.upper().startswith(("COMPLAINTS_RECEIVED", "FLAT_CMPL"))]
    if not files:
        raise SystemExit(f"No complaint .txt files in {a.raw}. See the docstring for what to download.")
    rows, seen = [], set()
    for f in files:
        with open(f, encoding="utf-8", errors="replace", newline="") as fh:
            rows += parse_rows(fh, a.make.upper(), models, y0, y1, seen)
    for m in sorted(models - {r["model"] for r in rows}):
        tokens = [t for t in m.replace("-", " ").split() if len(t) > 2]
        near = sorted(x for x in seen if any(t in x for t in tokens))[:8]
        print(f"WARNING: no complaints matched model '{m}'. NHTSA names containing it: {near or 'none'}")
    warehouse.init()
    with warehouse.tx() as con:
        # a new dataset invalidates everything derived from the old one (including synthetic leftovers)
        for t in ("complaints", "recalls", "investigations", "findings", "hypotheses"):
            con.execute(f"DELETE FROM {t}")
        for r in rows:
            warehouse.upsert(con, "complaints", r, "complaint_id")
    print(f"loaded {len(rows):,} {a.make} complaints from {len(files)} file(s); "
          f"{len({r['model'] for r in rows})} models, {len({r['component'] for r in rows})} components")

if __name__ == "__main__":
    main()
