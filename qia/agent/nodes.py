"""Root-cause investigation agent.
load_memory -> plan -> investigate (multi-step tool use) -> report -> validate -> persist
Deterministic by default; Gemini (if configured) plans, writes SQL and drafts the report,
and every LLM output is validated against tool evidence before it reaches the engineer."""
import re
from datetime import date
from .. import config, llm, memory, tools
from ..sql_guard import UnsafeSQL
from ..telemetry import new_id

# synonyms engineers use -> NHTSA component prefixes (checked in order; longer phrases first)
ALIASES = {"seat belt": "SEAT BELTS", "seatbelt": "SEAT BELTS", "air bag": "AIR BAGS", "airbag": "AIR BAGS",
           "brake": "SERVICE BRAKES", "braking": "SERVICE BRAKES", "parking brake": "PARKING BRAKE",
           "battery": "ELECTRICAL SYSTEM", "electrical": "ELECTRICAL SYSTEM", "seat": "SEATS",
           "steering": "STEERING", "transmission": "POWER TRAIN", "powertrain": "POWER TRAIN",
           "drivetrain": "POWER TRAIN", "engine": "ENGINE", "suspension": "SUSPENSION", "fuel": "FUEL SYSTEM",
           "camera": "BACK OVER PREVENTION", "backup": "BACK OVER PREVENTION", "lane": "LANE DEPARTURE",
           "collision": "FORWARD COLLISION AVOIDANCE", "cruise": "VEHICLE SPEED CONTROL",
           "door": "LATCHES/LOCKS/LINKAGES", "latch": "LATCHES/LOCKS/LINKAGES", "lock": "LATCHES/LOCKS/LINKAGES",
           "headlight": "EXTERIOR LIGHTING", "light": "EXTERIOR LIGHTING", "windshield": "VISIBILITY",
           "wiper": "VISIBILITY", "tire": "TIRES", "wheel": "WHEELS", "frame": "STRUCTURE", "hybrid": "HYBRID PROPULSION"}
# complaint IDs, synthetic recall IDs, and real NHTSA campaign numbers (e.g. 23V456000)
ID_RE = re.compile(r"\bC:\d+\b|\bRCL-\d{2}V-\d{3}\b|\b\d{2}[VETC]\d{6}\b")
NO_EVIDENCE = "No evidence in the complaint or recall data supports an answer to that."

# ---------------- load memory ----------------
def load_memory(state):
    inv_id = state.get("inv_id")
    if inv_id and memory.get(inv_id):
        inv = memory.get(inv_id)
        return {"scope": {"model": inv["model"], "component": inv["component"]}, "resumed": True,
                "prior_findings": memory.findings(inv_id), "prior_hypothesis": memory.active_hypothesis(inv_id)}
    inv_id = memory.create(title=state["question"][:120])
    return {"inv_id": inv_id, "scope": {"model": None, "component": None}, "resumed": False,
            "prior_findings": [], "prior_hypothesis": None}

# ---------------- plan ----------------
def plan(state):
    q, ql = state["question"], state["question"].lower()
    ctx = _ctx(state)
    models, comps = tools.vocab()
    scope = dict(state["scope"])
    hits = [m for m in models if re.search(rf"(?<![\w-]){re.escape(m.lower())}(?![\w-])", ql)]
    if hits:
        scope["model"] = max(hits, key=len)          # "MUSTANG MACH-E" beats "MUSTANG"
    for k, v in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(k)}s?\b", ql):
            matches = [c for c in comps if c.startswith(v)]
            if matches:
                scope["component"] = _busiest(matches, scope.get("model"))
                break
    else:
        # fallback: any NHTSA component whose own words appear in the question ("visibility", "structure")
        q_words = set(re.findall(r"[a-z]+", ql))
        matches = [c for c in comps if {w.rstrip("s") for w in re.findall(r"[a-z]{4,}", c.lower())}
                   & {w.rstrip("s") for w in q_words}]
        if matches:
            scope["component"] = _busiest(matches, scope.get("model"))
    intent = None
    if llm.available():
        try:
            p = llm.generate_json(ctx, PLAN_PROMPT.format(q=q, models=models, comps=comps, scope=scope), "planner")
            if p.get("intent") in INTENTS:
                intent = p["intent"]
                scope["model"] = p.get("model") if p.get("model") in models else scope["model"]
                scope["component"] = p.get("component") if p.get("component") in comps else scope["component"]
        except Exception:
            pass
    rule = _rule_intent(ql, scope)
    if intent is None or rule == "refuse":       # the write-refusal rule can't be overridden by the LLM
        intent = rule
    if scope != state["scope"]:
        memory.update_scope(state["inv_id"], scope.get("model"), scope.get("component"))
    return {"scope": scope, "intent": intent}

