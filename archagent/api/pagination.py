"""Pagination helpers and the standard list envelope.

Envelope: ``{items, total, limit, offset}``. The envelope is a *minimum* contract —
handlers may add extra keys (e.g. ``include_expired``). Replaces the old
``_list_table`` while preserving its default of returning the most recent 200 rows
when no limit is supplied.
"""
from __future__ import annotations

from archagent.api.auth import parse_int_param
from archagent.core.db import app_conn, rows_to_dicts

_ALLOWED_TABLES = {
    'prospects', 'followups', 'proposals', 'contractors', 'activities',
    'customer_profiles', 'lead_radar_exports', 'tender_dossiers',
    'worker_verifications', 'bid_profiles', 'pilot_requests',
}
# Tables that carry a deleted_at column (soft-delete aware).
_SOFT_DELETE_TABLES = {
    'prospects', 'proposals', 'contractors', 'followups', 'customer_profiles',
    'bid_profiles', 'tender_dossiers',
}


def paginate_params(params: dict, default_limit: int = 200, max_limit: int = 500) -> tuple[int, int]:
    limit = parse_int_param(params, 'limit', default_limit, 1, max_limit)
    offset = parse_int_param(params, 'offset', 0, 0)
    return limit, offset


def list_table(name: str, params: dict | None = None, order: str = 'id DESC') -> dict:
    """Return a paginated envelope for an allow-listed table."""
    if name not in _ALLOWED_TABLES:
        raise ValueError('invalid table')
    params = params or {}
    limit, offset = paginate_params(params)
    where = ''
    if name in _SOFT_DELETE_TABLES:
        where = ' WHERE deleted_at IS NULL'
    con = app_conn()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM {name}{where}').fetchone()[0]
        rows = con.execute(
            f'SELECT * FROM {name}{where} ORDER BY {order} LIMIT ? OFFSET ?', (limit, offset)
        ).fetchall()
    finally:
        con.close()
    return {'items': rows_to_dicts(rows), 'total': total, 'limit': limit, 'offset': offset}
