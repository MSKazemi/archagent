"""Server-side session store backed by the app database.

Sessions survive restarts and support revocation. The opaque token is given to the
client in a cookie; only its SHA-256 hash is stored, so a DB leak does not expose
usable tokens.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from archagent.core import config
from archagent.core.db import now


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_session(con: sqlite3.Connection, user_id: int, ip: str = '', user_agent: str = '') -> str:
    """Create a session row; return the opaque token to set as a cookie."""
    token = secrets.token_urlsafe(32)
    ts = now()
    expires = (datetime.utcnow() + timedelta(hours=config.session_ttl_hours())).replace(microsecond=0).isoformat() + 'Z'
    con.execute(
        'INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen_at,ip,user_agent)'
        ' VALUES (?,?,?,?,?,?,?)',
        (_hash(token), user_id, ts, expires, ts, ip or '', (user_agent or '')[:400]),
    )
    con.commit()
    return token


def resolve_session(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Return the joined user row for a valid, active session, else None."""
    if not token:
        return None
    row = con.execute(
        """SELECT s.id AS session_id, s.expires_at, s.revoked_at,
                  u.id AS user_id, u.email, u.role, u.status
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?""",
        (_hash(token),),
    ).fetchone()
    if row is None or row['revoked_at'] is not None:
        return None
    if row['status'] != 'active':
        return None
    if now() > row['expires_at']:
        return None
    con.execute('UPDATE sessions SET last_seen_at=? WHERE id=?', (now(), row['session_id']))
    con.commit()
    return row


def revoke_session(con: sqlite3.Connection, session_id: int) -> None:
    con.execute('UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL', (now(), session_id))


def revoke_by_token(con: sqlite3.Connection, token: str) -> None:
    con.execute('UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL',
                (now(), _hash(token)))


def list_sessions(con: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    sql = (
        'SELECT s.id, s.user_id, u.email, s.created_at, s.expires_at, s.last_seen_at,'
        ' s.ip, s.revoked_at FROM sessions s JOIN users u ON u.id=s.user_id'
    )
    if active_only:
        sql += " WHERE s.revoked_at IS NULL AND s.expires_at > '" + now() + "'"
    sql += ' ORDER BY s.id DESC LIMIT 500'
    return [dict(r) for r in con.execute(sql).fetchall()]
