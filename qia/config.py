"""All settings in one place. M2 switches WAREHOUSE=bigquery without touching agent code."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("QIA_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("QIA_DB", DATA_DIR / "quality.db"))
WAREHOUSE = os.getenv("WAREHOUSE", "sqlite")          # sqlite (local) | bigquery
STATE_STORE = os.getenv("STATE_STORE", "sqlite")      # sqlite (local) | firestore
GCP_PROJECT = os.getenv("GCP_PROJECT", "")
BQ_DATASET = os.getenv("BQ_DATASET", "quality")
FIRESTORE_DB = os.getenv("FIRESTORE_DB", "(default)")
MAX_BYTES_BILLED = int(os.getenv("MAX_BYTES_BILLED", 1_000_000_000))  # 1 GB cap per query

# emerging-issue detection (see qia/detect.py)
WINDOW_WEEKS = 4          # compare the last 4 weeks...
BASELINE_WEEKS = 26       # ...with what the previous 26 weeks predict
Z_THRESHOLD = 3.0
MIN_WINDOW_COUNT = 8      # at least 8 complaints in the window
MIN_RATIO = 2.0           # and at least double the expected count
EXCLUDE_COMPONENTS = {"UNKNOWN OR OTHER"}   # not actionable for an engineer
# Recalls trigger complaint surges (owners reporting remedy delays). For EMERGING defects we
# ignore complaints that mention a recall, so alerts reflect new problems, not known ones.
IGNORE_RECALL_COMPLAINTS = True
RECALL_DRIVEN_SHARE = 0.5   # if >= half of a spike's complaints cite a recall, call it recall-driven

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
USE_LLM = bool(GEMINI_API_KEY) and os.getenv("USE_LLM", "1") == "1"
# agentic: Gemini chooses tools in a function-calling loop | deterministic: fixed investigation pipeline
AGENT_MODE = os.getenv("AGENT_MODE", "agentic" if USE_LLM else "deterministic")
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", 8))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", 4))
LLM_RETRY_BASE_S = float(os.getenv("LLM_RETRY_BASE_S", 2.0))
EVAL_PACE_S = float(os.getenv("EVAL_PACE_S", 4.0))   # free-tier Gemini has low requests-per-minute limits
USE_EMBEDDINGS = os.getenv("USE_EMBEDDINGS", "0") == "1"
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")   # confirm against current Gemini docs
EMBED_MIN_SIM = float(os.getenv("EMBED_MIN_SIM", 0.6))
