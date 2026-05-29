"""Audit & compliance: attributed audit trail, login log, change history, GDPR tools."""
from __future__ import annotations

from archagent.api.auth import parse_int_param
from archagent.api.errors import ApiError
from archagent.core.audit import log_activity
from archagent.core.db import app_conn, rows_to_dicts

# Registry tables that carry an ``email`` column → in scope for GDPR subject access.
_EMAIL_TABLES = ('prospects', 'customer_profiles', 'bid_profiles', 'expert_workers', 'pilot_requests')


def _has_json1(con) -> bool:
    try:
        con.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        return True
    except Exception:
        return False


def list_activities(params: dict) -> dict:
    actor = (params.get('actor') or [''])[0].strip()
    kind = (params.get('kind') or [''])[0].strip()
    dfrom = (params.get('from') or [''])[0].strip()
    dto = (params.get('to') or [''])[0].strip()
    limit = parse_int_param(params, 'limit', 50, 1, 200)
    offset = parse_int_param(params, 'offset', 0, 0)
    clauses, vals = [], []
    if actor:
        clauses.append('actor_user_id=?'); vals.append(actor)
    if kind:
        clauses.append('kind=?'); vals.append(kind)
    if dfrom:
        clauses.append('created_at>=?'); vals.append(dfrom)
    if dto:
        clauses.append('created_at<=?'); vals.append(dto)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    con = app_conn()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM activities{where}', vals).fetchone()[0]
        rows = rows_to_dicts(con.execute(
            f'SELECT * FROM activities{where} ORDER BY id DESC LIMIT ? OFFSET ?', vals + [limit, offset]
        ).fetchall())
    finally:
        con.close()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


def list_logins(params: dict) -> dict:
    user = (params.get('user') or [''])[0].strip()
    outcome = (params.get('outcome') or [''])[0].strip()
    limit = parse_int_param(params, 'limit', 50, 1, 200)
    offset = parse_int_param(params, 'offset', 0, 0)
    clauses, vals = [], []
    if user:
        clauses.append('email=? COLLATE NOCASE'); vals.append(user)
    if outcome in ('success', 'failure'):
        clauses.append('success=?'); vals.append(1 if outcome == 'success' else 0)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    con = app_conn()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM login_attempts{where}', vals).fetchone()[0]
        rows = rows_to_dicts(con.execute(
            f'SELECT * FROM login_attempts{where} ORDER BY id DESC LIMIT ? OFFSET ?', vals + [limit, offset]
        ).fetchall())
    finally:
        con.close()
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


def change_history(params: dict) -> dict:
    resource = (params.get('resource') or [''])[0].strip()
    rid = (params.get('id') or [''])[0].strip()
    if not resource or not rid:
        raise ApiError('bad_request', 'resource and id are required')
    con = app_conn()
    try:
        if _has_json1(con):
            rows = rows_to_dicts(con.execute(
                "SELECT * FROM activities WHERE json_extract(payload_json,'$.resource')=?"
                " AND json_extract(payload_json,'$.id')=? ORDER BY id DESC LIMIT 200",
                (resource, int(rid)),
            ).fetchall())
        else:
            like = f'%"resource": "{resource}"%'
            like_id = f'%"id": {int(rid)}%'
            rows = rows_to_dicts(con.execute(
                'SELECT * FROM activities WHERE payload_json LIKE ? AND payload_json LIKE ?'
                ' ORDER BY id DESC LIMIT 200', (like, like_id),
            ).fetchall())
    finally:
        con.close()
    return {'items': rows, 'resource': resource, 'id': int(rid)}


def gdpr_export(params: dict) -> dict:
    email = (params.get('email') or [''])[0].strip()
    if not email:
        raise ApiError('bad_request', 'email is required')
    out: dict = {}
    con = app_conn()
    try:
        for table in _EMAIL_TABLES:
            rows = con.execute(f'SELECT * FROM {table} WHERE email=? COLLATE NOCASE', (email,)).fetchall()
            if rows:
                out[table] = rows_to_dicts(rows)
    finally:
        con.close()
    from archagent.core.db import now
    return {'email': email, 'generated_at': now(), 'tables': out}


def gdpr_preview(payload: dict) -> dict:
    email = (payload or {}).get('email', '').strip()
    if not email:
        raise ApiError('bad_request', 'email is required')
    counts = {}
    con = app_conn()
    try:
        for table in _EMAIL_TABLES:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM {table} WHERE email=? COLLATE NOCASE', (email,)).fetchone()[0]
    finally:
        con.close()
    return {'email': email, 'counts': counts, 'total': sum(counts.values())}


def gdpr_erase(payload: dict, principal=None) -> dict:
    email = (payload or {}).get('email', '').strip()
    mode = (payload or {}).get('mode', 'anonymize')
    if not email:
        raise ApiError('bad_request', 'email is required')
    if mode not in ('anonymize', 'erase'):
        raise ApiError('bad_request', "mode must be 'anonymize' or 'erase'")
    if not (payload or {}).get('confirm'):
        raise ApiError('bad_request', 'confirm:true is required for an irreversible operation')
    affected = {}
    con = app_conn()
    try:
        for table in _EMAIL_TABLES:
            cols = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
            n = con.execute(f'SELECT COUNT(*) FROM {table} WHERE email=? COLLATE NOCASE', (email,)).fetchone()[0]
            if not n:
                continue
            affected[table] = n
            if mode == 'erase':
                con.execute(f'DELETE FROM {table} WHERE email=? COLLATE NOCASE', (email,))
            else:  # anonymize PII-ish columns; soft-delete if supported
                sets = ["email='[erased]'"]
                for c in ('name', 'company', 'phone', 'address', 'website'):
                    if c in cols:
                        sets.append(f"{c}='[erased]'")
                if 'deleted_at' in cols:
                    from archagent.core.db import now as _now
                    sets.append(f"deleted_at='{_now()}'")
                con.execute(f'UPDATE {table} SET {", ".join(sets)} WHERE email=? COLLATE NOCASE', (email,))
        log_activity(con, 'gdpr_erase', f'GDPR {mode} for {email}',
                     {'email': email, 'mode': mode, 'affected': affected}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'mode': mode, 'email': email, 'affected': affected}