def _busiest(components, model):
    """Real NHTSA has several brake components; pick the one with the most complaints in scope."""
    from .. import warehouse
    q = "SELECT component, COUNT(*) n FROM complaints WHERE component IN (%s)" % ",".join("?" * len(components))
    params = list(components)
    if model:
        q += " AND model = ?"; params.append(model)
    rows = warehouse.query(q + " GROUP BY component ORDER BY n DESC", tuple(params))
    return rows[0]["component"] if rows else components[0]

INTENTS = ["refuse", "unsupported", "detect", "investigate", "breakdown_year", "breakdown_state", "safety", "similar", "recalls", "summarize", "sql"]

def _rule_intent(ql, scope):
    if re.search(r"\b(delete|drop|update|insert|truncate|modify|overwrite|remove)\b", ql): return "refuse"
    if re.search(r"summar|so far|what do we know|findings|conclusion", ql): return "summarize"
    if re.search(r"model year|by year|which years", ql): return "breakdown_year"
    if re.search(r"\bstate|region|where are", ql): return "breakdown_state"
    if re.search(r"crash|injur|fire|safety", ql): return "safety"
    if re.search(r"similar|example|show (me )?complaints", ql): return "similar"
    if re.search(r"recall", ql) and scope.get("model"): return "recalls"
    if re.search(r"how many|count|number of|average|per month|per week", ql): return "sql"
    if re.search(r"emerging|new issue|spike|trending|anything unusual", ql) and not scope.get("component"): return "detect"
    if scope.get("model") or scope.get("component"): return "investigate"
    if re.search(r"investigat|root cause|why|what.s wrong", ql): return "investigate"
    return "unsupported"

PLAN_PROMPT = """You route quality-engineering questions over vehicle complaint data.
Intents: {intents}
Known models: {models}. Known components: {comps}. Current investigation scope: {scope}.
Question: {q}
Return JSON: {{"intent": "...", "model": "... or null", "component": "... or null"}}""".replace("{intents}", ", ".join(INTENTS))

# ---------------- investigate ----------------
def investigate(state):
    """Agentic (Gemini picks tools) when configured; deterministic pipeline otherwise or on any failure."""
    if config.AGENT_MODE == "agentic" and llm.available() and state["intent"] in ("investigate", "detect", "sql"):
        ev = list(state.get("evidence", []))
        def add(kind, text, ids):
            ev.append({"eid": f"E{len(ev) + 1}", "kind": kind, "text": text, "ids": sorted(set(ids))})
        try:
            from . import agentic
            out = agentic.run({**state, "scope": dict(state["scope"])}, _ctx(state), add)
            if out["steps"]:
                if out["scope"] != state["scope"]:
                    memory.update_scope(state["inv_id"], out["scope"].get("model"), out["scope"].get("component"))
                intent = "investigate" if out["scope"].get("component") else state["intent"]
                return {**out, "evidence": ev, "intent": intent, "answer_mode": "agentic"}
        except Exception as e:                      # any SDK/model failure -> deterministic path
            from ..audit import record
            record(_ctx(state), "agent", "fallback", "agentic_loop", {}, f"{type(e).__name__}: {e}", 0, 0.0, "error")
    return deterministic_investigate(state)

