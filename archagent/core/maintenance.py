"""SQLite maintenance: integrity check, optimize, vacuum."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from archagent.core.config import APP_DB, LEADS_DB

DBS = [LEADS_DB, APP_DB]
APP_DB_TABLES = ['bid_profiles', 'tender_dossiers', 'proposals', 'prospects', 'workers', 'activities']


def _row_counts(con: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    existing = {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    counts: dict[str, int] = {}
    for table in tables:
        if table not in existing:
            continue
        try:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]  # noqa: S608
        except sqlite3.OperationalError:
            pass
    return counts


def maintain(db: Path, *, vacuum: bool = False) -> dict:
    if not db.exists():
        return {'db': str(db), 'exists': False}
    con = sqlite3.connect(db)
    try:
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        con.execute('PRAGMA optimize')
        if vacuum:
            con.execute('VACUUM')
        pages = con.execute('PRAGMA page_count').fetchone()[0]
        page_size = con.execute('PRAGMA page_size').fetchone()[0]
        result: dict = {
            'db': str(db),
            'exists': True,
            'integrity': integrity,
            'size_bytes': pages * page_size,
        }
        if db.name == 'archagent_app.sqlite3':
            result['table_counts'] = _row_counts(con, APP_DB_TABLES)
        return result
    finally:
        con.close()
