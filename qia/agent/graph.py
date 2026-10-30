"""LangGraph wiring (sequential fallback if LangGraph isn't installed: same nodes, same order)."""
from typing import TypedDict
from . import nodes

ORDER = [("load_memory", nodes.load_memory), ("plan", nodes.plan), ("investigate", nodes.investigate),
         ("report", nodes.report), ("validate", nodes.validate), ("persist", nodes.persist)]

class State(TypedDict, total=False):
    question: str; inv_id: str; session_id: str; scope: dict; resumed: bool; prior_findings: list
    prior_hypothesis: dict; intent: str; evidence: list; steps: list; issues: list; onset: str; terms: list
    recalls: list; sql: dict; answer: str; hypothesis: str; confidence: str; answered: bool; answer_by: str
    validation: dict; driven: dict; signal: str; answer_mode: str

KEYS = set(State.__annotations__)

_app = None
def _build():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return "sequential"
    g = StateGraph(State)
    for name, fn in ORDER:
        g.add_node(name, fn)
    g.add_edge(START, ORDER[0][0])
    for (a, _), (b, _) in zip(ORDER, ORDER[1:]):
        g.add_edge(a, b)
    g.add_edge(ORDER[-1][0], END)
    return g.compile()

def ask(question, inv_id=None, session_id=None):
    global _app
    if _app is None:
        _app = _build()
    state = {"question": question, "inv_id": inv_id, "session_id": session_id or nodes.new_session(), "evidence": []}
    if _app == "sequential":
        for _, fn in ORDER:
            # mimic LangGraph exactly: keys not declared in State are dropped, so a missing
            # declaration fails here too instead of only on machines with LangGraph installed
            state.update({k: v for k, v in (fn(state) or {}).items() if k in KEYS})
        state["engine"] = "sequential"
        return state
    state = dict(_app.invoke(state))
    state["engine"] = "langgraph"
    return state
