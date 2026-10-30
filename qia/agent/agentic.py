"""Agentic investigation: Gemini chooses which tools to call, with what arguments, for how many steps.

Guardrails stay in code, not in the prompt:
  * every tool is the same audited function the deterministic pipeline uses
  * model-written SQL goes through the SQL guard and a read-only connection
  * the loop is capped at MAX_AGENT_STEPS
  * tool outputs become numbered evidence; the final report must cite it and is validated
  * any failure falls back to the deterministic pipeline
"""
import json
from .. import config, llm, memory, tools
from ..sql_guard import UnsafeSQL

SYSTEM = """You investigate vehicle quality issues using public complaint and recall data.
Use the tools; never guess numbers. Good practice:
1. If no model/component is given, call detect_emerging to find candidates.
2. For a model+component, call detect_emerging twice: ignore_recall_complaints=true (new problems) and
   false (all complaints). A rise only when recall-citing complaints are included is recall-driven
   (remedy or parts delays), not a new defect.
3. Then drill_down by model_year, and call symptom_terms, similar_complaints, recall_driven_share and
   rank_recalls with `since` set to the onset_week that detect_emerging returned. A wider window
   (e.g. a whole year) blurs the new symptoms into old ones. If no rise was found, use the last 26 weeks.
4. Use run_sql (one read-only SELECT over complaints/recalls) only for questions the tools don't cover.
Stop as soon as you have enough evidence and reply with one short sentence starting with DONE."""

S = {"type": "STRING"}
DECLS = [
    ("detect_emerging", "Find model/component pairs with an unusual rise in complaints over the last 4 weeks.",
     {"model": S, "component": S, "ignore_recall_complaints": {"type": "BOOLEAN"}}, []),
    ("drill_down", "Complaint counts, crashes and injuries for a model+component by model_year or state.",
     {"model": S, "component": S, "by": {"type": "STRING", "enum": ["model_year", "state", "crash", "injured"]}},
     ["model", "component"]),
    ("symptom_terms", "Words over-represented in complaints since a week (YYYY-MM-DD) vs. before it.",
     {"model": S, "component": S, "since": S}, ["model", "component", "since"]),
    ("similar_complaints", "Most relevant complaints for a model+component matching a text query.",
     {"model": S, "component": S, "query": S, "since": S}, ["model", "component", "query"]),
    ("recall_driven_share", "Share of complaints since a week that cite a recall, and which campaigns.",
     {"model": S, "component": S, "since": S}, ["model", "component", "since"]),
    ("rank_recalls", "The model's recalls ranked by relevance to complaints since a week.",
     {"model": S, "component": S, "since": S}, ["model", "component", "since"]),
    ("run_sql", "Run one read-only SQL SELECT over complaints(complaint_id, model, model_year, component, crash, "
     "fire, injured, received_date, week_start, state, description) and recalls(recall_id, model, model_years, "
     "component, report_date, defect_summary).", {"sql": S}, ["sql"]),
    ("search_memory", "Find earlier investigations of the same model or component.",
     {"model": S, "component": S}, ["model", "component"]),
]

def _types():
    from google.genai import types
    return types

def _tool_config():
    t = _types()
    decls = [t.FunctionDeclaration(name=n, description=d,
                                   parameters={"type": "OBJECT", "properties": p, "required": r})
             for n, d, p, r in DECLS]
    return t.GenerateContentConfig(system_instruction=SYSTEM, temperature=0, tools=[t.Tool(function_declarations=decls)],
                                   automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True))

def _call(ctx, name, args, inv_id):
    if name == "detect_emerging":
        return tools.detect_emerging(ctx, model=args.get("model"), component=args.get("component"),
                                     ignore_recall_complaints=args.get("ignore_recall_complaints"))
    if name == "drill_down":
        return tools.drill_down(ctx, model=args["model"], component=args["component"], by=args.get("by", "model_year"))
    if name == "symptom_terms":
        return tools.symptom_terms(ctx, model=args["model"], component=args["component"], since=args["since"])
    if name == "similar_complaints":
        return tools.similar_complaints(ctx, model=args["model"], component=args["component"], query=args["query"],
                                        since=args.get("since"), k=4)
    if name == "recall_driven_share":
        return tools.recall_driven_share(ctx, model=args["model"], component=args["component"], since=args["since"])
    if name == "rank_recalls":
        return tools.rank_recalls(ctx, model=args["model"], component=args["component"], since=args["since"])[:3]
    if name == "run_sql":
        try:
            return tools.run_sql(ctx, sql=args["sql"])
        except UnsafeSQL as e:
            return {"error": f"blocked by SQL guard: {e}"}       # the model sees the refusal and can adapt
    if name == "search_memory":
        return [{"inv_id": p["inv_id"], "conclusion": p["conclusion"]}
                for p in memory.related_past(args["model"], args["component"], exclude=inv_id)]
    return {"error": f"unknown tool {name}"}

