"""Fakes that mimic the parts of the Gemini, BigQuery and Firestore SDKs this project uses.
They prove the logic (tool loop, guard, validation, fallbacks, state parity), not the network calls."""
import importlib, json, sys, types as pytypes
from types import SimpleNamespace as NS

def install_module(monkeypatch, name, module):
    parent = name.rsplit(".", 1)[0]
    try:
        importlib.import_module(parent)
    except ImportError:
        monkeypatch.setitem(sys.modules, parent, pytypes.ModuleType(parent))
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys.modules[parent], name.rsplit(".", 1)[1], module, raising=False)

# ---------------- Gemini ----------------
def fake_genai_types():
    m = pytypes.ModuleType("google.genai.types")
    m.Content = lambda role, parts: NS(role=role, parts=parts)
    m.Part = NS(from_text=lambda text: NS(text=text),
                from_function_response=lambda name, response: NS(name=name, response=response))
    m.FunctionDeclaration = lambda **kw: NS(**kw)
    m.Tool = lambda function_declarations: NS(function_declarations=function_declarations)
    m.GenerateContentConfig = lambda **kw: NS(**kw)
    m.AutomaticFunctionCallingConfig = lambda disable: NS(disable=disable)
    return m

class FakeGemini:
    """Scripted model: `steps` is a list of tool-call lists for the agent loop; JSON prompts
    (planner / report) are answered by `planner` and `report`."""
    def __init__(self, steps, planner, report, fail_loop=False):
        self.steps, self.planner, self.report, self.fail_loop = list(steps), planner, report, fail_loop
        self.models = self
        self.seen_tool_results = []

    def generate_content(self, model, contents, config):
        if isinstance(config, dict):                                  # generate_json path
            text = json.dumps(self.planner if "route quality" in contents else self.report)
            return NS(text=text, usage_metadata=None)
        if self.fail_loop:
            raise RuntimeError("simulated model outage")
        for c in contents:
            for p in getattr(c, "parts", []):
                if hasattr(p, "response"):
                    self.seen_tool_results.append((p.name, p.response))
        calls = self.steps.pop(0) if self.steps else []
        return NS(function_calls=[NS(name=n, args=a) for n, a in calls],
                  candidates=[NS(content=NS(role="model", parts=[]))], text="DONE", usage_metadata=None)

# ---------------- BigQuery ----------------
def fake_bigquery_module():
    m = pytypes.ModuleType("google.cloud.bigquery")
    m.QueryJobConfig = lambda **kw: NS(**kw)
    m.ScalarQueryParameter = lambda name, type_, value: NS(name=name, type_=type_, value=value)
    return m

class FakeBQClient:
    project = "demo-project"
    def __init__(self, rows=None, bytes_processed=1000):
        self.rows, self.bytes_processed, self.calls = rows or [], bytes_processed, []
    def query(self, sql, job_config):
        self.calls.append((sql, job_config))
        return NS(result=lambda: [NS(items=lambda r=r: r.items()) for r in self.rows],
                  total_bytes_processed=self.bytes_processed)

# ---------------- Firestore ----------------
class _Doc:
    def __init__(self, store, path):
        self.store, self.path, self.id = store, path, path[-1]
    def set(self, data, merge=False):
        cur = self.store.setdefault(self.path, {}) if merge else {}
        cur.update(data)
        self.store[self.path] = cur
    def get(self):
        d = self.store.get(self.path)
        return NS(exists=d is not None, to_dict=lambda: dict(d) if d else None)
    def collection(self, name):
        return _Col(self.store, self.path + (name,))

class _Col:
    def __init__(self, store, path):
        self.store, self.path = store, path
    def document(self, doc_id):
        return _Doc(self.store, self.path + (doc_id,))
    def stream(self):
        n = len(self.path) + 1
        return [NS(id=p[-1], to_dict=lambda d=d: dict(d)) for p, d in self.store.items()
                if len(p) == n and p[:-1] == self.path]

class FakeFirestore:
    def __init__(self):
        self.store = {}
    def collection(self, name):
        return _Col(self.store, (name,))
