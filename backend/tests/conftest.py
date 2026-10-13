# Test setup. Runs before any test module is imported.
#
# Safety: tests must never touch a real Supabase project or spend OpenAI credit,
# even if the developer has a real .env. So we force in-memory storage and a fake key.

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

import db  # noqa: E402

db.USE_SUPABASE = False  # all db functions check this flag at call time

import pytest  # noqa: E402
import security  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh in-memory data and fresh rate-limit counters for every test."""
    db._mock_reservations.clear()
    db._mock_callbacks.clear()
    db._mock_settings.clear()
    security._ai_per_ip = security.SlidingWindow(1000, 60)
    security._ai_global = security.SlidingWindow(1000, 3600)
    security._login_per_ip = security.SlidingWindow(1000, 60)
    security._health_per_ip = security.SlidingWindow(1000, 60)
    yield
