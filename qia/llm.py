"""Optional Gemini client. Every caller has a deterministic fallback, and every call is audited."""
import json, re, time
from . import config
from .audit import record

def available():
    return config.USE_LLM

_client = None
def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client

def set_client(c):
    """For tests: inject a fake Gemini client."""
    global _client
    _client = c

RETRYABLE = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "DEADLINE_EXCEEDED")

def with_retry(fn, ctx=None, name="llm", attempts=None, base_delay=None, sleep=time.sleep):
    """Retry rate limits and transient errors with exponential backoff (2s, 4s, 8s, 16s by default).
    Anything else, or the final failure, is raised so callers can fall back."""
    attempts = attempts or config.LLM_RETRIES
    delay = base_delay if base_delay is not None else config.LLM_RETRY_BASE_S
    for i in range(attempts + 1):
        try:
            return fn()
        except Exception as e:
            if i == attempts or not any(code in str(e) for code in RETRYABLE):
                raise
            if ctx is not None:
                record(ctx, "llm", "retry", name, {"attempt": i + 1}, str(e)[:200], 0, 0.0, "retry")
            sleep(delay * (2 ** i))

def parse_json(text):
    """First JSON object in the reply; tolerates code fences and trailing text after the object."""
    text = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in model reply: {text[:120]}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj

def embed(texts):
    """Semantic memory search. Returns unit-normalised vectors."""
    resp = with_retry(lambda: client().models.embed_content(model=config.EMBED_MODEL, contents=list(texts)))
    out = []
    for e in resp.embeddings:
        v = list(e.values)
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out

def record_call(ctx, name, resp, t0=None, prompt_chars=None):
    usage = getattr(resp, "usage_metadata", None)
    record(ctx, "llm", "llm", name, {"model": config.GEMINI_MODEL, "prompt_chars": prompt_chars},
           f"tokens_in={getattr(usage, 'prompt_token_count', None)} tokens_out={getattr(usage, 'candidates_token_count', None)}",
           0, (time.perf_counter() - t0) * 1000 if t0 else 0.0, "ok")

def generate_json(ctx, prompt, name):
    t0 = time.perf_counter()
    resp = with_retry(lambda: client().models.generate_content(
        model=config.GEMINI_MODEL, contents=prompt, config={"response_mime_type": "application/json", "temperature": 0}),
        ctx=ctx, name=name)
    usage = getattr(resp, "usage_metadata", None)
    record(ctx, "llm", "llm", name, {"model": config.GEMINI_MODEL, "prompt_chars": len(prompt)},
           f"tokens_in={getattr(usage, 'prompt_token_count', None)} tokens_out={getattr(usage, 'candidates_token_count', None)}",
           0, (time.perf_counter() - t0) * 1000, "ok")
    return parse_json(resp.text)
