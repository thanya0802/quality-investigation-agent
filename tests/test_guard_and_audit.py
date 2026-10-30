import sqlite3
import pytest
from qia import audit, tools, warehouse
from qia.sql_guard import UnsafeSQL, guard

CTX = {"inv_id": "inv-test", "session_id": "ses-test"}

@pytest.mark.parametrize("sql", [
    "DELETE FROM complaints", "SELECT 1; DROP TABLE recalls", "SELECT * FROM audit_log",
    "SELECT * FROM investigations", "SELECT * FROM complaints -- x", "UPDATE complaints SET model='X'",
    "SELECT * FROM `proj.dataset.secret_table`"])
def test_guard_blocks_unsafe_or_out_of_scope(sql):
    with pytest.raises(UnsafeSQL):
        guard(sql)

def test_guard_allows_ctes_and_caps_rows():
    s = guard("WITH x AS (SELECT model FROM complaints) SELECT model FROM x")
    assert s.endswith("LIMIT 500")

def test_readonly_connection_blocks_writes(db):
    with pytest.raises(sqlite3.OperationalError):
        warehouse.query("DELETE FROM complaints", readonly=True)

def test_every_tool_call_is_audited(db):
    tools.detect_emerging(CTX)
    tools.related_recalls(CTX, model="VESTA", component="SERVICE BRAKES")
    names = [r["name"] for r in audit.trail("inv-test")]
    assert names == ["detect_emerging", "related_recalls"]

def test_blocked_sql_is_audited_as_error(db):
    with pytest.raises(UnsafeSQL):
        tools.run_sql(CTX, sql="DROP TABLE complaints")
    row = audit.trail("inv-test")[-1]
    assert row["status"] == "error" and "UnsafeSQL" in row["output_summary"]
