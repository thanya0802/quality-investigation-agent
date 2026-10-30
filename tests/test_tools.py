from qia import tools
CTX = {"inv_id": "inv-test", "session_id": "ses-test"}

def test_detects_planted_issues_only(db):
    found = {(i["model"], i["component"]) for i in tools.detect_emerging(CTX)}
    assert found == {(e["model"], e["component"]) for e in db["emerging"]}

def test_onset_close_to_planted_start(db):
    issues = {i["model"]: i for i in tools.detect_emerging(CTX)}
    weeks = sorted(r["week_start"] for r in __import__("qia.warehouse", fromlist=["x"]).query(
        "SELECT DISTINCT week_start FROM complaints"))
    arden_idx = weeks.index(issues["ARDEN"]["onset_week"])
    assert abs((len(weeks) - arden_idx) - 7) <= 1          # planted 7 weeks ago

def test_similar_complaints_are_distinct(db):
    sims = tools.similar_complaints(CTX, model="VESTA", component="SERVICE BRAKES", query="rear caliper brake", k=4)
    assert len({s["description"] for s in sims}) == len(sims) == 4

def test_symptom_terms_explain_the_spike(db):
    onset = [i for i in tools.detect_emerging(CTX) if i["model"] == "ARDEN"][0]["onset_week"]
    terms = [t["term"] for t in tools.symptom_terms(CTX, model="ARDEN", component="ELECTRICAL SYSTEM", since=onset)]
    assert "battery" in terms

def test_recall_cited_by_owners_ranks_first(db):
    """Real-data lesson (Explorer seats): generic words outranked the recall owners actually cited."""
    import random
    from datetime import date, timedelta
    from data.generate import _row
    from qia import warehouse
    rng, wk = random.Random(4), date.today() - timedelta(days=date.today().weekday())
    with warehouse.tx() as con:
        warehouse.upsert(con, "recalls", dict(recall_id="25V077000:KORU:AIR BAGS", make="HALCYON", model="KORU",
                         model_years="2024", component="AIR BAGS", report_date="2026-01-05",
                         defect_summary="Sensor may fail.", remedy="Replace sensor."), "recall_id")
        warehouse.upsert(con, "recalls", dict(recall_id="24V010000:KORU:AIR BAGS", make="HALCYON", model="KORU",
                         model_years="2024", component="AIR BAGS", report_date="2024-01-05",
                         defect_summary="Koru 2024 airbag warning light passenger airbag light intermittent",
                         remedy="Inspect."), "recall_id")
        for n in range(6):
            warehouse.upsert(con, "complaints", _row(rng, 93_000_000 + n, "KORU", 2024, "AIR BAGS",
                             "airbag warning light on, waiting on recall 25V077 parts", wk - timedelta(weeks=1)),
                             "complaint_id")
    ranked = tools.rank_recalls(CTX, model="KORU", component="AIR BAGS", since=str(wk - timedelta(weeks=2)))
    assert ranked[0]["recall_id"].startswith("25V077000") and ranked[0]["cited_by_complaints"]
    assert "koru" not in ranked[1]["shared_terms"] and "2024" not in ranked[1]["shared_terms"]
