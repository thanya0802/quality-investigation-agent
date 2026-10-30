"""API behind the quality-engineer UI. Run: uvicorn api.main:app --reload --port 8000"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qia import audit, config, memory, tools, warehouse
from qia.agent.graph import ask

app = FastAPI(title="Quality Investigation Agent API", version="0.1.0")

class AskIn(BaseModel):
    question: str
    session_id: str | None = None

def _public(st):
    return {k: st.get(k) for k in ("inv_id", "session_id", "scope", "intent", "answer", "hypothesis", "confidence",
                                   "answer_by", "engine", "steps", "validation", "evidence", "resumed")}

@app.get("/v1/health")
def health():
    return {"ok": True, "warehouse": config.WAREHOUSE, "llm": config.USE_LLM}

@app.get("/v1/emerging")
def emerging(model: str | None = None):
    return tools.detect_emerging({"inv_id": None, "session_id": "api"}, model=model)

@app.post("/v1/investigations")
def start(body: AskIn):
    """Start a new investigation with a first question."""
    return _public(ask(body.question, session_id=body.session_id))

@app.post("/v1/investigations/{inv_id}/ask")
def follow_up(inv_id: str, body: AskIn):
    """Continue an investigation; memory carries scope, findings and hypothesis across sessions."""
    if not memory.get(inv_id):
        raise HTTPException(404, "investigation not found")
    return _public(ask(body.question, inv_id=inv_id, session_id=body.session_id))

@app.get("/v1/investigations")
def list_investigations():
    return warehouse.query("SELECT * FROM investigations ORDER BY updated_at DESC LIMIT 100")

@app.get("/v1/investigations/{inv_id}")
def get_investigation(inv_id: str):
    inv = memory.get(inv_id)
    if not inv:
        raise HTTPException(404, "investigation not found")
    return {"investigation": inv, "findings": memory.findings(inv_id), "hypothesis": memory.active_hypothesis(inv_id)}

@app.get("/v1/investigations/{inv_id}/audit")
def audit_trail(inv_id: str):
    return audit.trail(inv_id)
