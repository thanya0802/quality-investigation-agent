"""Every model-written query passes here before it reaches the warehouse.
Defense in depth: static checks + read-only connection (SQLite mode=ro / BigQuery viewer SA)
+ a bytes-billed cap enforced by the dry run in M2."""
import re
from . import config, warehouse

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|merge|attach|detach|pragma|"
                       r"vacuum|truncate|grant|revoke|export|load|call|execute)\b", re.I)

class UnsafeSQL(ValueError):
    pass

def guard(sql, allowed_tables=warehouse.DATA_TABLES, max_rows=500):
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise UnsafeSQL("multiple statements")
    if "--" in s or "/*" in s:
        raise UnsafeSQL("comments are not allowed")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise UnsafeSQL("only SELECT/WITH queries are allowed")
    if m := FORBIDDEN.search(s):
        raise UnsafeSQL(f"forbidden keyword: {m.group(0)}")
    ctes = {c.lower() for c in re.findall(r"\b(\w+)\s+as\s*\(", s, re.I)}
    tables = {t.lower().split(".")[-1].strip("`") for t in re.findall(r"\b(?:from|join)\s+([`\w.\-]+)", s, re.I)}
    if bad := tables - set(allowed_tables) - ctes:
        raise UnsafeSQL(f"table(s) not allowed: {sorted(bad)}")
    if not re.search(r"\blimit\s+\d+\s*$", s, re.I):
        s += f" LIMIT {max_rows}"
    est = warehouse.estimate_bytes(s)          # BigQuery dry run: bytes scanned, before anything runs
    if est is not None and est > config.MAX_BYTES_BILLED:
        raise UnsafeSQL(f"query would scan {est:,} bytes, above the {config.MAX_BYTES_BILLED:,} cap")
    return s
