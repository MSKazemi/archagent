"""In-process request metrics collector (stdlib only, thread-safe).

Records per-route counts/errors/latency and keeps a ring buffer of recent latencies
and recent errors for the admin observability panel. Counters reset on restart; the
``error_log`` table provides durable error history (written by the server).
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

_NUM_SEG = re.compile(r'/\d+(?=/|$)')


def path_group(path: str) -> str:
    """Normalize numeric path segments so cardinality stays bounded."""
    path = path.split('?', 1)[0]
    if path.startswith('/api/v1/'):
        path = '/api/' + path[len('/api/v1/'):]
    return _NUM_SEG.sub('/{id}', path)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict = defaultdict(int)          # (group, method) -> count
        self._errors: dict = defaultdict(int)          # (group, method) -> error count
        self._latency_sum: dict = defaultdict(float)   # (group, method) -> ms
        self._latencies = deque(maxlen=2048)           # recent latencies (ms)
        self._recent_errors = deque(maxlen=200)
        self._started = time.time()
        self.inflight = 0

    def record(self, group: str, method: str, status: int, latency_ms: float):
        key = (group, method)
        with self._lock:
            self._counts[key] += 1
            self._latency_sum[key] += latency_ms
            self._latencies.append(latency_ms)
            if status >= 500:
                self._errors[key] += 1

    def record_error(self, method: str, path: str, status: int, message: str, request_id: str = ''):
        with self._lock:
            self._recent_errors.appendleft({
                'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'method': method, 'path': path, 'status': status,
                'message': message, 'request_id': request_id,
            })

    @staticmethod
    def _percentile(sorted_vals: list, pct: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, int(round(pct / 100.0 * (len(sorted_vals) - 1))))
        return round(sorted_vals[idx], 2)

    def snapshot(self) -> dict:
        with self._lock:
            total = sum(self._counts.values())
            errors = sum(self._errors.values())
            lat = sorted(self._latencies)
            routes = []
            for (group, method), count in sorted(self._counts.items(), key=lambda kv: -kv[1]):
                routes.append({
                    'route': f'{method} {group}',
                    'count': count,
                    'errors': self._errors.get((group, method), 0),
                    'avg_ms': round(self._latency_sum[(group, method)] / count, 2) if count else 0.0,
                })
            return {
                'uptime_seconds': int(time.time() - self._started),
                'total_requests': total,
                'total_errors': errors,
                'error_rate': round(errors / total, 4) if total else 0.0,
                'latency_ms': {
                    'p50': self._percentile(lat, 50),
                    'p95': self._percentile(lat, 95),
                    'p99': self._percentile(lat, 99),
                },
                'inflight': self.inflight,
                'routes': routes[:50],
            }

    def recent_errors(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._recent_errors)[:limit]


# Module-level singleton.
metrics = Metrics()
