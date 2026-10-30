"""Optional Gemini client. Every caller has a deterministic fallback, and every call is audited."""
import json, re, time
from . import config
from .audit import record

def available():
    return config.USE_LLM

def generate_json(ctx, prompt, name):
    from google import genai
    t0 = time.perf_counter()
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt,
                                          config={"response_mime_type": "application/json", "temperature": 0})
    usage = getattr(resp, "usage_metadata", None)
    record(ctx, "llm", "llm", name, {"model": config.GEMINI_MODEL, "prompt_chars": len(prompt)},
           f"tokens_in={getattr(usage, 'prompt_token_count', None)} tokens_out={getattr(usage, 'candidates_token_count', None)}",
           0, (time.perf_counter() - t0) * 1000, "ok")
    return json.loads(re.sub(r"^```(?:json)?|```$", "", resp.text.strip()).strip())
