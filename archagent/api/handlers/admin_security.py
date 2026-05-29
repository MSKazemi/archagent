"""Config & security admin: API keys, users, sessions, rate-limit, flags, backups."""
from __future__ import annotations

import json
import os

from archagent.api.ratelimit import limiter
from archagent.core import auth_db, sessions
from archagent.core.audit import log_activity
from archagent.core.db import app_conn, now, rows_to_dicts

# Env vars surfaced (redacted) in the config-status panel.
_CONFIG_KEYS = (
    'AZURE_OPENAI_KEY', 'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_DEPLOYMENT', 'AZURE_OPENAI_API_VERSION',
    'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'NOTIFY_EMAIL',
    'HERMES_PROPOSAL_ENABLED', 'ARCHAGENT_TOKEN', 'ARCHAGENT_ADMIN_EMAIL',
)


def _actor_id(principal):
    return getattr(principal, 'user_id', None) if principal else None


# ─── API keys ─────────────────────────────────────────────────────────────────────

def list_keys() -> dict:
    con = app_conn()
    try:
        return {'items': auth_db.list_api_keys(con)}
    finally:
        con.close()


def create_key(payload: dict, principal=None) -> dict:
    con = app_conn()
    try:
        result = auth_db.issue_api_key(con, (payload or {}).get('name', ''),
                                       (payload or {}).get('role', 'viewer'),
                                       (payload or {}).get('scopes'), _actor_id(principal))
        log_activity(con, 'apikey_issue', f'Issued API key {result["prefix"]}',
                     {'name': result['name'], 'role': result['role']}, principal)
        con.commit()
        return result
    finally:
        con.close()


def rotate_key(key_id: int, principal=None) -> dict:
    con = app_conn()
    try:
        result = auth_db.rotate_api_key(con, key_id)
        log_activity(con, 'apikey_rotate', f'Rotated API key #{key_id}', {'id': key_id}, principal)
        con.commit()
        return result
    finally:
        con.close()


def revoke_key(key_id: int, principal=None) -> dict:
    con = app_conn()
    try:
        auth_db.revoke_api_key(con, key_id)
        log_activity(con, 'apikey_revoke', f'Revoked API key #{key_id}', {'id': key_id}, principal)
        con.commit()
        return {'ok': True, 'id': key_id}
    finally:
        con.close()


# ─── Users ──────────────────────────────────────────────────────────────────────

def list_users() -> dict:
    con = app_conn()
    try:
        return {'items': auth_db.list_users(con)}
    finally:
        con.close()


def create_user(payload: dict, principal=None) -> dict:
    p = payload or {}
    con = app_conn()
    try:
        uid = auth_db.create_user(con, p.get('email', ''), p.get('password', ''), p.get('role', 'viewer'))
        log_activity(con, 'user_create', f'Created user {p.get("email")}', {'role': p.get('role')}, principal)
        con.commit()
        return {'id': uid, 'email': p.get('email'), 'role': p.get('role', 'viewer')}
    finally:
        con.close()


def update_user(user_id: int, payload: dict, principal=None) -> dict:
    p = payload or {}
    status = None
    if 'disabled' in p:
        status = 'disabled' if p['disabled'] else 'active'
    elif p.get('status'):
        status = p['status']
    con = app_conn()
    try:
        row = auth_db.update_user(con, user_id, role=p.get('role'), status=status, password=p.get('password'))
        log_activity(con, 'user_update', f'Updated user #{user_id}',
                     {'role': p.get('role'), 'status': status, 'password_reset': bool(p.get('password'))}, principal)
        con.commit()
        return {k: row[k] for k in ('id', 'email', 'role', 'status')}
    finally:
        con.close()


# ─── Sessions ─────────────────────────────────────────────────────────────────────

def list_sessions_handler() -> dict:
    con = app_conn()
    try:
        return {'items': sessions.list_sessions(con, active_only=False)}
    finally:
        con.close()


def revoke_session(session_id: int, principal=None) -> dict:
    con = app_conn()
    try:
        sessions.revoke_session(con, session_id)
        log_activity(con, 'session_revoke', f'Revoked session #{session_id}', {'id': session_id}, principal)
        con.commit()
        return {'ok': True, 'id': session_id}
    finally:
        con.close()


# ─── Settings / rate-limit / feature flags ────────────────────────────────────────

def _get_setting(con, key: str, default=None):
    row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row and row[0] else default


def _set_setting(con, key: str, value, principal=None):
    con.execute(
        'INSERT INTO settings(key,value,updated_at,updated_by) VALUES (?,?,?,?)'
        ' ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
        (key, json.dumps(value), now(), _actor_id(principal)),
    )


def get_rate_limit() -> dict:
    con = app_conn()
    try:
        overrides = _get_setting(con, 'rate_limit', {}) or {}
    finally:
        con.close()
    return {'tiers': limiter._tiers, 'overrides': overrides}


def update_rate_limit(payload: dict, principal=None) -> dict:
    con = app_conn()
    try:
        _set_setting(con, 'rate_limit', payload or {}, principal)
        log_activity(con, 'settings_update', 'Updated rate-limit settings', payload, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True}


def list_flags() -> dict:
    con = app_conn()
    try:
        return {'items': rows_to_dicts(con.execute('SELECT * FROM feature_flags ORDER BY key').fetchall())}
    finally:
        con.close()


def set_flag(key: str, payload: dict, principal=None) -> dict:
    value = json.dumps((payload or {}).get('value'))
    desc = (payload or {}).get('description')
    con = app_conn()
    try:
        con.execute(
            'INSERT INTO feature_flags(key,value,description,updated_at,updated_by) VALUES (?,?,?,?,?)'
            ' ON CONFLICT(key) DO UPDATE SET value=excluded.value,'
            ' description=COALESCE(excluded.description,feature_flags.description),'
            ' updated_at=excluded.updated_at, updated_by=excluded.updated_by',
            (key, value, desc, now(), _actor_id(principal)),
        )
        log_activity(con, 'flag_update', f'Set feature flag {key}', {'key': key, 'value': (payload or {}).get('value')}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'key': key}


# ─── Config status (redacted) ─────────────────────────────────────────────────────

def config_status() -> dict:
    items = []
    for name in _CONFIG_KEYS:
        val = os.getenv(name, '')
        preview = ''
        if val:
            preview = ('***' + val[-4:]) if len(val) > 4 else '***'
        items.append({'name': name, 'set': bool(val), 'preview': preview})
    return {'items': items}


# ─── Backups (delegated to the enterprise data plane) ──────────────────────────────
# Full backup/restore/export/retention now live in ``admin_data`` (both databases,
# integrity-verified, prunable). These thin wrappers keep the router surface stable.

def list_backups() -> dict:
    from archagent.api.handlers import admin_data
    return admin_data.list_backups()


def create_backup(principal=None) -> dict:
    from archagent.api.handlers import admin_data
    return admin_data.create_backup(principal, kind='manual')
