#!/usr/bin/env python3
"""Create timestamped SQLite and export backups for ArchAgent."""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_BACKUP_DIR = BASE / "backups"
DBS = ["archagent_actionable_projects.sqlite3", "archagent_app.sqlite3"]


def sqlite_backup(src: Path, dst: Path) -> None:
    src_con = sqlite3.connect(src)
    dst_con = sqlite3.connect(dst)
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up ArchAgent SQLite DBs and optional exports.")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--include-exports", action="store_true", help="Also archive exports/ into a .tar.gz file")
    parser.add_argument("--keep", type=int, default=30, help="Keep newest N backup files per prefix")
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir).expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")

    created: list[Path] = []
    for db_name in DBS:
        src = BASE / db_name
        if not src.exists():
            continue
        dst = backup_dir / f"{src.stem}_{stamp}.sqlite3"
        sqlite_backup(src, dst)
        created.append(dst)

    if args.include_exports and (BASE / "exports").exists():
        archive = backup_dir / f"exports_{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(BASE / "exports", arcname="exports")
        created.append(archive)

    for prefix in [Path(db).stem for db in DBS] + ["exports"]:
        files = sorted(backup_dir.glob(f"{prefix}_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[args.keep:]:
            if old.is_file():
                old.unlink()
            elif old.is_dir():
                shutil.rmtree(old)

    for path in created:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