def _compact(result):
    """What the model sees: truncated, so long complaint texts can't blow up the context."""
    text = json.dumps(result, default=str)
    return json.loads(text[:6000]) if len(text) <= 6000 else {"truncated": text[:6000]}

def run(state, ctx, add_evidence):
    """Returns updates for the graph state. Raises on any SDK/model failure (caller falls back)."""
    t = _types()
    q = state["question"]
    scope = state["scope"]
    prompt = f"Question: {q}\nCurrent investigation scope: {json.dumps(scope)}"
    contents = [t.Content(role="user", parts=[t.Part.from_text(text=prompt)])]
    steps, facts = [], {"det_new": None, "det_all": None, "onset": None, "terms": [], "recalls": [], "driven": None}
    for _ in range(config.MAX_AGENT_STEPS):
        resp = llm.with_retry(lambda: llm.client().models.generate_content(
            model=config.GEMINI_MODEL, contents=contents, config=_tool_config()), ctx=ctx, name="agent_step")
        llm.record_call(ctx, "agent_step", resp)
        calls = resp.function_calls or []
        if not calls:
            break
        contents.append(resp.candidates[0].content)
        parts = []
        for c in calls:
            args = dict(c.args or {})
            result = _call(ctx, c.name, args, state["inv_id"])
            steps.append(c.name)
            _to_evidence(c.name, args, result, add_evidence, facts, scope)
            parts.append(t.Part.from_function_response(name=c.name, response={"result": _compact(result)}))
        contents.append(t.Content(role="user", parts=parts))
    signal = ("new" if facts["det_new"] else "recall_driven" if facts["det_all"] else
              "none" if facts["det_new"] is not None or facts["det_all"] is not None else None)
    return {"steps": steps, "scope": scope, "onset": facts["onset"], "terms": facts["terms"],
            "recalls": facts["recalls"], "driven": facts["driven"], "signal": signal}

def _to_evidence(name, args, result, add, facts, scope):
    from .nodes import _det_text, _short
    m, c = args.get("model"), args.get("component")
    if m and c:
        scope.update(model=m, component=c)
    if name == "detect_emerging":
        key = "det_all" if args.get("ignore_recall_complaints") is False else "det_new"
        hits = [r for r in result if not c or r["component"] == c]
        facts[key] = hits
        if hits and not facts["onset"]:
            facts["onset"] = hits[0]["onset_week"]
        for r in hits[:5]:
            add("detection", _det_text(r) + ("" if key == "det_new" else " (all complaints, recall-citing included)"),
                [f"DET:{r['model']}/{r['component']}"])
        if not hits:
            add("detection", f"No unusual rise found ({'all complaints' if key == 'det_all' else 'recall-citing complaints excluded'})"
                + (f" for {m} / {c}." if c else "."), [f"DET:{m}/{c}" if c else "DET:all"])
    elif name == "drill_down":
        add("breakdown", f"{m} / {c} by {args.get('by', 'model_year')}: "
            + ", ".join(f"{r['bucket']}: {r['n']}" for r in result[:10]), [])
    elif name == "symptom_terms":
        facts["terms"] = result
        if result:
            add("symptoms", "Over-represented since " + args["since"] + ": "
                + ", ".join(f"'{r['term']}' ({int(r['share_recent'] * 100)}%, {r['lift']}x)" for r in result), [])
    elif name == "similar_complaints":
        for r in result:
            add("complaint", f"C:{r['complaint_id']} (MY{r['model_year']}, {r['received_date']}): {r['description']}",
                [f"C:{r['complaint_id']}"])
    elif name == "recall_driven_share":
        facts["driven"] = result
        add("recall_driven", f"{result['citing_recall']} of {result['total']} complaints since {args['since']} cite a "
            f"recall; campaigns: {', '.join(result['campaigns']) or 'none'}.", [])
    elif name == "rank_recalls":
        facts["recalls"] = result
        for r in result:
            add("recall", f"{r['recall_id']} ({r['report_date']}; {'cited in complaints' if r['cited_by_complaints'] else 'text match'}): "
                f"{_short(r['defect_summary'])}", [r["recall_id"]])
    elif name == "run_sql":
        if result.get("rows"):
            add("sql", f"Query: {result['sql']} -> {result['rows'][:20]}", [])
        elif result.get("error"):
            add("sql", f"Query refused: {result['error']}", [])
    elif name == "search_memory":
        for p in result:
            add("memory", f"Earlier investigation {p['inv_id']} concluded: {p['conclusion']}", [])
