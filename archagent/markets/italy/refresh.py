"""Italy market refresh orchestration."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from archagent.core.config import BASE

ITALY_AREAS = 'Rome,Milan,Turin,Naples,Bologna'


def _run(cmd: list[str]) -> None:
    print('+', ' '.join(cmd), flush=True)
    subprocess.run(cmd, cwd=BASE, check=True)


def refresh(
    *,
    limit_per_search: int = 50,
    report_limit: int = 80,
    areas: str = ITALY_AREAS,
    sleep: float = 1.0,
    skip_workers: bool = False,
    skip_tenders: bool = False,
) -> None:
    if not skip_tenders:
        _run([sys.executable, '-m', 'archagent.ingestion.ted',
              '--limit-per-search', str(limit_per_search),
              '--report-limit', str(report_limit)])
    if not skip_workers:
        _run([sys.executable, '-m', 'archagent.ingestion.osm',
              '--areas', areas, '--sleep', str(sleep)])
    _run([sys.executable, 'ops/seed_italy.py'])
    _run([sys.executable, '-m', 'archagent.markets.italy.report'])
