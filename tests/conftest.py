import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from qia import config  # noqa: E402

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Tests are hermetic: a GEMINI_API_KEY in your shell must not turn on real API calls.
    Tests that need the model opt in through the `gemini` fixture, which uses a fake client."""
    from qia import llm
    monkeypatch.setattr(config, "USE_LLM", False)
    monkeypatch.setattr(config, "AGENT_MODE", "deterministic")
    monkeypatch.setattr(config, "USE_EMBEDDINGS", False)
    llm.set_client(None)
    yield
    llm.set_client(None)

@pytest.fixture
def db(tmp_path):
    """Fresh warehouse with the planted synthetic data, per test."""
    from data.generate import generate, load
    config.DB_PATH = tmp_path / "quality.db"
    rows, recalls, truth = generate()
    load(rows, recalls)
    return truth
