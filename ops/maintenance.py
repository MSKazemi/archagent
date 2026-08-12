#!/usr/bin/env python3
"""CLI wrapper — delegates to archagent.core.maintenance."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from archagent.core.maintenance import DBS, maintain


def main() -> int:
    parser = argparse.ArgumentParser(description='Run SQLite integrity/optimize checks.')
    parser.add_argument('--vacuum', action='store_true', help='Also VACUUM databases after integrity checks')
    args = parser.parse_args()
    failed = False
    for db in DBS:
        result = maintain(db, vacuum=args.vacuum)
        print(result)
        if 'table_counts' in result:
            print('  table_counts:')
            for tbl, cnt in result['table_counts'].items():
                print(f'    {tbl}: {cnt}')
        if result.get('exists') and result.get('integrity') != 'ok':
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
