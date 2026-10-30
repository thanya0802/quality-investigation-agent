import pytest
pytest.importorskip("fastapi"); pytest.importorskip("httpx")
from fastapi.testclient import TestClient
from api.main import app

def test_investigation_flow_over_http(db):
    c = TestClient(app)
    r = c.post("/v1/investigations", json={"question": "Investigate the Vesta brake complaints"}).json()
    inv = r["inv_id"]
    assert r["validation"]["passed"]
    assert c.post(f"/v1/investigations/{inv}/ask", json={"question": "Any crashes?"}).status_code == 200
    assert len(c.get(f"/v1/investigations/{inv}/audit").json()) >= 5
    assert c.post("/v1/investigations/inv-missing/ask", json={"question": "x"}).status_code == 404
