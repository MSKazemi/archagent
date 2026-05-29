"""Thread-safe in-process token-bucket rate limiter.

Keyed by an authenticated principal (hashed) when available, else client IP, plus a
per-route tier. Buckets live in a dict guarded by a single lock; idle buckets are
pruned lazily to bound memory. Stdlib only.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import time

# tier → (capacity/burst, refill_per_second)
_DEFAULT_TIERS = {
    'default': (60, 5.0),       # ~300/min sustained, burst 60 (page-load fan-out safe)
    'write': (30, 1.0),         # ~60/min sustained, burst 30
    'expensive': (6, 0.1),      # ~6/min sustained, burst 6 (LLM/PDF + external CLI only)
}

# Only genuinely expensive endpoints (LLM token spend / PDF rasterization / external
# CLI). Deterministic template generation stays on the normal write tier.
_EXPENSIVE = (
    '/api/dossier/analyze', '/api/dossier/analyze/async', '/api/proposals/hermes',
)
_WRITE_METHODS = {'POST', 'PATCH', 'PUT', 'DELETE'}
_PRUNE_AFTER = 600  # seconds


def _tiers() -> dict:
    """Allow env overrides like ARCHAGENT_RL_DEFAULT='30,2.0'."""
    tiers = dict(_DEFAULT_TIERS)
    for name in tiers:
        raw = os.getenv(f'ARCHAGENT_RL_{name.upper()}', '').strip()
        if raw and ',' in raw:
            try:
                cap, refill = raw.split(',', 1)
                tiers[name] = (int(cap), float(refill))
            except ValueError:
                pass
    return tiers


def tier_for(method: str, path: str) -> str:
    # /api/v1 alias collapses to canonical for tier decisions.
    if path.startswith('/api/v1/'):
        path = '/api/' + path[len('/api/v1/'):]
    for prefix in _EXPENSIVE:
        if path == prefix or path.startswith(prefix):
            return 'expensive'
    if method.upper() in _WRITE_METHODS:
        return 'write'
    return 'default'


class _Bucket:
    __slots__ = ('tokens', 'last')

    def __init__(self, capacity: float):
        self.tokens = float(capacity)
        self.last = time.monotonic()


class RateLimiter:
    def __init__(self):
        self._buckets: dict = {}
        self._lock = threading.Lock()
        self._tiers = _tiers()

    def reload(self):
        with self._lock:
            self._tiers = _tiers()

    def allow(self, key: str, tier: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        capacity, refill = self._tiers.get(tier, self._tiers['default'])
        nowm = time.monotonic()
        bucket_key = f'{tier}:{key}'
        with self._lock:
            self._maybe_prune(nowm)
            b = self._buckets.get(bucket_key)
            if b is None:
                b = self._buckets[bucket_key] = _Bucket(capacity)
            elapsed = nowm - b.last
            b.tokens = min(capacity, b.tokens + elapsed * refill)
            b.last = nowm
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0
            deficit = 1.0 - b.tokens
            retry = int(math.ceil(deficit / refill)) if refill > 0 else 60
            return False, max(1, retry)

    def _maybe_prune(self, nowm: float):
        if len(self._buckets) < 1024:
            return
        stale = [k for k, b in self._buckets.items() if nowm - b.last > _PRUNE_AFTER]
        for k in stale:
            self._buckets.pop(k, None)


def principal_key(principal, ip: str) -> str:
    """Stable, non-reversible key: authenticated identity if present, else IP."""
    if principal is not None and getattr(principal, 'is_authenticated', False):
        ident = f'{principal.kind}:{principal.user_id or principal.api_key_id or principal.role}'
        return 'p:' + hashlib.sha256(ident.encode()).hexdigest()[:16]
    return 'ip:' + (ip or 'unknown')


# Module-level singleton.
limiter = RateLimiter()
