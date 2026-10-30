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

def _connect(readonly=False):
    path = config.DB_PATH
    if readonly:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    return con

def init():
    with _connect() as con:
        con.executescript(SCHEMA)

def query(sql, params=(), readonly=True):
    """Read path. Agent-generated SQL always comes here with readonly=True."""
    con = _connect(readonly=readonly)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()

def estimate_bytes(sql):
    """BigQuery: a dry run returns total_bytes_processed before anything is billed.
    SQLite has no equivalent, so locally this returns None and the cap check is skipped."""
    return None

@contextmanager
def tx():
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
