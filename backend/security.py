# security.py
# Password hashing + a small in-memory rate limiter (no extra dependencies).

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# ── PASSWORDS ─────────────────────────────────────────────────────

_PREFIX = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_PREFIX}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(candidate: str, stored: str) -> bool:
    """
    Accepts a hashed value (what we store now) or a plain value
    (an old row in the settings table, or the ADMIN_PASSWORD env var).
    """
    if not stored:
        return False
    if stored.startswith(_PREFIX + "$"):
        try:
            _, iterations, salt_hex, digest_hex = stored.split("$")
            digest = hashlib.pbkdf2_hmac(
                "sha256", candidate.encode(), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(digest.hex(), digest_hex)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(candidate.encode(), stored.encode())


# ── RATE LIMITING ─────────────────────────────────────────────────

class SlidingWindow:
    """Allows at most `limit` hits per `window` seconds for each key."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._calls = 0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)

        # Occasionally drop keys that have gone quiet so the dict can't grow forever.
        self._calls += 1
        if self._calls % 500 == 0:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                del self._hits[k]
        return True


def client_ip(request: Request) -> str:
    # Behind Render's proxy the real IP arrives in X-Forwarded-For.
    # That header can be spoofed, which is why there is also a global limit below.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# The three AI endpoints cost money (OpenAI), so they get a per-IP limit AND a global one.
_ai_per_ip = SlidingWindow(_env_int("AI_RATE_LIMIT_PER_MINUTE", 30), 60)
_ai_global = SlidingWindow(_env_int("AI_GLOBAL_LIMIT_PER_HOUR", 600), 3600)
_login_per_ip = SlidingWindow(_env_int("LOGIN_RATE_LIMIT_PER_MINUTE", 10), 60)
_health_per_ip = SlidingWindow(30, 60)


def ai_rate_limit(request: Request):
    if not _ai_per_ip.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down a little.")
    if not _ai_global.allow("global"):
        raise HTTPException(status_code=429, detail="The demo is very busy right now. Please try again later.")


def login_rate_limit(request: Request):
    if not _login_per_ip.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many login attempts. Wait a minute and try again.")


def health_rate_limit(request: Request):
    if not _health_per_ip.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests.")
