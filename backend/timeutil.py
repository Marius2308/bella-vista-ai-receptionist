# timeutil.py
# One place for "what time is it at the restaurant?".
#
# Servers (Render, etc.) run in UTC, but the restaurant is in Kosovo. Without this,
# "today" and "is this slot in the past?" would be wrong for a couple of hours a day.
# Override with RESTAURANT_TIMEZONE in .env if needed.

import os
from datetime import datetime, timedelta

DEFAULT_TZ = "Europe/Belgrade"  # same offset as Kosovo (CET/CEST)

try:
    from zoneinfo import ZoneInfo
    _tz = ZoneInfo(os.getenv("RESTAURANT_TIMEZONE", DEFAULT_TZ))
except Exception as e:  # missing tz database (e.g. Windows without the tzdata package)
    print(f"[TIME] Could not load timezone, falling back to server time: {e}")
    _tz = None


def now_local() -> datetime:
    """Current restaurant time as a naive datetime (no tzinfo), safe to compare with parsed dates."""
    if _tz is None:
        return datetime.now()
    return datetime.now(_tz).replace(tzinfo=None)


def calendar_text(now: datetime, days: int = 15) -> str:
    """
    'Saturday 2026-10-10 (today), Sunday 2026-10-11 (tomorrow), Monday 2026-10-12, ...'
    Handed to the AI so it can look dates up instead of working out weekdays itself
    (language models get that arithmetic wrong surprisingly often).
    """
    parts = []
    for i in range(days):
        label = (now + timedelta(days=i)).strftime("%A %Y-%m-%d")
        if i == 0:
            label += " (today)"
        elif i == 1:
            label += " (tomorrow)"
        parts.append(label)
    return ", ".join(parts)