def deterministic_investigate(state):
    ctx, scope, intent = _ctx(state), state["scope"], state["intent"]
    ev, steps = list(state.get("evidence", [])), []
    def add(kind, text, ids):
        ev.append({"eid": f"E{len(ev) + 1}", "kind": kind, "text": text, "ids": sorted(set(ids))})
    out = {}

    if intent == "detect" or (intent == "investigate" and not scope.get("component")):
        issues = tools.detect_emerging(ctx, model=scope.get("model")); steps.append("detect_emerging")
        for it in issues:
            add("detection", _det_text(it), [f"DET:{it['model']}/{it['component']}"])
        if not issues:
            add("detection", f"No {'component of ' + scope['model'] if scope.get('model') else 'model/component'} "
                f"shows an unusual rise in the last 4 complete weeks "
                f"({ctx.get('series_checked', 0)} model/component series checked).", ["DET:all"])
            if scope.get("model"):
                top = tools.run_sql(ctx, sql=f"SELECT component, COUNT(*) AS n FROM complaints WHERE model = "
                                    f"'{scope['model']}' GROUP BY component ORDER BY n DESC LIMIT 5")
                steps.append("run_sql")
                add("breakdown", f"Most-complained components for {scope['model']}: "
                    + ", ".join(f"{r['component']} {r['n']}" for r in top["rows"]) + ".", [])
        out["issues"] = issues
        if intent == "investigate" and issues:
            # most urgent = no recall already addressing it, then most unusual
            def has_recall(it):
                return bool(tools.related_recalls(ctx, model=it["model"], component=it["component"]))
            top = sorted(issues, key=lambda it: (has_recall(it), -it["z"]))[0]
            scope = {"model": top["model"], "component": top["component"]}
            memory.update_scope(state["inv_id"], scope["model"], scope["component"])
            intent = "investigate"
        elif intent == "detect":
            return {"evidence": ev, "steps": steps, **out}

    m, c = scope.get("model"), scope.get("component")
    if intent in ("investigate",) and m and c:
        # Two views of the same series: complaints about NEW problems (recall-citing ones excluded) and
        # ALL complaints. A rise only in the second view is a recall-driven surge (remedy/parts delays).
        prior = [i for i in out.get("issues", []) if i["model"] == m and i["component"] == c]
        if prior:
            ev = [e for e in ev if not (e["kind"] == "detection" and e["ids"] != [f"DET:{m}/{c}"])]
            for e_i, e in enumerate(ev):
                e["eid"] = f"E{e_i + 1}"
        det_new = prior or tools.detect_emerging(ctx, model=m, component=c, ignore_recall_complaints=True)
        det_all = [] if det_new else tools.detect_emerging(ctx, model=m, component=c, ignore_recall_complaints=False)
        steps.append("detect_emerging")
        signal = "new" if det_new else "recall_driven" if det_all else "none"
        det = det_new or det_all
        onset = det[0]["onset_week"] if det else None
        if signal == "new" and not prior:
            add("detection", _det_text(det[0]), [f"DET:{m}/{c}"])
        elif signal == "recall_driven":
            d = det[0]
            add("detection", f"{m} / {c}: {d['count']} complaints in the 4 weeks ending {d['week']} vs {d['expected']} "
                f"expected (z={d['z']}), but the rise disappears once complaints citing a recall are excluded; "
                f"elevated since {onset}.", [f"DET:{m}/{c}"])
        elif signal == "none":
            add("detection", f"{m} / {c}: no unusual rise in the last 4 complete weeks, with or without "
                "recall-citing complaints.", [f"DET:{m}/{c}"])
        yrs = tools.drill_down(ctx, model=m, component=c, by="model_year"); steps.append("drill_down")
        total = sum(r["n"] for r in yrs) or 1
        add("breakdown", "By model year: " + ", ".join(f"MY{r['bucket']} {r['n']} ({r['n'] * 100 // total}%)" for r in yrs)
            + f"; crashes {sum(r['crashes'] or 0 for r in yrs)}, injuries {sum(r['injuries'] or 0 for r in yrs)}.", [])
        terms = []
        if onset:
            terms = tools.symptom_terms(ctx, model=m, component=c, since=onset); steps.append("symptom_terms")
            if terms:
                add("symptoms", "Over-represented since onset: " + ", ".join(
                    f"'{t['term']}' ({int(t['share_recent'] * 100)}% of complaints, {t['lift']}x)" for t in terms), [])
            query = " ".join(t["term"] for t in terms) or c
            sims = tools.similar_complaints(ctx, model=m, component=c, query=query, since=onset, k=4)
            steps.append("similar_complaints")
            for s in sims:
                add("complaint", f"C:{s['complaint_id']} (MY{s['model_year']}, {s['received_date']}"
                    f"{', crash' if s['crash'] else ''}): {s['description']}", [f"C:{s['complaint_id']}"])
        since = onset or tools.recent_cutoff(26)
        driven = tools.recall_driven_share(ctx, model=m, component=c, since=since); steps.append("recall_driven_share")
        if driven["total"] and driven["citing_recall"]:
            add("recall_driven", f"{driven['citing_recall']} of {driven['total']} complaints since {since} "
                f"({int(driven['share'] * 100)}%) mention a recall"
                + (f", most often campaign(s) {', '.join(driven['campaigns'])}" if driven["campaigns"] else "") + ".", [])
        recs = tools.rank_recalls(ctx, model=m, component=c, since=since); steps.append("rank_recalls")
        for r in recs[:3]:
            lag = ""
            if onset:
                weeks = (date.fromisoformat(r["report_date"]) - date.fromisoformat(onset)).days // 7
                lag = f" Complaint rise began {weeks} weeks before this recall." if weeks >= 0 else ""
            why = ("cited in complaints" if r["cited_by_complaints"] else
                   f"shared terms: {', '.join(r['shared_terms'][:5])}" if r["shared_terms"] else "same component")
            add("recall", f"{r['recall_id']} ({r['report_date']}, MY {r['model_years']}; {why}): "
                f"{_short(r['defect_summary'])}{lag}", [r["recall_id"]])
        if not recs:
            add("recall", f"No recall on file for {m}.", [])
        past = memory.related_past(m, c, exclude=state["inv_id"], query_text=state["question"])
        for p in past:
            add("memory", f"Earlier investigation {p['inv_id']} ({p['updated_at'][:10]}) concluded: {p['conclusion']}", [])
        out.update(onset=onset, terms=terms, recalls=recs[:3], driven=driven, signal=signal)

    elif intent in ("breakdown_year", "breakdown_state", "safety") and m and c:
        by = {"breakdown_year": "model_year", "breakdown_state": "state", "safety": "crash"}[intent]
        rows = tools.drill_down(ctx, model=m, component=c, by=by); steps.append("drill_down")
        label = {"model_year": "MY", "state": "", "crash": "crash="}[by]
        add("breakdown", f"{m} / {c} by {by}: " + ", ".join(f"{label}{r['bucket']} {r['n']}" for r in rows)
            + f"; crashes {sum(r['crashes'] or 0 for r in rows)}, injuries {sum(r['injuries'] or 0 for r in rows)}.", [])
    elif intent == "similar" and m and c:
        sims = tools.similar_complaints(ctx, model=m, component=c, query=state["question"], k=5); steps.append("similar_complaints")
        for s in sims:
            add("complaint", f"C:{s['complaint_id']} (MY{s['model_year']}, {s['received_date']}): {s['description']}",
                [f"C:{s['complaint_id']}"])
    elif intent == "recalls" and m and c:
        recs = tools.related_recalls(ctx, model=m, component=c); steps.append("related_recalls")
        for r in recs:
            add("recall", f"{r['recall_id']} ({r['report_date']}): {r['defect_summary']}", [r["recall_id"]])
        if not recs:
            add("recall", f"No recall on file for {m} / {c}.", [])
    elif intent == "sql":
        res = _nl_to_sql(ctx, state["question"], scope); steps.append("run_sql")
        out["sql"] = res
        if res.get("rows"):
            add("sql", f"Query: {res['sql']} -> {res['rows'][:20]}", [])
    elif intent == "summarize":
        pass  # handled in report from memory
    return {"evidence": ev, "steps": steps, "scope": scope, "intent": intent, **out}

