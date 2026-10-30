"""The agent's tools. Each one: plain SQL in the portable subset, audited, returns evidence IDs.
ctx carries inv_id/session_id so the audit trail ties every call to an investigation."""
from collections import Counter
from . import config, warehouse
from .audit import audited
from .sql_guard import guard
from .detect import onset_index, window_flags
from .text import BM25, tokenize

def weekly_series(model=None, component=None, as_of=None, ignore_recall_complaints=None):
    """Complete weeks only: the latest week is dropped if its data isn't finished yet."""
    from datetime import date, timedelta
    where, params = [], []
    if model: where.append("model = ?"); params.append(model)
    if component: where.append("component = ?"); params.append(component)
    if as_of: where.append("week_start <= ?"); params.append(as_of)
    if config.IGNORE_RECALL_COMPLAINTS if ignore_recall_complaints is None else ignore_recall_complaints:
        where.append("LOWER(description) NOT LIKE '%recall%'")
    rows = warehouse.query("SELECT model, component, week_start, COUNT(*) AS n FROM complaints "
                           + (f"WHERE {' AND '.join(where)} " if where else "")
                           + "GROUP BY model, component, week_start", tuple(params))
    bounds = warehouse.query("SELECT MIN(week_start) w0, MAX(week_start) w1, MAX(received_date) last FROM complaints")[0]
    if not bounds["w0"]:
        return [], {}
    w0 = date.fromisoformat(bounds["w0"])
    w1 = date.fromisoformat(min(bounds["w1"], as_of or bounds["w1"]))
    if not as_of and date.fromisoformat(bounds["last"]) < w1 + timedelta(days=6):
        w1 -= timedelta(weeks=1)                      # incomplete current week
    weeks = [str(w0 + timedelta(weeks=i)) for i in range((w1 - w0).days // 7 + 1)]
    idx = {w: i for i, w in enumerate(weeks)}
    series = {}
    for r in rows:
        if r["component"] in config.EXCLUDE_COMPONENTS or r["week_start"] not in idx:
            continue
        series.setdefault((r["model"], r["component"]), [0] * len(weeks))[idx[r["week_start"]]] = r["n"]
    return weeks, series

@audited("tool")
def detect_emerging(ctx, model=None, component=None, recent_weeks=4, as_of=None, ignore_recall_complaints=None):
    """Flag model+component pairs whose last-4-week complaint count is unusually high.
    Returns [] when nothing is flagged; the caller reports how many series were checked."""
    weeks, series = weekly_series(model, component, as_of, ignore_recall_complaints)
    ctx["series_checked"] = len(series)
    issues = []
    for (m, c), counts in series.items():
        flags = window_flags(counts)
        recent = [f for f in flags if f[0] >= len(weeks) - recent_weeks]
        if not recent:
            continue
        i, s, expected, z = max(recent, key=lambda f: f[3])
        flagged = {f[0] for f in flags}
        run_start = i
        while run_start - 1 in flagged:
            run_start -= 1
        issues.append(dict(model=m, component=c, week=weeks[i], count=s, expected=expected, z=z,
                           onset_week=weeks[onset_index(counts, run_start)], last4=counts[i - 3:i + 1],
                           method=f"{config.WINDOW_WEEKS}-week count vs. robust {config.BASELINE_WEEKS}-week baseline"
                                  + ("; complaints citing a recall excluded"
                                     if (config.IGNORE_RECALL_COMPLAINTS if ignore_recall_complaints is None
                                         else ignore_recall_complaints) else "")))
    return sorted(issues, key=lambda x: x["z"], reverse=True)

@audited("tool")
def drill_down(ctx, model, component, by="model_year"):
    if by not in {"model_year", "state", "crash", "fire", "injured"}:
        raise ValueError(f"unsupported breakdown: {by}")
    sql = (f"SELECT {by} AS bucket, COUNT(*) AS n, SUM(crash) AS crashes, SUM(injured) AS injuries "
           f"FROM complaints WHERE model = ? AND component = ? GROUP BY {by} ORDER BY n DESC")
    return warehouse.query(sql, (model, component))

@audited("tool")
def similar_complaints(ctx, model, component, query, since=None, k=5):
    sql = "SELECT complaint_id, model_year, received_date, crash, description FROM complaints WHERE model=? AND component=?"
    params = [model, component]
    if since:
        sql += " AND week_start >= ?"; params.append(since)
    rows = warehouse.query(sql, tuple(params))
    if not rows:
        return []
    scores = BM25([r["description"] for r in rows]).scores(query)
    ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for s, r in ranked:                      # diversity: one complaint per distinct description
        key = r["description"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r, score=round(s, 2)))
        if len(out) == k:
            break
    return out

@audited("tool")
def symptom_terms(ctx, model, component, since, k=6):
    """Explainability: which words are over-represented in recent complaints vs. before the spike."""
    recent = warehouse.query("SELECT description FROM complaints WHERE model=? AND component=? AND week_start >= ?",
                             (model, component, since))
    before = warehouse.query("SELECT description FROM complaints WHERE model=? AND component=? AND week_start < ?",
                             (model, component, since))
    noise = {"extremely", "concerned", "very", "really", "though", "well", "currently", "even", "again", "never",
             "issue", "issues", "problem", "problems", "ford", "dealer", "dealership", "recall", "recalls",
             "vehicle", "car"}
    keep = lambda t: t not in noise and not any(ch.isdigit() for ch in t)
    rc = Counter(t for r in recent for t in set(tokenize(r["description"])) if keep(t))
    bc = Counter(t for r in before for t in set(tokenize(r["description"])))
    nr, nb = max(len(recent), 1), max(len(before), 1)
    lift = {t: (rc[t] / nr) / ((bc[t] + 1) / (nb + 1)) for t in rc if rc[t] >= 3}
    return [dict(term=t, share_recent=round(rc[t] / nr, 2), lift=round(lift[t], 1))
            for t in sorted(lift, key=lift.get, reverse=True)[:k]]

@audited("tool")
def related_recalls(ctx, model, component):
    return warehouse.query("SELECT * FROM recalls WHERE model=? AND component=? ORDER BY report_date", (model, component))

import re as _re
# NHTSA campaign numbers: full "25V467000" or the short form owners write, "25V467"
CAMPAIGN_RE = _re.compile(r"\b(\d{2}[VETC]\d{3})(\d{3})?\b")

def campaigns_in(text):
    return [a + (b or "000") for a, b in CAMPAIGN_RE.findall(text)]

def recent_cutoff(weeks):
    last = warehouse.query("SELECT MAX(week_start) w FROM complaints")[0]["w"]
    from datetime import date, timedelta
    return str(date.fromisoformat(last) - timedelta(weeks=weeks)) if last else "0000-00-00"

@audited("tool")
def recall_driven_share(ctx, model, component, since):
    """Share of complaints since onset that cite a recall or a campaign number."""
    rows = warehouse.query("SELECT description FROM complaints WHERE model=? AND component=? AND week_start >= ?",
                           (model, component, since))
    cited = [r for r in rows if "recall" in r["description"].lower() or CAMPAIGN_RE.search(r["description"])]
    campaigns = Counter(c for r in cited for c in campaigns_in(r["description"]))
    return {"total": len(rows), "citing_recall": len(cited),
            "share": round(len(cited) / len(rows), 2) if rows else 0.0,
            "campaigns": [c for c, _ in campaigns.most_common(3)]}

@audited("tool")
def rank_recalls(ctx, model, component, since):
    """Rank this MODEL's recalls (any component: a fuel-system recall can explain 'engine' complaints)
    by BM25 match with recent complaint text, plus explicit campaign citations and same-component bonus."""
    recs = warehouse.query("SELECT * FROM recalls WHERE model=?", (model,))
    texts = [r["description"] for r in warehouse.query(
        "SELECT description FROM complaints WHERE model=? AND component=? AND week_start >= ?", (model, component, since))]
    if not recs or not texts:
        return []
    joined = " ".join(texts)
    generic = {"ford", "lincoln", "vehicle", "vehicles"} | set(tokenize(model)) | set(tokenize(component))
    q_terms = {t for t in tokenize(joined) if t not in generic and not any(ch.isdigit() for ch in t)}
    scores = BM25([r["defect_summary"] for r in recs]).scores(" ".join(sorted(q_terms)))  # unique terms only
    cited_set = set(campaigns_in(joined))
    out = []
    for sc, r in zip(scores, recs):
        shared = sorted(q_terms & set(tokenize(r["defect_summary"])))
        cited = r["recall_id"].split(":")[0] in cited_set
        same = r["component"] == component
        if not (cited or same or len(shared) >= 3):
            continue                     # other-component recalls must earn their place
        out.append(dict(r, match_score=round(sc + (50 if cited else 0) + (5 if same else 0), 2),
                        shared_terms=shared[:8], cited_by_complaints=cited))
    # an explicit citation by owners beats any text similarity; then same component; then match strength
    return sorted(out, key=lambda x: (x["cited_by_complaints"], x["component"] == component,
                                      x["match_score"], x["report_date"]), reverse=True)

@audited("sql")
def run_sql(ctx, sql):
    """Agent-generated SQL: guarded, then executed on a read-only connection."""
    safe = guard(sql)
    return {"sql": safe, "rows": warehouse.query(safe)}

_vocab_cache = {}
def vocab(ttl_seconds=600):
    """Distinct models/components, cached per process: on BigQuery each lookup is a billed query
    and ~1s of latency, and the vocabulary only changes when data is reloaded."""
    import time
    key = (config.WAREHOUSE, str(config.DB_PATH))
    hit = _vocab_cache.get(key)
    if hit and time.time() - hit[0] < ttl_seconds:
        return hit[1]
    models = [r["model"] for r in warehouse.query("SELECT DISTINCT model FROM complaints")]
    comps = [r["component"] for r in warehouse.query("SELECT DISTINCT component FROM complaints")]
    _vocab_cache[key] = (time.time(), (models, comps))
    return models, comps
