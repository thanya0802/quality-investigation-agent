"""Operational state: investigation memory and the audit log.

Analytics data lives in the warehouse (SQLite locally, BigQuery on GCP). State is different:
small, frequent writes and in-place updates (scope changes, superseded hypotheses), which suit an
operational store, not an analytics warehouse. Locally: SQLite. On GCP: Firestore.
Both backends implement the same methods, so memory.py and audit.py never know which one runs."""
import json, time
from . import config, warehouse

class SQLiteState:
    def create_investigation(self, row):
        with warehouse.tx() as con:
            con.execute("INSERT INTO investigations VALUES (?,?,?,?,?,?,?,?)",
                        (row["inv_id"], row["title"], row["model"], row["component"], row["status"],
                         row["created_at"], row["updated_at"], row["conclusion"]))

    def get_investigation(self, inv_id):
        rows = warehouse.query("SELECT * FROM investigations WHERE inv_id=?", (inv_id,))
        return rows[0] if rows else None

    def update_investigation(self, inv_id, fields):
        with warehouse.tx() as con:
            for k, v in fields.items():
                con.execute(f"UPDATE investigations SET {k}=? WHERE inv_id=?", (v, inv_id))

    def list_investigations(self, limit=100):
        return warehouse.query("SELECT * FROM investigations ORDER BY updated_at DESC LIMIT ?", (limit,))

    def add_finding(self, row):
        with warehouse.tx() as con:
            con.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?)",
                        (row["finding_id"], row["inv_id"], row["session_id"], row["kind"], row["content"],
                         json.dumps(row["evidence_ids"]), row["created_at"]))

    def findings(self, inv_id):
        rows = warehouse.query("SELECT * FROM findings WHERE inv_id=? ORDER BY created_at, rowid", (inv_id,))
        for r in rows:
            r["evidence_ids"] = json.loads(r["evidence_ids"])
        return rows

    def add_hypothesis(self, row):
        with warehouse.tx() as con:
            con.execute("UPDATE hypotheses SET status='superseded' WHERE inv_id=? AND status='active'", (row["inv_id"],))
            con.execute("INSERT INTO hypotheses VALUES (?,?,?,?,?,?,?)",
                        (row["hyp_id"], row["inv_id"], row["statement"], row["confidence"], "active",
                         json.dumps(row["evidence_ids"]), row["created_at"]))

    def active_hypothesis(self, inv_id):
        rows = warehouse.query("SELECT * FROM hypotheses WHERE inv_id=? AND status='active'", (inv_id,))
        return rows[0] if rows else None

    def add_audit(self, row):
        with warehouse.tx() as con:
            con.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        tuple(row[k] for k in AUDIT_FIELDS))

    def audit_trail(self, inv_id):
        return warehouse.query("SELECT * FROM audit_log WHERE inv_id=? ORDER BY created_at, rowid", (inv_id,))

AUDIT_FIELDS = ["audit_id", "inv_id", "session_id", "actor", "action", "name", "input", "output_summary",
                "row_count", "latency_ms", "status", "created_at"]

class FirestoreState:
    """investigations/{inv_id}, with findings/ and hypotheses/ subcollections; audit_log/{audit_id}.
    Ordering and filtering happen in Python: fine at portfolio scale (tens of investigations) and
    avoids composite indexes. At production scale, add indexes and query server-side."""
    def __init__(self, client=None):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client(project=config.GCP_PROJECT or None, database=config.FIRESTORE_DB)
        self.db = client

    def _inv(self, inv_id):
        return self.db.collection("investigations").document(inv_id)

    def create_investigation(self, row):
        self._inv(row["inv_id"]).set(row)

    def get_investigation(self, inv_id):
        snap = self._inv(inv_id).get()
        return snap.to_dict() if snap.exists else None

    def update_investigation(self, inv_id, fields):
        self._inv(inv_id).set(fields, merge=True)

    def list_investigations(self, limit=100):
        rows = [d.to_dict() for d in self.db.collection("investigations").stream()]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[:limit]

    def add_finding(self, row):
        self._inv(row["inv_id"]).collection("findings").document(row["finding_id"]).set({**row, "seq": time.time_ns()})

    def findings(self, inv_id):
        rows = [d.to_dict() for d in self._inv(inv_id).collection("findings").stream()]
        return [{k: v for k, v in r.items() if k != "seq"} for r in sorted(rows, key=lambda r: r["seq"])]

    def add_hypothesis(self, row):
        col = self._inv(row["inv_id"]).collection("hypotheses")
        for d in col.stream():
            if d.to_dict().get("status") == "active":
                col.document(d.id).set({"status": "superseded"}, merge=True)
        col.document(row["hyp_id"]).set({**row, "status": "active"})

    def active_hypothesis(self, inv_id):
        for d in self._inv(inv_id).collection("hypotheses").stream():
            r = d.to_dict()
            if r.get("status") == "active":
                return r
        return None

    def add_audit(self, row):
        self.db.collection("audit_log").document(row["audit_id"]).set({**row, "seq": time.time_ns()})

    def audit_trail(self, inv_id):
        rows = [d.to_dict() for d in self.db.collection("audit_log").stream() if d.to_dict().get("inv_id") == inv_id]
        return [{k: v for k, v in r.items() if k != "seq"} for r in sorted(rows, key=lambda r: r["seq"])]

_state = None
def get():
    global _state
    want = FirestoreState if config.STATE_STORE == "firestore" else SQLiteState
    if not isinstance(_state, want):
        _state = want()
    return _state

def set_backend(backend):
    """For tests: inject a backend (e.g. FirestoreState with a fake client)."""
    global _state
    _state = backend
