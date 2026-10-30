import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from qia import config  # noqa: E402

@pytest.fixture
def db(tmp_path):
    """Fresh warehouse with the planted synthetic data, per test."""
    from data.generate import generate, load
    config.DB_PATH = tmp_path / "quality.db"
    rows, recalls, truth = generate()
    load(rows, recalls)
    return truth
