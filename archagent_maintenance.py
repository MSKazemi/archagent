#!/usr/bin/env python3
"""SQLite maintenance checks for ArchAgent production/pilot deployments."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DBS = [BASE / "archagent_actionable_projects.sqlite3", BASE / "archagent_app.sqlite3"]

APP_DB_TABLES = ["bid_profiles", "tender_dossiers", "proposals", "prospects", "workers", "activities"]


def _row_counts(con: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    """Return row counts for each table that exists in the database."""
    existing = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    counts: dict[str, int] = {}
    for table in tables:
        if table not in existing:
            continue
        try:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            counts[table] = count
        except sqlite3.OperationalError:
            pass
    return counts


def maintain(db: Path, *, vacuum: bool = False) -> dict:
    if not db.exists():
        return {"db": str(db), "exists": False}
    con = sqlite3.connect(db)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        con.execute("PRAGMA optimize")
        if vacuum:
            con.execute("VACUUM")
        pages = con.execute("PRAGMA page_count").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        result: dict = {
            "db": str(db),
            "exists": True,
            "integrity": integrity,
            "size_bytes": pages * page_size,
        }
        if db.name == "archagent_app.sqlite3":
            result["table_counts"] = _row_counts(con, APP_DB_TABLES)
        return result
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SQLite integrity/optimize checks.")
    parser.add_argument("--vacuum", action="store_true", help="Also VACUUM databases after integrity checks")
    args = parser.parse_args()
    failed = False
    for db in DBS:
        result = maintain(db, vacuum=args.vacuum)
        print(result)
        if "table_counts" in result:
            print("  table_counts:")
            for tbl, cnt in result["table_counts"].items():
                print(f"    {tbl}: {cnt}")
        if result.get("exists") and result.get("integrity") != "ok":
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
