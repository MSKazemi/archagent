#!/usr/bin/env python3
"""Unit tests for archagent.core.db: init_app_db schema creation and WAL mode."""
from __future__ import annotations

import sys
from pathlib import Path
_sys = sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from archagent.core.db import init_app_db, app_conn, now


EXPECTED_TABLES = {
    'prospects', 'proposals', 'contractors', 'activities', 'followups',
    'customer_profiles', 'lead_radar_exports', 'tender_dossiers',
    'worker_verifications', 'expert_workers', 'bid_profiles',
    'pdf_extractions', 'analysis_jobs', 'pilot_requests',
}

EXPECTED_DOSSIER_COLUMNS = {
    'analyst_json', 'analyst_cost_eur', 'analyst_model',
    'analyst_pages_analyzed', 'is_partial', 'phase',
}


class TestInitAppDb(unittest.TestCase):
    def _temp_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        tmp.close()
        return Path(tmp.name)

    def test_creates_all_tables(self):
        db = self._temp_db()
        try:
            with patch('archagent.core.db.APP_DB', db):
                init_app_db()
            con = sqlite3.connect(db)
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            con.close()
            for expected in EXPECTED_TABLES:
                self.assertIn(expected, tables, f'Missing table: {expected}')
        finally:
            db.unlink(missing_ok=True)

    def test_tender_dossiers_migration_columns(self):
        db = self._temp_db()
        try:
            with patch('archagent.core.db.APP_DB', db):
                init_app_db()
            con = sqlite3.connect(db)
            cols = {row[1] for row in con.execute('PRAGMA table_info(tender_dossiers)').fetchall()}
            con.close()
            for expected in EXPECTED_DOSSIER_COLUMNS:
                self.assertIn(expected, cols, f'Missing column in tender_dossiers: {expected}')
        finally:
            db.unlink(missing_ok=True)

    def test_seeds_demo_bid_profile(self):
        db = self._temp_db()
        try:
            with patch('archagent.core.db.APP_DB', db):
                init_app_db()
            con = sqlite3.connect(db)
            count = con.execute('SELECT COUNT(*) FROM bid_profiles').fetchone()[0]
            con.close()
            self.assertGreater(count, 0, 'Expected at least 1 seed bid profile')
        finally:
            db.unlink(missing_ok=True)

    def test_seeds_seed_contractors(self):
        db = self._temp_db()
        try:
            with patch('archagent.core.db.APP_DB', db):
                init_app_db()
            con = sqlite3.connect(db)
            count = con.execute('SELECT COUNT(*) FROM contractors').fetchone()[0]
            con.close()
            self.assertGreater(count, 0, 'Expected seed contractors')
        finally:
            db.unlink(missing_ok=True)

    def test_idempotent_double_init(self):
        db = self._temp_db()
        try:
            with patch('archagent.core.db.APP_DB', db):
                init_app_db()
                init_app_db()  # should not raise or duplicate seeds
            con = sqlite3.connect(db)
            # bid_profiles should still be exactly 1 (not duplicated)
            count = con.execute('SELECT COUNT(*) FROM bid_profiles').fetchone()[0]
            con.close()
            self.assertEqual(count, 1, 'Double init should not duplicate seed bid profiles')
        finally:
            db.unlink(missing_ok=True)


class TestAppConn(unittest.TestCase):
    def test_wal_mode(self):
        db = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        db.close()
        db_path = Path(db.name)
        try:
            with patch('archagent.core.db.APP_DB', db_path):
                with patch('archagent.core.db.APP_DB', db_path):
                    con = app_conn()
                    mode = con.execute('PRAGMA journal_mode').fetchone()[0]
                    con.close()
            self.assertEqual(mode, 'wal', f'Expected WAL mode, got: {mode}')
        finally:
            db_path.unlink(missing_ok=True)
            for suffix in ('-wal', '-shm'):
                Path(str(db_path) + suffix).unlink(missing_ok=True)


class TestNow(unittest.TestCase):
    def test_now_format(self):
        ts = now()
        self.assertTrue(ts.endswith('Z'), f'Expected UTC timestamp ending with Z, got: {ts}')
        self.assertEqual(len(ts), 20, f'Expected 20-char ISO timestamp, got: {ts}')


if __name__ == '__main__':
    result = unittest.main(verbosity=2, exit=False)
    tests_run = result.result.testsRun
    failures = len(result.result.failures) + len(result.result.errors)
    if failures:
        print(f'\nFAIL tests/test_db.py: {failures}/{tests_run} failed')
        raise SystemExit(1)
    print(f'\nPASS tests/test_db.py: {tests_run} tests OK')
