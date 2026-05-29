"""Database connection factories, schema initialization, and shared helpers."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime

from archagent.core.config import APP_DB, LEADS_DB


def now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def app_conn() -> sqlite3.Connection:
    con = sqlite3.connect(APP_DB, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    # Enforce FK clauses on the new auth tables (existing tables carry no FK clauses).
    con.execute('PRAGMA foreign_keys=ON')
    return con


@contextlib.contextmanager
def app_cursor():
    """Connection context manager: commits on success, rolls back on error, always closes."""
    con = app_conn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def not_deleted(alias: str = '') -> str:
    """Return a SQL fragment filtering out soft-deleted rows."""
    col = f'{alias}.deleted_at' if alias else 'deleted_at'
    return f'{col} IS NULL'


def leads_conn() -> sqlite3.Connection:
    con = sqlite3.connect(LEADS_DB)
    con.row_factory = sqlite3.Row
    return con


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def init_app_db() -> None:
    con = app_conn()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS prospects (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT, company TEXT,
      country TEXT, role TEXT, need TEXT, offer TEXT, status TEXT NOT NULL DEFAULT 'new',
      value_estimate REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proposals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, lead_notice_id TEXT, prospect_id INTEGER,
      company_role TEXT, package_type TEXT, title TEXT, body TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'template', status TEXT NOT NULL DEFAULT 'draft',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contractors (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, countries TEXT NOT NULL,
      trades TEXT NOT NULL, availability TEXT, commercial_model TEXT,
      risk TEXT NOT NULL DEFAULT 'low', notes TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS activities (
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, message TEXT NOT NULL,
      payload_json TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS followups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      prospect_id INTEGER,
      lead_notice_id TEXT,
      subject TEXT NOT NULL,
      body TEXT NOT NULL,
      call_script TEXT,
      tasks_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customer_profiles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      company TEXT,
      email TEXT,
      countries TEXT,
      categories TEXT,
      trades TEXT,
      min_value REAL,
      max_leads INTEGER NOT NULL DEFAULT 30,
      status TEXT NOT NULL DEFAULT 'pilot',
      notes TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lead_radar_exports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_id INTEGER,
      format TEXT NOT NULL,
      title TEXT NOT NULL,
      filters_json TEXT NOT NULL,
      item_count INTEGER NOT NULL,
      export_path TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tender_dossiers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_notice_id TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'api',
      italy_fit_score INTEGER,
      italy_wedge TEXT,
      recommended_offer TEXT,
      risks_json TEXT,
      export_path TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS worker_verifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      worker_id INTEGER NOT NULL,
      verification_status TEXT NOT NULL,
      contact_status TEXT,
      capabilities TEXT,
      regions_served TEXT,
      languages TEXT,
      certifications TEXT,
      notes TEXT,
      verified_by TEXT,
      verified_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS expert_workers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL,
      source_id TEXT NOT NULL,
      name TEXT NOT NULL,
      worker_type TEXT NOT NULL,
      trades TEXT NOT NULL,
      country TEXT,
      city TEXT,
      address TEXT,
      lat REAL,
      lon REAL,
      phone TEXT,
      email TEXT,
      website TEXT,
      opening_hours TEXT,
      languages TEXT,
      verification_status TEXT NOT NULL DEFAULT 'public_listing_unverified',
      source_url TEXT,
      raw_json TEXT NOT NULL,
      imported_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(source, source_id)
    );
    CREATE TABLE IF NOT EXISTS bid_profiles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_name TEXT NOT NULL,
      email TEXT,
      soa_qualifications TEXT NOT NULL DEFAULT '[]',
      ateco_codes TEXT NOT NULL DEFAULT '[]',
      certifications_held TEXT NOT NULL DEFAULT '[]',
      avg_project_value_eur REAL,
      geographic_regions TEXT NOT NULL DEFAULT '[]',
      notes TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pdf_extractions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pdf_sha256 TEXT NOT NULL UNIQUE,
      notice_id TEXT,
      extracted_text TEXT NOT NULL,
      page_count INTEGER NOT NULL DEFAULT 0,
      is_truncated INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS analysis_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      status TEXT NOT NULL DEFAULT 'queued',
      notice_id TEXT,
      bid_profile_id TEXT,
      result_json TEXT,
      error TEXT,
      created_at TEXT NOT NULL,
      started_at TEXT,
      completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS pilot_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT NOT NULL,
      company TEXT,
      country TEXT,
      service TEXT,
      tender_deadline TEXT,
      brief TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS error_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      request_id TEXT,
      method TEXT,
      path TEXT,
      status INTEGER,
      message TEXT,
      traceback TEXT,
      actor_user_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS backups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      path TEXT NOT NULL,
      size_bytes INTEGER,
      sha256 TEXT,
      trigger TEXT NOT NULL DEFAULT 'manual',
      actor_user_id INTEGER,
      ok INTEGER NOT NULL DEFAULT 1,
      error TEXT
    );
    CREATE TABLE IF NOT EXISTS feature_flags (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      key TEXT NOT NULL UNIQUE,
      value TEXT,
      description TEXT,
      updated_at TEXT,
      updated_by INTEGER
    );
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT,
      updated_at TEXT,
      updated_by INTEGER
    );
    """)
    # Migrate tender_dossiers to add LLM analysis columns (idempotent)
    existing_cols = {row[1] for row in con.execute('PRAGMA table_info(tender_dossiers)')}
    for col, defn in [
        ('analyst_json', 'TEXT'),
        ('analyst_cost_eur', 'REAL'),
        ('analyst_model', 'TEXT'),
        ('analyst_pages_analyzed', 'INTEGER'),
        ('is_partial', 'INTEGER DEFAULT 0'),
        ('phase', "TEXT DEFAULT 'rule_based'"),
    ]:
        if col not in existing_cols:
            con.execute(f'ALTER TABLE tender_dossiers ADD COLUMN {col} {defn}')
    if con.execute('SELECT COUNT(*) FROM contractors').fetchone()[0] == 0:
        seed = [
            ('Facade & Insulation Crew', 'DEU,AUT,CHE', 'insulation,facade,envelope,roof', '2-4 weeks', 'referral fee or managed bid support', 'low', 'Good fit for roof/facade insulation tenders.'),
            ('Public Tender Bid Writer', 'EU', 'bid writing,compliance,proposal,procurement', '48h for first review', 'fixed bid package', 'low', 'Prepares compliance matrix and tender response checklist.'),
            ('Painting Contractor Network', 'DEU,FRA,BEL,LUX', 'painting,finishing,interior,facade painting', '1-3 weeks', 'lead fee + success fee', 'medium', 'Useful for small/medium painting frameworks and school/housing jobs.'),
            ('HVAC / Energy Partners', 'BEL,FRA,IRL,NLD', 'HVAC,energy,ventilation,heat pump,solar', '2-5 weeks', 'qualified appointment fee', 'low', 'Useful for public HVAC and energy upgrade opportunities.'),
            ('Architecture + Permit Studio', 'DEU,FRA,NLD', 'architecture,design,permits,planning', 'Discovery call in 7 days', 'scope package + project fee', 'low', 'Can review design/permit-heavy notices and prepare concept scope.'),
            ('General Contractor Marketplace', 'EU', 'construction,renovation,rehabilitation,multi-trade', 'varies', 'success fee', 'medium', 'Fallback for multi-trade work packages.'),
        ]
        con.executemany(
            'INSERT INTO contractors(name,countries,trades,availability,commercial_model,risk,notes,created_at) VALUES (?,?,?,?,?,?,?,?)',
            [(*row, now()) for row in seed],
        )
    # Mark any jobs that were running when the server last stopped
    con.execute(
        "UPDATE analysis_jobs SET status='error', error='Server restarted while job was in-flight',"
        " completed_at=? WHERE status IN ('queued','running')",
        (now(),),
    )
    if con.execute('SELECT COUNT(*) FROM bid_profiles').fetchone()[0] == 0:
        demo_soa = json.dumps([
            {'category': 'OG11', 'classification': 'III-bis'},
            {'category': 'OG1',  'classification': 'II'},
            {'category': 'OS28', 'classification': 'II'},
        ])
        demo_certs = json.dumps(['ISO 9001:2015', 'ISO 14001'])
        demo_ateco = json.dumps(['43.22', '43.21', '41.20'])
        demo_regions = json.dumps(['ITA', 'ITA-LO', 'ITA-ER', 'ITA-VE'])
        con.execute(
            'INSERT INTO bid_profiles(company_name,soa_qualifications,certifications_held,'
            'ateco_codes,geographic_regions,avg_project_value_eur,notes,created_at,updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?)',
            ('Demo — Termoidraulica Italiana SRL', demo_soa, demo_certs, demo_ateco,
             demo_regions, 750_000.0,
             'Demo ESCO/impianti contractor. OG11 III-bis covers up to €1.03M HVAC/energy work across Italy. Remove or replace with real profile before client delivery.',
             now(), now()),
        )
    con.commit()
    con.close()
    # Apply versioned migrations (auth tables, audit columns, soft-deletes, indexes),
    # then seed the bootstrap admin from env if no users exist. Imported lazily to
    # avoid a circular import (migrations/auth_db depend on this module).
    from archagent.core.migrations import run_migrations
    from archagent.core.auth_db import bootstrap_admin
    mcon = app_conn()
    try:
        run_migrations(mcon)
        bootstrap_admin(mcon)
    finally:
        mcon.close()
