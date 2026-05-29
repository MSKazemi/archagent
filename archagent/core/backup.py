"""SQLite backup: timestamped hot-copy using sqlite3.backup()."""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import tarfile
from pathlib import Path

from archagent.core.config import BASE

DEFAULT_BACKUP_DIR = BASE / 'backups'
DBS = ['archagent_actionable_projects.sqlite3', 'archagent_app.sqlite3']


def sqlite_backup(src: Path, dst: Path) -> None:
    src_con = sqlite3.connect(src)
    dst_con = sqlite3.connect(dst)
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def run_backup(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    include_exports: bool = False,
    keep: int = 30,
) -> list[Path]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%d_%H%M%S')
    created: list[Path] = []
    for db_name in DBS:
        src = BASE / db_name
        if not src.exists():
            continue
        dst = backup_dir / f'{src.stem}_{stamp}.sqlite3'
        sqlite_backup(src, dst)
        created.append(dst)
    if include_exports and (BASE / 'exports').exists():
        archive = backup_dir / f'exports_{stamp}.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(BASE / 'exports', arcname='exports')
        created.append(archive)
    for prefix in [Path(db).stem for db in DBS] + ['exports']:
        files = sorted(backup_dir.glob(f'{prefix}_*'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            if old.is_file():
                old.unlink()
            elif old.is_dir():
                shutil.rmtree(old)
    return created