def _short(text, n=200):
    """Recall summaries start with 'X is recalling certain ...'; keep the defect sentence."""
    sents = re.split(r"(?<=\.)\s+", text.strip())
    body = " ".join(x for x in sents if "recalling" not in x.lower()) or text
    return body if len(body) <= n else body[:n].rsplit(" ", 1)[0] + "..."

def _det_text(it):
    return (f"{it['model']} / {it['component']}: {it['count']} complaints in the 4 weeks ending {it['week']} vs "
            f"{it['expected']} expected (z={it['z']}; {it['method']}); elevated since {it['onset_week']}.")

SQL_TEMPLATES = [
    (re.compile(r"how many complaints"), lambda s: (
        "SELECT model, component, COUNT(*) AS complaints FROM complaints"
        + (f" WHERE model = '{s['model']}'" if s.get("model") else "")
        + (f"{' AND' if s.get('model') else ' WHERE'} component = '{s['component']}'" if s.get("component") else "")
        + " GROUP BY model, component ORDER BY complaints DESC")),
]

def _nl_to_sql(ctx, question, scope):
    sql = None
    for pat, build in SQL_TEMPLATES:
        if pat.search(question.lower()):
            sql = build(scope); break
    if sql is None and llm.available():
        try:
            models, comps = tools.vocab()
            sql = llm.generate_json(ctx, SQL_PROMPT.format(q=question, scope=scope, models=models, comps=comps),
                                    "nl_to_sql")["sql"]
        except Exception:
            sql = None
    if sql is None:
        return {"sql": None, "rows": [], "error": "no SQL template matched and no LLM configured"}
    try:
        return tools.run_sql(ctx, sql=sql)
    except UnsafeSQL as e:
        return {"sql": sql, "rows": [], "error": f"blocked by SQL guard: {e}"}

