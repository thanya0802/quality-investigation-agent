"""Investigation memory.
Structured: investigations, findings and hypotheses tables (what was concluded, with evidence IDs).
Semantic (optional): BM25 over past findings now; embeddings in M2. It answers
'has anyone looked at this before?' when a new investigation starts."""
import json
from . import warehouse
from .telemetry import new_id, now
from .text import BM25

def create(title, model=None, component=None):
    inv_id = new_id("inv")
    with warehouse.tx() as con:
        con.execute("INSERT INTO investigations VALUES (?,?,?,?,?,?,?,?)",
                    (inv_id, title, model, component, "open", now(), now(), None))
    return inv_id

def get(inv_id):
    rows = warehouse.query("SELECT * FROM investigations WHERE inv_id=?", (inv_id,))
    return rows[0] if rows else None

def update_scope(inv_id, model=None, component=None):
    with warehouse.tx() as con:
        if model:
            con.execute("UPDATE investigations SET model=?, updated_at=? WHERE inv_id=?", (model, now(), inv_id))
        if component:
            con.execute("UPDATE investigations SET component=?, updated_at=? WHERE inv_id=?", (component, now(), inv_id))

def add_finding(inv_id, session_id, kind, content, evidence_ids):
    with warehouse.tx() as con:
        con.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?)",
                    (new_id("fnd"), inv_id, session_id, kind, content, json.dumps(sorted(set(evidence_ids))), now()))
        con.execute("UPDATE investigations SET updated_at=? WHERE inv_id=?", (now(), inv_id))

def add_hypothesis(inv_id, statement, confidence, evidence_ids):
    with warehouse.tx() as con:
        con.execute("UPDATE hypotheses SET status='superseded' WHERE inv_id=? AND status='active'", (inv_id,))
        con.execute("INSERT INTO hypotheses VALUES (?,?,?,?,?,?,?)",
                    (new_id("hyp"), inv_id, statement, confidence, "active", json.dumps(sorted(set(evidence_ids))), now()))
        con.execute("UPDATE investigations SET conclusion=?, updated_at=? WHERE inv_id=?", (statement, now(), inv_id))

def findings(inv_id):
    rows = warehouse.query("SELECT * FROM findings WHERE inv_id=? ORDER BY created_at, rowid", (inv_id,))
    for r in rows:
        r["evidence_ids"] = json.loads(r["evidence_ids"])
    return rows

def active_hypothesis(inv_id):
    rows = warehouse.query("SELECT * FROM hypotheses WHERE inv_id=? AND status='active'", (inv_id,))
    return rows[0] if rows else None

def related_past(model, component, exclude=None, query_text=None, k=3):
    """Exact scope match first; then lexical similarity over past conclusions and findings."""
    past = warehouse.query("SELECT * FROM investigations WHERE inv_id <> ? AND conclusion IS NOT NULL",
                           (exclude or "",))
    exact = [p for p in past if p["model"] == model and p["component"] == component]
    if exact or not query_text or not past:
        return exact[:k]
    past = [p for p in past if p["model"] == model or p["component"] == component]   # never cross-scope
    if not past:
        return []
    texts = [f"{p['title']} {p['model']} {p['component']} {p['conclusion']}" for p in past]
    scores = BM25(texts).scores(query_text)
    ranked = sorted(zip(scores, past), key=lambda x: x[0], reverse=True)
    return [p for s, p in ranked if s > 0][:k]
