"""Web-layer governance: who may ask, how often, and how much it may cost.

Three gates sit in front of the pipeline when it runs as a website, and all
three are ordinary code with tests:

  access code   one shared phrase (TRAILHEADRX_ACCESS_CODE). A correct entry
                sets a signed cookie so it is typed once. Wrong entries are
                counted per visitor and slowed down; the text typed is never
                logged.
  rate limit    N questions per visitor (by IP) per hour, in memory.
  spend cap     the estimated Anthropic spend for the current UTC day, summed
                from the audit trace (every model call already records its
                cost estimate), plus the cost of jobs still running. Above the
                cap the site says so and stops calling the model.

None of this is patient data: the cookie holds only a signature, the rate
limiter holds IP → timestamps, and the spend ledger holds dollars.
FLUENCY.md: "guardrails", "rules-as-code", "tracing".
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import config
from ..audit import read_all


def _web() -> dict:
    return config.CONFIG.get("web", {})


# ---- access code -----------------------------------------------------------

def access_code() -> str | None:
    return os.environ.get("TRAILHEADRX_ACCESS_CODE") or None


def _secret() -> bytes:
    s = os.environ.get("TRAILHEADRX_SECRET")
    if not s:
        raise RuntimeError("TRAILHEADRX_SECRET is not set; the site cannot sign cookies.")
    return s.encode()


def cookie_token() -> str:
    """The value a correct login stores. It is an HMAC of the access code, so
    changing the code in .env logs everyone out, and the cookie never contains
    the code itself."""
    code = access_code() or ""
    return hmac.new(_secret(), f"access:{code}".encode(), hashlib.sha256).hexdigest()


def code_ok(entered: str) -> bool:
    expected = access_code()
    if not expected:
        return False
    return hmac.compare_digest(entered.strip().encode(), expected.encode())


def cookie_ok(value: str | None) -> bool:
    if not value or not access_code():
        return False
    return hmac.compare_digest(value, cookie_token())


# ---- rate limit and login back-off (in memory, per IP) ---------------------

class Limiter:
    def __init__(self, per_hour: int | None = None):
        self.per_hour = per_hour or int(_web().get("rate_limit_per_hour", 10))
        self._hits: dict[str, deque] = defaultdict(deque)
        self._bad_logins: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, dq: deque, now: float, window: float = 3600.0) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    def allow(self, ip: str) -> tuple[bool, int]:
        """Record one question from `ip`; return (allowed, remaining)."""
        now = time.time()
        with self._lock:
            dq = self._hits[ip]
            self._trim(dq, now)
            if len(dq) >= self.per_hour:
                return False, 0
            dq.append(now)
            return True, self.per_hour - len(dq)

    def bad_login(self, ip: str) -> int:
        """Count a wrong access code; return how many in the last hour."""
        now = time.time()
        with self._lock:
            dq = self._bad_logins[ip]
            self._trim(dq, now)
            dq.append(now)
            return len(dq)

    def login_locked(self, ip: str, max_attempts: int = 10) -> bool:
        now = time.time()
        with self._lock:
            dq = self._bad_logins[ip]
            self._trim(dq, now)
            return len(dq) >= max_attempts


# ---- spend cap -------------------------------------------------------------

@dataclass
class Spend:
    today_usd: float
    cap_usd: float

    @property
    def over(self) -> bool:
        return self.today_usd >= self.cap_usd


def spend_today(in_flight_usd: float = 0.0) -> Spend:
    """Estimated spend so far this UTC day, from the audit trace, plus a
    reservation for jobs that have not finished yet."""
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    for rec in read_all():
        if str(rec.get("started_at", "")).startswith(today):
            total += float(rec.get("total_cost_usd") or 0.0)
    return Spend(round(total + in_flight_usd, 4), float(_web().get("daily_spend_cap_usd", 5.0)))
