#!/usr/bin/env python3
"""Production-safe wrapper for scheduled ArchAgent Italy refreshes."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BASE, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up data, refresh Italy data, seed profiles, and generate dossiers.")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-workers", action="store_true", help="Skip OSM worker refresh for fast daily runs")
    parser.add_argument("--skip-tenders", action="store_true", help="Skip TED refresh")
    parser.add_argument("--dossiers", type=int, default=5, help="Number of top Italy dossiers to generate")
    parser.add_argument("--min-score", type=int, default=60)
    args = parser.parse_args()

    if not args.skip_backup:
        run([sys.executable, "archagent_backup.py", "--include-exports", "--keep", "30"])

    refresh_cmd = [sys.executable, "italy_refresh.py"]
    if args.skip_workers:
        refresh_cmd.append("--skip-workers")
    if args.skip_tenders:
        refresh_cmd.append("--skip-tenders")
    run(refresh_cmd)

    if args.dossiers > 0:
        run([sys.executable, "tender_document_collector.py", "--limit", str(args.dossiers), "--min-score", str(args.min_score)])

    run([sys.executable, "production_italy_test.py"])
    run([sys.executable, "archagent_maintenance.py"])
    print("ArchAgent production refresh complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
