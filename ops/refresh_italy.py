#!/usr/bin/env python3
"""CLI wrapper — delegates to archagent.markets.italy.refresh."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from archagent.markets.italy.refresh import ITALY_AREAS, refresh


def main() -> int:
    parser = argparse.ArgumentParser(description='Refresh ArchAgent Italy leads, public partner listings, seeded profiles, and report.')
    parser.add_argument('--limit-per-search', type=int, default=50)
    parser.add_argument('--report-limit', type=int, default=80)
    parser.add_argument('--areas', default=ITALY_AREAS)
    parser.add_argument('--sleep', type=float, default=1.0)
    parser.add_argument('--skip-workers', action='store_true')
    parser.add_argument('--skip-tenders', action='store_true')
    args = parser.parse_args()
    refresh(
        limit_per_search=args.limit_per_search,
        report_limit=args.report_limit,
        areas=args.areas,
        sleep=args.sleep,
        skip_workers=args.skip_workers,
        skip_tenders=args.skip_tenders,
    )
    print('Italy refresh complete.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
