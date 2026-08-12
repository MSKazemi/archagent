#!/usr/bin/env python3
"""CLI wrapper — delegates to archagent.core.backup."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from archagent.core.backup import DEFAULT_BACKUP_DIR, run_backup


def main() -> int:
    parser = argparse.ArgumentParser(description='Back up ArchAgent SQLite DBs and optional exports.')
    parser.add_argument('--backup-dir', default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument('--include-exports', action='store_true', help='Also archive exports/ into a .tar.gz file')
    parser.add_argument('--keep', type=int, default=30, help='Keep newest N backup files per prefix')
    args = parser.parse_args()
    created = run_backup(Path(args.backup_dir).expanduser().resolve(), include_exports=args.include_exports, keep=args.keep)
    for path in created:
        print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
