"""Fetch REAL recalls for the loaded models from NHTSA's public recalls API
(api.nhtsa.gov/recalls/recallsByVehicle?make=&model=&modelYear=). Run after load_nhtsa.py.
Field names are read case-insensitively and checked, so a schema change fails loudly."""
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import warehouse                   # noqa: E402
from data.load_nhtsa import component       # noqa: E402

API = "https://api.nhtsa.gov/recalls/recallsByVehicle?"
NEEDED = {"nhtsacampaignnumber", "component", "summary", "reportreceiveddate"}

def get(rec, key):
    for k, v in rec.items():
        if k.lower() == key:
            return v
    return None

def parse_date(s):
    s = str(s or "").strip()
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return None

def _ssl_context():
    """macOS python.org installs often lack root certificates; certifi ships its own bundle."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

CTX = _ssl_context()

def fetch(make, model, year):
    url = API + urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
    with urllib.request.urlopen(url, timeout=30, context=CTX) as r:
        data = json.loads(r.read().decode())
    return data.get("results") or data.get("Results") or []

def main():
    pairs = warehouse.query("SELECT DISTINCT make, model, model_year FROM complaints ORDER BY model, model_year")
    if not pairs:
        raise SystemExit("No complaints loaded yet. Run data/load_nhtsa.py first.")
    recalls = {}
    failed = []
    for p in pairs:
        try:
            results = fetch(p["make"], p["model"], p["model_year"])
        except urllib.error.URLError as e:
            failed.append(f"{p['model']} {p['model_year']}: {e}")
            continue
        for rec in results:
            keys = {k.lower() for k in rec}
            if not NEEDED <= keys:
                raise SystemExit(f"Unexpected recall API fields: {sorted(keys)}")
            camp, comp = get(rec, "nhtsacampaignnumber"), component(get(rec, "component") or "")
            rid = f"{camp}:{p['model']}:{comp}"
            r = recalls.setdefault(rid, dict(recall_id=rid, make=p["make"], model=p["model"], model_years=set(),
                                             component=comp, report_date=parse_date(get(rec, "reportreceiveddate")),
                                             defect_summary=(get(rec, "summary") or "").strip(),
                                             remedy=(get(rec, "remedy") or "").strip()))
            r["model_years"].add(str(p["model_year"]))
        time.sleep(0.2)   # be polite to a public API
    with warehouse.tx() as con:
        con.execute("DELETE FROM recalls")
        for r in recalls.values():
            if r["report_date"]:
                warehouse.upsert(con, "recalls", {**r, "model_years": ",".join(sorted(r["model_years"]))}, "recall_id")
    print(f"loaded {len(recalls)} recall records for {len({p['model'] for p in pairs})} models")
    for f in failed:
        print(f"WARNING: fetch failed for {f}")
    if failed and len(failed) == len(pairs):
        raise SystemExit("Every request failed. If this is an SSL error: pip install certifi")

if __name__ == "__main__":
    main()