SQL_PROMPT = """Write ONE read-only SQL SELECT over these tables. Use only portable SQL that runs on both
SQLite and BigQuery: no date functions; dates are 'YYYY-MM-DD' strings, so compare them with string
literals (e.g. received_date >= '2025-01-01'). crash and fire are 0/1; injured is a count.
`make` is the manufacturer (e.g. FORD). Vehicle names such as F-150 or EXPLORER are values of `model`.
Return only the columns the question asks for.
complaints(complaint_id, make, model, model_year, component, crash, fire, injured, fail_date, received_date, week_start, state, description)
recalls(recall_id, make, model, model_years, component, report_date, defect_summary, remedy)
Exact model values: {models}
Exact component values: {comps}
Current investigation scope: {scope}. Question: {q}
Return JSON: {{"sql": "..."}}"""

# ---------------- report ----------------
def report(state):
    ctx, ev, scope, intent = _ctx(state), state.get("evidence", []), state["scope"], state["intent"]
    if intent == "summarize":
        return _summary(state)
    if intent == "refuse":
        return {"answer": "This agent has read-only access to quality data and can't change or delete records.",
                "hypothesis": None, "answered": False, "answer_by": "policy"}
    if not ev:
        return {"answer": NO_EVIDENCE, "hypothesis": None, "answered": False, "answer_by": "deterministic"}
    if llm.available():
        try:
            blocks = "\n".join(f"[{e['eid']}] ({e['kind']}) {e['text']}" for e in ev)
            out = llm.generate_json(ctx, REPORT_PROMPT.format(q=state["question"], ev=blocks), "report")
            return {"answer": out["answer"], "hypothesis": out.get("hypothesis"), "confidence": out.get("confidence"),
                    "answered": True, "answer_by": "llm"}
        except Exception:
            pass
    return {**_deterministic_report(state), "answer_by": "deterministic"}

