#!/usr/bin/env python3
"""Refresh Italy market data for ArchAgent.

Runs the three repeatable Italy data steps:
1) TED lead refresh via project_finder.py
2) Italy OSM expert/worker refresh via expert_worker_importer.py
3) Italy customer profiles + ITALY_MARKET_REPORT.md regeneration
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ITALY_AREAS = "Rome,Milan,Turin,Naples,Bologna"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BASE, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh ArchAgent Italy leads, public partner listings, seeded profiles, and report.")
    parser.add_argument("--limit-per-search", type=int, default=50, help="TED results per search query. Default: 50")
    parser.add_argument("--report-limit", type=int, default=80, help="ACTIONABLE_PROJECTS.md lead count. Default: 80")
    parser.add_argument("--areas", default=ITALY_AREAS, help=f"Comma-separated Italy OSM areas. Default: {ITALY_AREAS}")
    parser.add_argument("--sleep", type=float, default=1.0, help="Delay between Overpass area requests. Default: 1")
    parser.add_argument("--skip-workers", action="store_true", help="Skip OSM worker/company refresh.")
    parser.add_argument("--skip-tenders", action="store_true", help="Skip TED tender refresh.")
    args = parser.parse_args()

    if not args.skip_tenders:
        run([sys.executable, "project_finder.py", "--limit-per-search", str(args.limit_per_search), "--report-limit", str(args.report_limit)])
    if not args.skip_workers:
        run([sys.executable, "expert_worker_importer.py", "--areas", args.areas, "--sleep", str(args.sleep)])
    run([sys.executable, "seed_italy_profiles.py"])
    run([sys.executable, "italy_market_report.py"])
    print("Italy refresh complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
