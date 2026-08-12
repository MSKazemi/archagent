#!/usr/bin/env python3
"""CLI — full data export (zip of every table, both DBs) and optional retention purge.

Safe by default: only exports. Purging is destructive and must be opted into explicitly
with ``--purge --confirm`` (the scheduled timer never purges).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse

from archagent.api.handlers import admin_data


def main() -> int:
    parser = argparse.ArgumentParser(description='Export all ArchAgent data (zip) and optionally purge old records.')
    parser.add_argument('--purge', action='store_true', help='Also purge soft-deleted rows + old audit/log records')
    parser.add_argument('--days', type=int, default=365, help='Retention window for --purge (default 365)')
    parser.add_argument('--confirm', action='store_true', help='Required to actually purge')
    args = parser.parse_args()

    result = admin_data.export_all()
    print(f"export: {result['file']} ({len(result['tables'])} tables, {result['size_bytes']} bytes)")

    if args.purge:
        if not args.confirm:
            print('refusing to purge without --confirm', file=sys.stderr)
            return 2
        purged = admin_data.retention_purge({'confirm': True, 'days': args.days})
        print(f"purged (older than {args.days}d): {purged['purged']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
