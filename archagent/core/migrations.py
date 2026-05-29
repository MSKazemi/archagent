"""Lightweight stdlib schema-migration runner for the app database.

A ``schema_migrations`` table records applied version numbers. ``MIGRATIONS`` is an
ordered list of ``(version, name, fn)`` where ``fn(con)`` performs the DDL. Each
migration runs in its own transaction and is recorded atomically. Migrations are
written to be idempotent (guarded ADD COLUMN / IF NOT EXISTS) so re-running against
a partially-migrated database is safe and coexists with ``init_app_db``'s own
idempotent ALTERs.
"""
from __future__ import annotations

import sqlite3

from archagent.core.db import now


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f'PRAGMA table_info({table})')}


def _add_column(con: sqlite3.Connection, table: str, col: str, defn: str) -> None:
    if col not in _columns(con, table):
        con.execute(f'ALTER TABLE {table} ADD COLUMN {col} {defn}')


# ─── Migration bodies ───────────────────────────────────────────────────────────

def _m2_users(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          password_salt TEXT NOT NULL,
          pbkdf2_iterations INTEGER NOT NULL,
          role TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','analyst','sales','viewer')),
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_login_at TEXT,
          deleted_at TEXT
        )"""
    )
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')


def _m3_sessions(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          token_hash TEXT NOT NULL UNIQUE,
          user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          ip TEXT,
          user_agent TEXT,
          revoked_at TEXT
        )"""
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)')


def _m4_api_keys(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS api_keys (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          key_hash TEXT NOT NULL UNIQUE,
          prefix TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','analyst','sales','viewer')),
          scopes TEXT NOT NULL DEFAULT '[]',
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL,
          last_used_at TEXT,
          revoked_at TEXT
        )"""
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix)')


def _m5_login_attempts(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS login_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL COLLATE NOCASE,
          ip TEXT,
          success INTEGER NOT NULL DEFAULT 0,
          attempted_at TEXT NOT NULL
        )"""
    )
    con.execute('CREATE INDEX IF NOT EXISTS idx_login_attempts_email_time ON login_attempts(email, attempted_at)')


def _m6_audit_cols(con):
    _add_column(con, 'activities', 'actor_user_id', 'INTEGER')
    _add_column(con, 'activities', 'actor_type', 'TEXT')
    _add_column(con, 'activities', 'actor_ip', 'TEXT')


_SOFT_DELETE_TABLES = (
    'prospects', 'proposals', 'contractors', 'followups', 'customer_profiles',
    'bid_profiles', 'expert_workers', 'tender_dossiers',
)


def _m7_soft_delete(con):
    for table in _SOFT_DELETE_TABLES:
        _add_column(con, table, 'deleted_at', 'TEXT')


def _m8_indexes(con):
    for stmt in (
        'CREATE INDEX IF NOT EXISTS idx_proposals_prospect_id ON proposals(prospect_id)',
        'CREATE INDEX IF NOT EXISTS idx_followups_prospect_id ON followups(prospect_id)',
        'CREATE INDEX IF NOT EXISTS idx_lead_radar_exports_profile_id ON lead_radar_exports(profile_id)',
        'CREATE INDEX IF NOT EXISTS idx_worker_verifications_worker_id ON worker_verifications(worker_id)',
        'CREATE INDEX IF NOT EXISTS idx_tender_dossiers_source_notice_id ON tender_dossiers(source_notice_id)',
        'CREATE INDEX IF NOT EXISTS idx_analysis_jobs_bid_profile_id ON analysis_jobs(bid_profile_id)',
        'CREATE INDEX IF NOT EXISTS idx_activities_actor_user_id ON activities(actor_user_id)',
    ):
        con.execute(stmt)


def _m9_backup_columns(con):
    # Enterprise backup metadata: which DB, why, whether integrity-verified, free-text note.
    _add_column(con, 'backups', 'db_name', 'TEXT')
    _add_column(con, 'backups', 'kind', "TEXT DEFAULT 'manual'")
    _add_column(con, 'backups', 'verified', 'INTEGER')
    _add_column(con, 'backups', 'note', 'TEXT')


# (version, name, fn) — ordered. M1 (schema_migrations) is bootstrapped separately.
MIGRATIONS = [
    (2, 'users', _m2_users),
    (3, 'sessions', _m3_sessions),
    (4, 'api_keys', _m4_api_keys),
    (5, 'login_attempts', _m5_login_attempts),
    (6, 'activities_actor_columns', _m6_audit_cols),
    (7, 'soft_delete_columns', _m7_soft_delete),
    (8, 'fk_indexes', _m8_indexes),
    (9, 'backup_columns', _m9_backup_columns),
]


def _applied_versions(con: sqlite3.Connection) -> set[int]:
    return {row[0] for row in con.execute('SELECT version FROM schema_migrations')}


def run_migrations(con: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations in order. Returns the versions applied this run."""
    con.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          version INTEGER NOT NULL UNIQUE,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )"""
    )
    con.commit()
    done = _applied_versions(con)
    applied: list[int] = []
    for version, name, fn in MIGRATIONS:
        if version in done:
            continue
        try:
            fn(con)
            con.execute(
                'INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)',
                (version, name, now()),
            )
            con.commit()
            applied.append(version)
        except Exception:
            con.rollback()
            raise
    return applied
