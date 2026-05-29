"""User and API-key persistence: CRUD, bootstrap, authentication, login throttling.

All functions take an open ``sqlite3.Connection`` (with ``row_factory=Row``) so they
compose inside a caller's transaction. Secrets are never stored in plaintext:
passwords use PBKDF2 (see :mod:`archagent.core.passwords`); API keys store only a
SHA-256 of the secret.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta

from archagent.core import config, passwords
from archagent.core.db import now
from archagent.core.rbac import ROLES


# ─── Users ──────────────────────────────────────────────────────────────────────

def get_user_by_email(con: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return con.execute(
        'SELECT * FROM users WHERE email = ? COLLATE NOCASE AND deleted_at IS NULL',
        (email.strip(),),
    ).fetchone()


def get_user(con: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return con.execute(
        'SELECT * FROM users WHERE id = ? AND deleted_at IS NULL', (user_id,)
    ).fetchone()


def list_users(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        'SELECT id,email,role,status,created_at,updated_at,last_login_at'
        ' FROM users WHERE deleted_at IS NULL ORDER BY id'
    ).fetchall()
    return [dict(r) for r in rows]


def create_user(con: sqlite3.Connection, email: str, password: str, role: str = 'viewer') -> int:
    email = (email or '').strip()
    if not email or '@' not in email:
        raise ValueError('valid email is required')
    if role not in ROLES:
        raise ValueError(f'role must be one of {ROLES}')
    if get_user_by_email(con, email) is not None:
        raise ValueError('a user with that email already exists')
    h, salt, iters = passwords.hash_password(password)
    ts = now()
    cur = con.execute(
        'INSERT INTO users(email,password_hash,password_salt,pbkdf2_iterations,role,status,created_at,updated_at)'
        " VALUES (?,?,?,?,?,'active',?,?)",
        (email, h, salt, iters, role, ts, ts),
    )
    return int(cur.lastrowid)


def update_user(con: sqlite3.Connection, user_id: int, *, role: str | None = None,
                status: str | None = None, password: str | None = None) -> sqlite3.Row | None:
    if get_user(con, user_id) is None:
        raise ValueError('user not found')
    sets, vals = [], []
    if role is not None:
        if role not in ROLES:
            raise ValueError(f'role must be one of {ROLES}')
        sets.append('role=?'); vals.append(role)
    if status is not None:
        if status not in ('active', 'disabled'):
            raise ValueError("status must be 'active' or 'disabled'")
        sets.append('status=?'); vals.append(status)
    if password is not None:
        h, salt, iters = passwords.hash_password(password)
        sets += ['password_hash=?', 'password_salt=?', 'pbkdf2_iterations=?']
        vals += [h, salt, iters]
    if not sets:
        raise ValueError('nothing to update')
    sets.append('updated_at=?'); vals.append(now())
    vals.append(user_id)
    con.execute(f'UPDATE users SET {", ".join(sets)} WHERE id=?', vals)
    return get_user(con, user_id)


def bootstrap_admin(con: sqlite3.Connection) -> bool:
    """Seed the first admin from env if no users exist. Returns True if seeded."""
    if con.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
        return False
    email, password = config.bootstrap_admin_credentials()
    if not (email and password):
        return False
    create_user(con, email, password, role='admin')
    con.commit()
    return True


# ─── Login throttling + authentication ────────────────────────────────────────────

def _recent_failures(con: sqlite3.Connection, email: str) -> int:
    window_start = (datetime.utcnow() - timedelta(minutes=config.LOGIN_WINDOW_MINUTES))
    cutoff = window_start.replace(microsecond=0).isoformat() + 'Z'
    return con.execute(
        'SELECT COUNT(*) FROM login_attempts WHERE email=? COLLATE NOCASE AND success=0 AND attempted_at>=?',
        (email.strip(), cutoff),
    ).fetchone()[0]


def is_locked_out(con: sqlite3.Connection, email: str) -> bool:
    return _recent_failures(con, email) >= config.LOGIN_MAX_FAILURES


def _record_attempt(con: sqlite3.Connection, email: str, ip: str, success: bool) -> None:
    con.execute(
        'INSERT INTO login_attempts(email,ip,success,attempted_at) VALUES (?,?,?,?)',
        (email.strip(), ip or '', 1 if success else 0, now()),
    )


class LockedOut(Exception):
    """Raised when an account has too many recent failed attempts."""


def authenticate_user(con: sqlite3.Connection, email: str, password: str, ip: str = '') -> sqlite3.Row:
    """Return the user row on success; raise LockedOut or ValueError otherwise.

    On success the failed-attempt window is cleared and ``last_login_at`` updated.
    """
    email = (email or '').strip()
    if is_locked_out(con, email):
        raise LockedOut('too many failed login attempts; try again later')
    user = get_user_by_email(con, email)
    ok = bool(
        user
        and user['status'] == 'active'
        and passwords.verify_password(password, user['password_hash'], user['password_salt'], user['pbkdf2_iterations'])
    )
    _record_attempt(con, email, ip, ok)
    if not ok:
        con.commit()
        raise ValueError('invalid credentials')
    # Clear the failure window and stamp last login.
    con.execute('DELETE FROM login_attempts WHERE email=? COLLATE NOCASE AND success=0', (email,))
    con.execute('UPDATE users SET last_login_at=? WHERE id=?', (now(), user['id']))
    con.commit()
    return get_user(con, user['id'])


# ─── API keys ─────────────────────────────────────────────────────────────────────

def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def issue_api_key(con: sqlite3.Connection, name: str, role: str = 'viewer',
                  scopes: list[str] | None = None, created_by: int | None = None) -> dict:
    """Create a key; returns the full key ONCE plus the stored row metadata."""
    if not (name or '').strip():
        raise ValueError('name is required')
    if role not in ROLES:
        raise ValueError(f'role must be one of {ROLES}')
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f'{prefix}.{secret}'
    cur = con.execute(
        'INSERT INTO api_keys(name,key_hash,prefix,role,scopes,created_by,created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (name.strip(), _hash_secret(secret), prefix, role, json.dumps(scopes or []), created_by, now()),
    )
    return {'id': int(cur.lastrowid), 'name': name.strip(), 'prefix': prefix,
            'role': role, 'key': full, 'created_at': now()}


def rotate_api_key(con: sqlite3.Connection, key_id: int) -> dict:
    row = con.execute('SELECT * FROM api_keys WHERE id=? AND revoked_at IS NULL', (key_id,)).fetchone()
    if row is None:
        raise ValueError('api key not found or revoked')
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    con.execute('UPDATE api_keys SET key_hash=?, prefix=?, last_used_at=NULL WHERE id=?',
                (_hash_secret(secret), prefix, key_id))
    return {'id': key_id, 'name': row['name'], 'prefix': prefix,
            'role': row['role'], 'key': f'{prefix}.{secret}'}


def revoke_api_key(con: sqlite3.Connection, key_id: int) -> None:
    con.execute('UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL', (now(), key_id))


def list_api_keys(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        'SELECT id,name,prefix,role,scopes,created_by,created_at,last_used_at,revoked_at'
        ' FROM api_keys ORDER BY id DESC'
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['scopes'] = json.loads(d.get('scopes') or '[]')
        out.append(d)
    return out


def resolve_api_key(con: sqlite3.Connection, presented: str) -> sqlite3.Row | None:
    """Look up a non-revoked key by its presented ``prefix.secret`` value."""
    if not presented or '.' not in presented:
        return None
    prefix, secret = presented.split('.', 1)
    row = con.execute(
        'SELECT * FROM api_keys WHERE prefix=? AND revoked_at IS NULL', (prefix,)
    ).fetchone()
    if row is None:
        return None
    if not secrets.compare_digest(_hash_secret(secret), row['key_hash']):
        return None
    con.execute('UPDATE api_keys SET last_used_at=? WHERE id=?', (now(), row['id']))
    con.commit()
    return row
