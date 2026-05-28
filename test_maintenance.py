#!/usr/bin/env python3
"""Unit tests for archagent_maintenance.py."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from archagent_maintenance import maintain


def test_happy_path():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db = Path(f.name)
    try:
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()
        result = maintain(db)
        assert result["exists"] is True
        assert result["integrity"] == "ok"
        assert result["size_bytes"] > 0
    finally:
        db.unlink(missing_ok=True)
        Path(str(db) + "-wal").unlink(missing_ok=True)
        Path(str(db) + "-shm").unlink(missing_ok=True)


def test_missing_db():
    result = maintain(Path("/nonexistent/path/archagent_test_missing.sqlite3"))
    assert result["exists"] is False


def test_vacuum_flag():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db = Path(f.name)
    try:
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()
        result = maintain(db, vacuum=True)
        assert result["integrity"] == "ok"
    finally:
        db.unlink(missing_ok=True)
        Path(str(db) + "-wal").unlink(missing_ok=True)
        Path(str(db) + "-shm").unlink(missing_ok=True)


def test_bid_profiles_table_counted():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        # Name the file so maintain() recognises it as archagent_app.sqlite3
        db = Path(f.name)
    # Rename to match the expected db.name check
    app_db = db.parent / "archagent_app.sqlite3"
    db.rename(app_db)
    db = app_db
    try:
        con = sqlite3.connect(db)
        con.execute(
            """CREATE TABLE bid_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                soa_qualifications TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )"""
        )
        con.execute(
            "INSERT INTO bid_profiles (company_name, soa_qualifications, created_at) VALUES (?, ?, ?)",
            ("Acme Build Ltd", '["OG1"]', "2026-01-01T00:00:00"),
        )
        con.execute(
            "INSERT INTO bid_profiles (company_name, soa_qualifications, created_at) VALUES (?, ?, ?)",
            ("Merlino Costruzioni Srl", '["OS28"]', "2026-02-15T00:00:00"),
        )
        con.commit()
        con.close()
        result = maintain(db)
        assert result["exists"] is True
        assert result["integrity"] == "ok"
        assert "table_counts" in result, "table_counts missing from result"
        assert result["table_counts"]["bid_profiles"] == 2, (
            f"Expected 2 bid_profiles rows, got {result['table_counts'].get('bid_profiles')}"
        )
    finally:
        db.unlink(missing_ok=True)
        Path(str(db) + "-wal").unlink(missing_ok=True)
        Path(str(db) + "-shm").unlink(missing_ok=True)


if __name__ == "__main__":
    test_happy_path()
    test_missing_db()
    test_vacuum_flag()
    test_bid_profiles_table_counted()
    print("PASS archagent_maintenance: happy path, missing DB, vacuum flag, bid_profiles counted")
