"""Single audit-logging helper used by every write path.

Writes an ``activities`` row with actor attribution. ``principal`` is duck-typed
(any object exposing ``user_id``, ``actor_type``, ``ip``) or None — when None the
actor columns are written NULL, so callers that have not yet been updated still
produce valid rows.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from archagent.core.db import now


def log_activity(con: sqlite3.Connection, kind: str, message: str,
                 payload: Any = None, principal: Any = None) -> None:
    actor_user_id = getattr(principal, 'user_id', None) if principal else None
    actor_type = getattr(principal, 'actor_type', None) if principal else None
    actor_ip = getattr(principal, 'ip', None) if principal else None
    payload_json = None
    if payload is not None:
        payload_json = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    con.execute(
        'INSERT INTO activities(kind,message,payload_json,created_at,actor_user_id,actor_type,actor_ip)'
        ' VALUES (?,?,?,?,?,?,?)',
        (kind, message, payload_json, now(), actor_user_id, actor_type, actor_ip),
    )
