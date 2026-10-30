"""Investigation memory (structured: investigations, findings, hypotheses), stored through
qia/state.py (SQLite locally, Firestore on GCP). Semantic recall of past investigations: lexical
BM25 by default, Gemini embeddings when configured (USE_EMBEDDINGS=1)."""
from . import config, state
from .telemetry import new_id, now
from .text import BM25

def create(title, model=None, component=None):
    inv_id = new_id("inv")
    state.get().create_investigation(dict(inv_id=inv_id, title=title, model=model, component=component,
                                          status="open", created_at=now(), updated_at=now(), conclusion=None))
    return inv_id

def get(inv_id):
    return state.get().get_investigation(inv_id)

def list_all(limit=100):
    return state.get().list_investigations(limit)

def update_scope(inv_id, model=None, component=None):
    fields = {"updated_at": now()}
    if model: fields["model"] = model
    if component: fields["component"] = component
    state.get().update_investigation(inv_id, fields)

def add_finding(inv_id, session_id, kind, content, evidence_ids):
    state.get().add_finding(dict(finding_id=new_id("fnd"), inv_id=inv_id, session_id=session_id, kind=kind,
                                 content=content, evidence_ids=sorted(set(evidence_ids)), created_at=now()))
    state.get().update_investigation(inv_id, {"updated_at": now()})

def add_hypothesis(inv_id, statement, confidence, evidence_ids):
    state.get().add_hypothesis(dict(hyp_id=new_id("hyp"), inv_id=inv_id, statement=statement, confidence=confidence,
                                    evidence_ids=sorted(set(evidence_ids)), created_at=now()))
    state.get().update_investigation(inv_id, {"conclusion": statement, "updated_at": now()})

def findings(inv_id):
    return state.get().findings(inv_id)

def active_hypothesis(inv_id):
    return state.get().active_hypothesis(inv_id)

def related_past(model, component, exclude=None, query_text=None, k=3):
    """Exact scope match first; otherwise similarity over past conclusions, restricted to the same
    model or component (never cross-scope: a real bug once leaked brake findings into a battery case)."""
    past = [p for p in state.get().list_investigations(500) if p["inv_id"] != exclude and p.get("conclusion")]
    exact = [p for p in past if p["model"] == model and p["component"] == component]
    if exact or not query_text or not past:
        return exact[:k]
    past = [p for p in past if p["model"] == model or p["component"] == component]
    if not past:
        return []
    texts = [f"{p['title']} {p['model']} {p['component']} {p['conclusion']}" for p in past]
    if config.USE_EMBEDDINGS and config.USE_LLM:
        try:
            from . import llm
            vecs = llm.embed([query_text] + texts)
            q, docs = vecs[0], vecs[1:]
            scores = [sum(a * b for a, b in zip(q, d)) for d in docs]
            ranked = sorted(zip(scores, past), key=lambda x: x[0], reverse=True)
            return [p for s, p in ranked if s >= config.EMBED_MIN_SIM][:k]
        except Exception:
            pass                      # fall back to lexical
    scores = BM25(texts).scores(query_text)
    ranked = sorted(zip(scores, past), key=lambda x: x[0], reverse=True)
    return [p for s, p in ranked if s > 0][:k]