REPORT_PROMPT = """You are writing for a vehicle quality engineer. Use ONLY the evidence. Cite evidence IDs like [E3]
after each claim and keep complaint IDs (C:...) and recall IDs exactly as written. Separate facts from the hypothesis.
Question: {q}
Evidence:
{ev}
Return JSON: {{"answer": "...", "hypothesis": "... or null", "confidence": "low|medium|high"}}"""

def _deterministic_report(state):
    ev, scope, intent = state["evidence"], state["scope"], state["intent"]
    by_kind = {}
    for e in ev:
        by_kind.setdefault(e["kind"], []).append(e)
    cite = lambda e: f"{e['text']} [{e['eid']}]"
    lines, hyp, conf = [], None, None
    if intent == "detect":
        lines.append("Emerging issues (ranked by how unusual the recent complaint volume is):")
        lines += [f"- {cite(e)}" for e in by_kind.get("detection", [])]
        return {"answer": "\n".join(lines), "hypothesis": None, "answered": True}
    if intent != "investigate":
        lines += [f"- {cite(e)}" for e in ev]
        return {"answer": "\n".join(lines), "hypothesis": None, "answered": True}
    m, c = scope["model"], scope["component"]
    lines.append(f"Investigation: {m} / {c}" if c else f"Investigation: {m or 'all models'} (all components)")
    for kind, title in [("detection", "What is happening"), ("breakdown", "Who is affected"),
                        ("symptoms", "Symptoms"), ("recall_driven", "Recall references in complaints"),
                        ("complaint", "Representative complaints"),
                        ("recall", "Most relevant recalls"), ("memory", "Prior investigations")]:
        if by_kind.get(kind):
            lines.append(f"{title}:")
            lines += [f"- {cite(e)}" for e in by_kind[kind]]
    driven, signal = state.get("driven") or {}, state.get("signal")
    recs = state.get("recalls") or []
    top = recs[0] if recs else None
    top_terms = ", ".join(t["term"] for t in state.get("terms", [])[:4])
    if signal in (None, "none"):
        hyp = None          # no unusual rise: report the facts, don't invent a root cause
    elif signal == "recall_driven":
        cited = driven.get("campaigns") or []
        same = [cp for cp in cited if any(r["recall_id"].startswith(cp) and r["component"] == c for r in recs)]
        camps = ", ".join(same or cited) or (top["recall_id"].split(":")[0] if top else "an existing recall")
        conf = "high" if driven.get("share", 0) >= 0.7 else "medium"
        hyp = (f"Recall-driven rise, not a new defect: the increase disappears when complaints citing a recall are "
               f"excluded ({int(driven.get('share', 0) * 100)}% cite one, mainly {camps}), typically remedy or parts "
               f"delays. Next: check remedy and parts availability for {camps}.")
    elif top and top["component"] == c and len(top["shared_terms"]) >= 3 and not top["cited_by_complaints"]:
        conf = "high" if len(top["shared_terms"]) >= 4 else "medium"
        hyp = (f"Complaint pattern is consistent with {top['recall_id']} ({_short(top['defect_summary'], 140)}). "
               f"Shared terms: {', '.join(top['shared_terms'][:6])}. Next: confirm affected VINs fall inside the "
               f"recall population and track whether complaints decline after remedy.")
    elif top and (top["cited_by_complaints"] or top["shared_terms"]):
        conf = "medium"
        overl = [r for r in recs if r["cited_by_complaints"]] or [top]
        named = "; ".join(f"{r['recall_id'].split(':')[0]} ({r['component']}: {_short(r['defect_summary'], 90)})"
                          for r in overl[:2])
        hyp = (f"Possible new issue: the rise persists after excluding recall-citing complaints, with symptoms "
               f"'{top_terms}'. Owners link it to {named}. Next: determine whether these failures fall within "
               f"{'those recalls' if len(overl) > 1 else 'that recall'}' scope or are a separate issue; pull "
               f"diagnostic codes and repair orders for the affected model years.")
    else:
        conf = "medium"
        hyp = (f"Unaddressed emerging issue: {m} {c} complaints dominated by '{top_terms}' with no matching recall. "
               f"Next: pull warranty claims and diagnostic trouble codes for the affected model years, and request "
               f"returned parts for teardown.")
    if hyp:
        lines.append(f"Hypothesis ({conf} confidence): {hyp}")
    return {"answer": "\n".join(lines), "hypothesis": hyp, "confidence": conf, "answered": True}

