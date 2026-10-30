"""Governance: an append-only record of what the agent did, with what input, and what came back.
Every tool is wrapped with @audited, so nothing can run without leaving a row."""
import functools, json, time
from . import state
from .telemetry import log, new_id, now

def record(ctx, actor, action, name, inp, out_summary, rows, latency_ms, status):
    state.get().add_audit(dict(audit_id=new_id("aud"), inv_id=ctx.get("inv_id"), session_id=ctx.get("session_id"),
                               actor=actor, action=action, name=name, input=json.dumps(inp, default=str)[:4000],
                               output_summary=str(out_summary)[:1000], row_count=rows,
                               latency_ms=round(latency_ms, 1), status=status, created_at=now()))
    log(inv_id=ctx.get("inv_id"), node=name, action=action, latency_ms=round(latency_ms, 1), status=status, rows=rows)

def audited(action):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ctx, **kwargs):
            t0 = time.perf_counter()
            try:
                out = fn(ctx, **kwargs)
            except Exception as e:
                record(ctx, "agent", action, fn.__name__, kwargs, f"{type(e).__name__}: {e}", 0,
                       (time.perf_counter() - t0) * 1000, "error")
                raise
            rows = len(out) if isinstance(out, list) else len(out.get("rows", [])) if isinstance(out, dict) else 0
            record(ctx, "agent", action, fn.__name__, kwargs, _summary(out), rows,
                   (time.perf_counter() - t0) * 1000, "ok")
            return out
        return wrapper
    return deco

def _summary(out):
    if isinstance(out, list):
        return f"{len(out)} rows"
    if isinstance(out, dict):
        return ", ".join(f"{k}={v if not isinstance(v, list) else len(v)}" for k, v in list(out.items())[:6])
    return str(out)[:200]

def trail(inv_id):
    return state.get().audit_trail(inv_id)
