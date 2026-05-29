"""Observability & ops endpoints: metrics, errors, health, job queue control."""
from __future__ import annotations

import os

from archagent.api.auth import parse_int_param
from archagent.api.metrics import metrics
from archagent.core.config import APP_DB
from archagent.core.db import app_conn, now, rows_to_dicts


def get_metrics() -> dict:
    return metrics.snapshot()


def get_errors(params: dict) -> dict:
    limit = parse_int_param(params, 'limit', 50, 1, 200)
    # In-memory ring buffer first; fall back to durable error_log for older entries.
    items = metrics.recent_errors(limit)
    if len(items) < limit:
        con = app_conn()
        try:
            rows = rows_to_dicts(con.execute(
                'SELECT created_at AS time, method, path, status, message, request_id'
                ' FROM error_log ORDER BY id DESC LIMIT ?', (limit,)
            ).fetchall())
        finally:
            con.close()
        seen = {(e.get('request_id') or '') for e in items}
        for r in rows:
            if (r.get('request_id') or '') not in seen:
                items.append(r)
    return {'items': items[:limit]}


def list_jobs(params: dict) -> dict:
    status = (params.get('status') or [''])[0].strip()
    limit = parse_int_param(params, 'limit', 50, 1, 200)
    offset = parse_int_param(params, 'offset', 0, 0)
    where, vals = '', []
    if status:
        where = ' WHERE status=?'; vals.append(status)
    con = app_conn()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM analysis_jobs{where}', vals).fetchone()[0]
        rows = rows_to_dicts(con.execute(
            f'SELECT id,status,notice_id,bid_profile_id,error,created_at,started_at,completed_at'
            f' FROM analysis_jobs{where} ORDER BY id DESC LIMIT ? OFFSET ?', vals + [limit, offset]
        ).fetchall())
    finally:
        con.close()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


def get_health() -> dict:
    con = app_conn()
    try:
        try:
            wal = con.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
        except Exception:
            wal = None
        stuck = con.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status IN ('queued','running','canceling')").fetchone()[0]
        last_backup = con.execute('SELECT created_at FROM backups ORDER BY id DESC LIMIT 1').fetchone()
    finally:
        con.close()
    db_size = APP_DB.stat().st_size if APP_DB.exists() else 0
    return {
        'db_size_bytes': db_size,
        'wal_checkpoint': list(wal) if wal else None,
        'stuck_jobs': stuck,
        'last_backup_at': last_backup[0] if last_backup else None,
        'azure_configured': bool(os.getenv('AZURE_OPENAI_KEY')),
        'smtp_configured': bool(os.getenv('SMTP_HOST')),
        'metrics': metrics.snapshot(),
        'time': now(),
    }


def retry_job(job_id: int) -> dict:
    from archagent.api.handlers import dossiers
    return dossiers.retry_analysis_job(job_id)


def cancel_job(job_id: int) -> dict:
    from archagent.api.handlers import dossiers
    return dossiers.cancel_analysis_job(job_id)