def _summary(state):
    f, h = state.get("prior_findings", []), state.get("prior_hypothesis")
    if not f and not h:
        return {"answer": "This investigation has no findings yet.", "hypothesis": None, "answered": False,
                "answer_by": "memory"}
    lines = [f"Investigation {state['inv_id']} scope: {state['scope']['model']} / {state['scope']['component']}",
             f"{len(f)} findings recorded across {len({x['session_id'] for x in f})} session(s)."]
    lines += [f"- [{x['kind']}] {x['content'][:220]}" for x in f[-8:]]
    if h:
        lines.append(f"Current hypothesis ({h['confidence']}): {h['statement']}")
    return {"answer": "\n".join(lines), "hypothesis": None, "answered": True, "answer_by": "memory"}

# ---------------- validate ----------------
def validate(state):
    """Citations must point at evidence produced by tools (or stored memory); IDs must not be invented."""
    ev = state.get("evidence", [])
    known_ids = {i for e in ev for i in e["ids"]} | set(ID_RE.findall(" ".join(e["text"] for e in ev)))
    for f in state.get("prior_findings", []):
        known_ids |= set(f["evidence_ids"])
    text = (state.get("answer") or "") + " " + (state.get("hypothesis") or "")
    issues = []
    cited = {e for grp in re.findall(r"\[((?:E\d+\s*,?\s*)+)\]", text) for e in re.findall(r"E\d+", grp)}
    if cited - {e["eid"] for e in ev}:
        issues.append(f"cites unknown evidence {sorted(cited - {e['eid'] for e in ev})}")
    if invented := set(ID_RE.findall(text)) - known_ids:
        issues.append(f"mentions IDs not in evidence {sorted(invented)}")
    if state.get("answered") and state.get("intent") != "summarize" and ev and not cited:
        issues.append("answer has no citations")
    upd = {"validation": {"passed": not issues, "issues": issues}}
    if issues and state.get("answer_by") == "llm":        # never show an ungrounded LLM report
        upd.update(_deterministic_report(state), answer_by="deterministic_fallback")
    return upd

# ---------------- persist ----------------
def persist(state):
    inv, sid = state["inv_id"], state["session_id"]
    for e in state.get("evidence", []):
        if e["kind"] in ("detection", "breakdown", "symptoms", "recall_driven", "recall", "sql"):
            memory.add_finding(inv, sid, e["kind"], e["text"], e["ids"])
    cids = [i for e in state.get("evidence", []) if e["kind"] == "complaint" for i in e["ids"]]
    if cids:
        memory.add_finding(inv, sid, "complaints", f"{len(cids)} representative complaints reviewed", cids)
    if state.get("hypothesis") and state.get("validation", {}).get("passed"):
        ids = [i for e in state.get("evidence", []) for i in e["ids"]]
        clean = re.sub(r"\s*\[(?:E\d+\s*,?\s*)+\]", "", state["hypothesis"]).strip()
        memory.add_hypothesis(inv, clean, state.get("confidence") or "medium", ids)
    return {}

def _ctx(state):
    return {"inv_id": state.get("inv_id"), "session_id": state.get("session_id")}

def new_session():
    return new_id("ses")
