"""Milestone 2 logic with fake Gemini / BigQuery / Firestore clients."""
import pytest
from fakes import (FakeBQClient, FakeFirestore, FakeGemini, fake_bigquery_module, fake_genai_types,
                   install_module)
from qia import audit, config, llm, memory, state, warehouse
from qia.sql_guard import UnsafeSQL, guard

@pytest.fixture
def gemini(monkeypatch, db):
    install_module(monkeypatch, "google.genai.types", fake_genai_types())
    monkeypatch.setattr(config, "USE_LLM", True)
    monkeypatch.setattr(config, "AGENT_MODE", "agentic")
    def use(fake):
        llm.set_client(fake)
        return fake
    yield use
    llm.set_client(None)

PLANNER = {"intent": "investigate", "model": "VESTA", "component": "SERVICE BRAKES"}
SCRIPT = [
    [("detect_emerging", {"model": "VESTA", "component": "SERVICE BRAKES", "ignore_recall_complaints": True})],
    [("symptom_terms", {"model": "VESTA", "component": "SERVICE BRAKES", "since": "2026-01-01"}),
     ("rank_recalls", {"model": "VESTA", "component": "SERVICE BRAKES", "since": "2026-01-01"})],
    [("run_sql", {"sql": "DROP TABLE complaints"})],
]

def test_agent_chooses_tools_and_report_is_validated(gemini):
    from qia.agent.graph import ask
    fake = gemini(FakeGemini(SCRIPT, PLANNER, {"answer": "Rise matches RCL-26V-118 [E1][E3].",
                                               "hypothesis": "Rear caliper corrosion per RCL-26V-118.",
                                               "confidence": "high"}))
    st = ask("Why are Vesta brake complaints rising?")
    assert st["answer_mode"] == "agentic"
    assert st["steps"] == ["detect_emerging", "symptom_terms", "rank_recalls", "run_sql"]
    assert st["validation"]["passed"] and st["answer_by"] == "llm"
    refused = [r for n, r in fake.seen_tool_results if n == "run_sql"]
    assert "blocked by SQL guard" in refused[0]["result"]["error"]           # the model saw the refusal
    actions = [a["name"] for a in audit.trail(st["inv_id"])]
    assert actions.count("agent_step") >= 4 and "run_sql" in actions        # every step is audited

def test_hallucinated_id_falls_back_to_deterministic_report(gemini):
    from qia.agent.graph import ask
    gemini(FakeGemini(SCRIPT[:1], PLANNER, {"answer": "Caused by RCL-99V-999 [E1].", "hypothesis": None,
                                            "confidence": "low"}))
    st = ask("Investigate Vesta brakes")
    assert st["answer_by"] == "deterministic_fallback" and "RCL-99V-999" not in st["answer"]

def test_model_outage_falls_back_to_deterministic_pipeline(gemini):
    from qia.agent.graph import ask
    gemini(FakeGemini([], PLANNER, {"answer": "x", "hypothesis": None, "confidence": "low"}, fail_loop=True))
    st = ask("Investigate Vesta brakes")
    assert st.get("answer_mode") != "agentic" and "rank_recalls" in st["steps"]
    assert any(a["name"] == "agentic_loop" and a["status"] == "error" for a in audit.trail(st["inv_id"]))

def test_bigquery_adapter_parameters_dataset_and_cost_cap(monkeypatch):
    install_module(monkeypatch, "google.cloud.bigquery", fake_bigquery_module())
    monkeypatch.setattr(config, "WAREHOUSE", "bigquery")
    fake = FakeBQClient(rows=[{"model": "F-150", "n": 3}])
    warehouse.set_bq_client(fake)
    try:
        assert warehouse.query("SELECT model, COUNT(*) n FROM complaints WHERE model = ? AND model_year = ?",
                               ("F-150", 2021)) == [{"model": "F-150", "n": 3}]
        cfg = fake.calls[0][1]
        assert cfg.default_dataset == "demo-project.quality" and cfg.maximum_bytes_billed == config.MAX_BYTES_BILLED
        assert [(p.type_, p.value) for p in cfg.query_parameters] == [("STRING", "F-150"), ("INT64", 2021)]
        fake.bytes_processed = config.MAX_BYTES_BILLED + 1                   # dry run says: too expensive
        with pytest.raises(UnsafeSQL, match="cap"):
            guard("SELECT * FROM complaints")
        assert fake.calls[-1][1].dry_run is True
    finally:
        warehouse.set_bq_client(None)

def test_firestore_state_behaves_like_sqlite(db):
    """Same agent flow on the Firestore backend: memory across sessions, superseded hypotheses, audit."""
    from qia.agent.graph import ask
    state.set_backend(state.FirestoreState(client=FakeFirestore()))
    try:
        first = ask("Investigate the Vesta brake complaints", session_id="s1")
        follow = ask("Which model years are affected?", inv_id=first["inv_id"], session_id="s2")
        assert follow["resumed"] and follow["scope"] == {"model": "VESTA", "component": "SERVICE BRAKES"}
        assert {f["session_id"] for f in memory.findings(first["inv_id"])} == {"s1", "s2"}
        assert "RCL-26V-118" in memory.active_hypothesis(first["inv_id"])["statement"]
        memory.add_hypothesis(first["inv_id"], "revised", "low", [])
        assert memory.active_hypothesis(first["inv_id"])["statement"] == "revised"
        names = [a["name"] for a in audit.trail(first["inv_id"])]
        assert names[0] == "detect_emerging" and len(names) >= 6
    finally:
        state.set_backend(None)

def test_rate_limits_are_retried_with_backoff():
    calls, waits = {"n": 0}, []
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")
        return "ok"
    assert llm.with_retry(flaky, attempts=4, base_delay=1, sleep=waits.append) == "ok"
    assert waits == [1, 2]

def test_non_retryable_errors_fail_fast():
    with pytest.raises(ValueError):
        llm.with_retry(lambda: (_ for _ in ()).throw(ValueError("bad request")), sleep=lambda s: None)

def test_json_with_trailing_text_is_parsed():
    assert llm.parse_json('```json\\n{"sql": "SELECT 1"}\\n```\\nHope this helps!') == {"sql": "SELECT 1"}

def test_comma_separated_citations_are_validated():
    from qia.agent import nodes
    ok = {"answered": True, "intent": "investigate", "answer": "Battery drain [E1, E2].",
          "evidence": [{"eid": "E1", "kind": "x", "text": "a", "ids": []}, {"eid": "E2", "kind": "x", "text": "b", "ids": []}]}
    assert nodes.validate(ok)["validation"]["passed"]
    bad = dict(ok, answer="Battery drain [E1, E9].")
    assert "E9" in nodes.validate(bad)["validation"]["issues"][0]
