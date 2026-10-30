"""Warehouse adapter. Agent code calls query()/execute(); it never knows which engine runs.

SQL stays in a portable subset (no engine-specific date or stats functions; week_start is
precomputed at load time, z-scores are computed in Python) so the same tool SQL runs on
SQLite locally and BigQuery in M2.
"""
import sqlite3
from contextlib import contextmanager
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS complaints(
  complaint_id TEXT PRIMARY KEY, make TEXT, model TEXT, model_year INTEGER, component TEXT,
  crash INTEGER, fire INTEGER, injured INTEGER, fail_date TEXT, received_date TEXT,
  week_start TEXT, state TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS recalls(
  recall_id TEXT PRIMARY KEY, make TEXT, model TEXT, model_years TEXT, component TEXT,
  report_date TEXT, defect_summary TEXT, remedy TEXT);
-- investigation memory (structured)
CREATE TABLE IF NOT EXISTS investigations(
  inv_id TEXT PRIMARY KEY, title TEXT, model TEXT, component TEXT, status TEXT,
  created_at TEXT, updated_at TEXT, conclusion TEXT);
CREATE TABLE IF NOT EXISTS findings(
  finding_id TEXT PRIMARY KEY, inv_id TEXT, session_id TEXT, kind TEXT, content TEXT,
  evidence_ids TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS hypotheses(
  hyp_id TEXT PRIMARY KEY, inv_id TEXT, statement TEXT, confidence TEXT, status TEXT,
  evidence_ids TEXT, created_at TEXT);
-- governance: every tool call, SQL statement and LLM call
CREATE TABLE IF NOT EXISTS audit_log(
  audit_id TEXT PRIMARY KEY, inv_id TEXT, session_id TEXT, actor TEXT, action TEXT, name TEXT,
  input TEXT, output_summary TEXT, row_count INTEGER, latency_ms REAL, status TEXT, created_at TEXT);
"""
DATA_TABLES = {"complaints", "recalls"}
MEMORY_TABLES = {"investigations", "findings", "hypotheses"}

# ---------------- BigQuery backend ----------------
_bq = None
def _bq_client():
    global _bq
    if _bq is None:
        from google.cloud import bigquery
        _bq = bigquery.Client(project=config.GCP_PROJECT or None)
    return _bq

def set_bq_client(client):
    """For tests: inject a fake BigQuery client."""
    global _bq
    _bq = client

def _bq_job_config(params, dry_run=False):
    from google.cloud import bigquery
    types = {bool: "BOOL", int: "INT64", float: "FLOAT64", str: "STRING"}
    return bigquery.QueryJobConfig(
        dry_run=dry_run, use_query_cache=not dry_run,
        default_dataset=f"{_bq_client().project}.{config.BQ_DATASET}",   # lets SQL say FROM complaints
        maximum_bytes_billed=config.MAX_BYTES_BILLED,                    # hard cost cap enforced by BigQuery
        query_parameters=[bigquery.ScalarQueryParameter(None, types.get(type(p), "STRING"), p) for p in params])

def _bq_query(sql, params):
    job = _bq_client().query(sql, job_config=_bq_job_config(params))
    return [dict(row.items()) for row in job.result()]

# ---------------- SQLite backend ----------------
def _connect(readonly=False):
    path = config.DB_PATH
    if readonly:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    return con

def init():
    if config.WAREHOUSE == "bigquery":
        return                      # tables are created by Terraform
    with _connect() as con:
        con.executescript(SCHEMA)

def query(sql, params=(), readonly=True):
    """Read path. Agent-generated SQL always comes here with readonly=True.
    On BigQuery, read-only is enforced by IAM: the agent's service account only has dataViewer."""
    if config.WAREHOUSE == "bigquery":
        return _bq_query(sql, tuple(params))
    con = _connect(readonly=readonly)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()

def estimate_bytes(sql, params=()):
    """BigQuery: a dry run returns total_bytes_processed before anything runs or is billed.
    SQLite has no equivalent, so locally this returns None and the cap check is skipped."""
    if config.WAREHOUSE != "bigquery":
        return None
    job = _bq_client().query(sql, job_config=_bq_job_config(tuple(params), dry_run=True))
    return job.total_bytes_processed

@contextmanager
def tx():
    if config.WAREHOUSE == "bigquery":
        raise RuntimeError("The app never writes to BigQuery; load data with data/load_bigquery.py")
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()

def upsert(con, table, row, key):
    cols = list(row)
    upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT({key}) DO UPDATE SET {upd}", [row[c] for c in cols])
