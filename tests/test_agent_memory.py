from qia import memory
from qia.agent import nodes
from qia.agent.graph import ask

def test_investigation_links_recall_and_validates(db):
    st = ask("Investigate the Vesta brake complaints")
    assert st["validation"]["passed"]
    assert "RCL-26V-118" in st["hypothesis"] and st["confidence"] == "high"

def test_unaddressed_issue_has_no_recall(db):
    st = ask("Investigate battery problems on the Arden")
    assert "Unaddressed" in st["hypothesis"]
    assert "RCL-" not in st["answer"]

def test_memory_carries_scope_and_findings_across_sessions(db):
    first = ask("Investigate the Vesta brake complaints", session_id="ses-1")
    follow = ask("Which model years are affected?", inv_id=first["inv_id"], session_id="ses-2")
    assert follow["resumed"] and follow["scope"] == {"model": "VESTA", "component": "SERVICE BRAKES"}
    assert follow["answer"].split("MY")[1].startswith("2024")
    sessions = {f["session_id"] for f in memory.findings(first["inv_id"])}
    assert sessions == {"ses-1", "ses-2"}
    summary = ask("Summarize what we know so far", inv_id=first["inv_id"], session_id="ses-3")
    assert "RCL-26V-118" in summary["answer"]

def test_new_investigation_surfaces_prior_conclusion(db):
    ask("Investigate the Vesta brake complaints")
    second = ask("Look into Vesta brakes again")
    assert any(e["kind"] == "memory" for e in second["evidence"])

def test_refuses_write_requests(db):
    st = ask("Delete all complaints for the Vesta")
    assert st["intent"] == "refuse" and st["answered"] is False

def test_validator_flags_invented_ids():
    state = {"answered": True, "intent": "investigate", "answer": "Matches RCL-99V-999 [E1]",
             "evidence": [{"eid": "E1", "kind": "recall", "text": "RCL-26V-118 ...", "ids": ["RCL-26V-118"]}]}
    v = nodes.validate(state)["validation"]
    assert not v["passed"] and "RCL-99V-999" in v["issues"][0]

def test_memory_never_crosses_scope(db):
    ask("Investigate the Vesta brake complaints")
    st = ask("Investigate battery problems on the Arden")
    assert not any(e["kind"] == "memory" for e in st["evidence"])

def test_no_hypothesis_without_a_spike(db):
    st = ask("Investigate Koru airbag complaints")
    assert st["hypothesis"] is None

def test_recall_driven_rise_is_named_as_such(db):
    """Real-data lesson: recalls trigger complaint surges about remedy delays. Those must not be
    reported as a new defect, and the hypothesis must say the rise is recall-driven."""
    from qia import warehouse
    from data.generate import _row
    import random
    from datetime import date, timedelta
    rng, wk = random.Random(1), date.today() - timedelta(days=date.today().weekday())
    with warehouse.tx() as con:
        for w in range(1, 6):
            for n in range(9):
                r = _row(rng, 90_000_000 + w * 100 + n, "KORU", 2024, "AIR BAGS",
                         "recall 25V077000 remedy parts not available, dealer cannot fix airbag", wk - timedelta(weeks=w))
                warehouse.upsert(con, "complaints", r, "complaint_id")
    st = ask("Investigate Koru airbag complaints")
    assert st["signal"] == "recall_driven"
    assert st["hypothesis"].startswith("Recall-driven rise")
    assert st["validation"]["passed"]

def test_recall_complaints_ignored_for_emerging_detection(db):
    from qia import tools
    from data.generate import _row
    import random
    from datetime import date, timedelta
    from qia import warehouse
    rng, wk = random.Random(2), date.today() - timedelta(days=date.today().weekday())
    with warehouse.tx() as con:
        for w in range(1, 6):
            for n in range(9):
                warehouse.upsert(con, "complaints", _row(rng, 91_000_000 + w * 100 + n, "KORU", 2024, "AIR BAGS",
                                 "waiting on recall parts", wk - timedelta(weeks=w)), "complaint_id")
    found = {(i["model"], i["component"]) for i in tools.detect_emerging({"inv_id": "t", "session_id": "s"})}
    assert ("KORU", "AIR BAGS") not in found

def test_component_vocabulary_matches_real_names(db):
    from qia.agent.nodes import plan
    st = {"question": "Investigate Vesta seat complaints", "scope": {"model": None, "component": None},
          "inv_id": "x", "session_id": "s"}
    # SEATS isn't in the synthetic fixture, so the scope stays None rather than guessing
    assert plan(st)["scope"]["component"] is None
    st["question"] = "Look at Arden battery drain"
    assert plan(st)["scope"] == {"model": "ARDEN", "component": "ELECTRICAL SYSTEM"}

def test_every_node_output_key_is_declared_in_state():
    """LangGraph drops undeclared keys silently; this caught a real bug on the first real-data run."""
    import ast, inspect
    from qia.agent import graph, nodes
    declared = set(graph.State.__annotations__)
    returned = set()
    for _, fn in graph.ORDER:
        for node in ast.walk(ast.parse(inspect.getsource(fn))):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                returned |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    returned |= {"issues", "onset", "terms", "recalls", "driven", "sql"}   # set via out.update(...)
    assert returned <= declared, f"undeclared: {returned - declared}"


def test_short_campaign_numbers_are_recognised():
    from qia.tools import campaigns_in
    assert campaigns_in("recall 25S76 / NHTSA 25V467, also 24V267000") == ["25V467000", "24V267000"]

def test_rise_that_survives_exclusion_is_not_called_recall_driven(db):
    """Real-data lesson (Bronco Sport): half the complaints cited a recall, but the rise persisted
    without them; that is a possible new issue, not a recall-driven surge."""
    import random
    from datetime import date, timedelta
    from data.generate import _row
    from qia import warehouse
    rng, wk = random.Random(3), date.today() - timedelta(days=date.today().weekday())
    with warehouse.tx() as con:
        for w in range(1, 6):
            for n in range(12):
                text = ("car will not start after refueling, evap code" if n % 2 else
                        "no start after refueling, worried about recall 25V077 too")
                warehouse.upsert(con, "complaints", _row(rng, 92_000_000 + w * 100 + n, "KORU", 2024, "STEERING",
                                 text, wk - timedelta(weeks=w)), "complaint_id")
    st = ask("Investigate Koru steering complaints")
    assert st["signal"] == "new" and not st["hypothesis"].startswith("Recall-driven")
