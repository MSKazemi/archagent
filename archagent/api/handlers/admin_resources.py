"""Generic admin CRUD over an allow-listed resource registry.

Table and column identifiers come ONLY from the registry (never the client); all
values are bound parameters. This generalizes the old ``_list_table`` allow-list to
list/detail/edit/soft-delete/restore/bulk across every managed entity.
"""
from __future__ import annotations

from archagent.api.auth import parse_int_param
from archagent.api.errors import ApiError
from archagent.core.audit import log_activity
from archagent.core.db import app_conn, now, rows_to_dicts

# name → spec. ``editable``/``search``/``filterable``/``sortable`` are allow-lists.
RESOURCES: dict[str, dict] = {
    'prospects': {
        'table': 'prospects', 'label': 'Prospects',
        'editable': {'name', 'email', 'company', 'country', 'role', 'need', 'offer', 'status', 'value_estimate'},
        'search': ['name', 'email', 'company'], 'filterable': {'status', 'country'},
        'sortable': {'id', 'created_at', 'updated_at', 'status', 'value_estimate'},
        'soft_delete': True,
    },
    'proposals': {
        'table': 'proposals', 'label': 'Proposals',
        'editable': {'title', 'company_role', 'package_type', 'status'},
        'search': ['title', 'lead_notice_id'], 'filterable': {'status', 'source'},
        'sortable': {'id', 'created_at', 'updated_at', 'status'},
        'soft_delete': True,
    },
    'customer_profiles': {
        'table': 'customer_profiles', 'label': 'Customer Profiles',
        'editable': {'name', 'company', 'email', 'countries', 'categories', 'trades', 'min_value', 'max_leads', 'status', 'notes'},
        'search': ['name', 'company', 'email'], 'filterable': {'status'},
        'sortable': {'id', 'created_at', 'updated_at', 'status'},
        'soft_delete': True,
    },
    'bid_profiles': {
        'table': 'bid_profiles', 'label': 'Bid Profiles',
        'editable': {'company_name', 'email', 'soa_qualifications', 'ateco_codes', 'certifications_held', 'avg_project_value_eur', 'geographic_regions', 'notes'},
        'search': ['company_name', 'email'], 'filterable': set(),
        'sortable': {'id', 'created_at', 'updated_at'},
        'soft_delete': True,
    },
    'expert_workers': {
        'table': 'expert_workers', 'label': 'Expert Workers',
        'editable': {'name', 'worker_type', 'trades', 'country', 'city', 'phone', 'email', 'website', 'verification_status'},
        'search': ['name', 'city', 'trades', 'email'], 'filterable': {'country', 'worker_type', 'verification_status'},
        'sortable': {'id', 'imported_at', 'updated_at', 'name'},
        'soft_delete': False,  # no deleted_at column on this table
    },
    'tender_dossiers': {
        'table': 'tender_dossiers', 'label': 'Tender Dossiers',
        'editable': {'italy_wedge', 'recommended_offer', 'phase'},
        'search': ['source_notice_id', 'italy_wedge'], 'filterable': {'source', 'phase'},
        'sortable': {'id', 'created_at', 'italy_fit_score'},
        'soft_delete': True,
    },
    'contractors': {
        'table': 'contractors', 'label': 'Contractors',
        'editable': {'name', 'countries', 'trades', 'availability', 'commercial_model', 'risk', 'notes'},
        'search': ['name', 'trades', 'countries'], 'filterable': {'risk'},
        'sortable': {'id', 'created_at', 'name'},
        'soft_delete': True,
    },
}

_PRAGMA_CACHE: dict[str, set] = {}


def _columns(con, table: str) -> set:
    if table not in _PRAGMA_CACHE:
        _PRAGMA_CACHE[table] = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    return _PRAGMA_CACHE[table]


def _spec(name: str) -> dict:
    spec = RESOURCES.get(name)
    if spec is None:
        raise ApiError('not_found', f'unknown resource: {name}')
    return spec


def list_resources() -> dict:
    return {'items': [
        {'name': k, 'label': v['label'], 'search': v['search'],
         'filterable': sorted(v['filterable']), 'sortable': sorted(v['sortable']),
         'editable': sorted(v['editable']), 'soft_delete': v['soft_delete']}
        for k, v in RESOURCES.items()
    ]}


def list_rows(name: str, params: dict) -> dict:
    spec = _spec(name)
    table = spec['table']
    con = app_conn()
    try:
        cols = _columns(con, table)
        clauses, vals = [], []
        has_soft = spec['soft_delete'] and 'deleted_at' in cols
        include_deleted = (params.get('include_deleted') or ['0'])[0] in ('1', 'true', 'yes')
        if has_soft and not include_deleted:
            clauses.append('deleted_at IS NULL')
        q = (params.get('q') or [''])[0].strip()
        if q and spec['search']:
            ors = ' OR '.join(f'{c} LIKE ?' for c in spec['search'] if c in cols)
            if ors:
                clauses.append(f'({ors})')
                vals += [f'%{q}%'] * sum(1 for c in spec['search'] if c in cols)
        for fkey in spec['filterable']:
            fv = (params.get(fkey) or [''])[0]
            if fv and fkey in cols:
                clauses.append(f'{fkey} = ?'); vals.append(fv)
        sort = (params.get('sort') or ['id'])[0]
        if sort not in spec['sortable']:
            sort = 'id'
        direction = 'ASC' if (params.get('dir') or ['desc'])[0].lower() == 'asc' else 'DESC'
        limit = parse_int_param(params, 'limit', 50, 1, 500)
        offset = parse_int_param(params, 'offset', 0, 0)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        total = con.execute(f'SELECT COUNT(*) FROM {table}{where}', vals).fetchone()[0]
        rows = con.execute(
            f'SELECT * FROM {table}{where} ORDER BY {sort} {direction} LIMIT ? OFFSET ?',
            vals + [limit, offset],
        ).fetchall()
    finally:
        con.close()
    return {'items': rows_to_dicts(rows), 'total': total, 'limit': limit, 'offset': offset}


def get_row(name: str, row_id: int) -> dict:
    spec = _spec(name)
    con = app_conn()
    try:
        row = con.execute(f'SELECT * FROM {spec["table"]} WHERE id=?', (row_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise ApiError('not_found', 'row not found')
    return dict(row)


def update_row(name: str, row_id: int, payload: dict, principal=None) -> dict:
    spec = _spec(name)
    table = spec['table']
    updates = {k: v for k, v in (payload or {}).items() if k in spec['editable']}
    if not updates:
        raise ApiError('bad_request', 'no editable fields supplied')
    con = app_conn()
    try:
        cols = _columns(con, table)
        before = con.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
        if before is None:
            raise ApiError('not_found', 'row not found')
        sets = ', '.join(f'{k}=?' for k in updates)
        vals = list(updates.values())
        if 'updated_at' in cols:
            sets += ', updated_at=?'; vals.append(now())
        vals.append(row_id)
        con.execute(f'UPDATE {table} SET {sets} WHERE id=?', vals)
        after = dict(con.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone())
        log_activity(con, 'admin_edit', f'Edited {name} #{row_id}',
                     {'resource': name, 'id': row_id,
                      'before': {k: before[k] for k in updates}, 'after': {k: after[k] for k in updates}},
                     principal)
        con.commit()
    finally:
        con.close()
    return after


def delete_row(name: str, row_id: int, principal=None) -> dict:
    spec = _spec(name)
    table = spec['table']
    con = app_conn()
    try:
        cols = _columns(con, table)
        row = con.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
        if row is None:
            raise ApiError('not_found', 'row not found')
        soft = spec['soft_delete'] and 'deleted_at' in cols
        if soft:
            con.execute(f'UPDATE {table} SET deleted_at=? WHERE id=?', (now(), row_id))
        else:
            con.execute(f'DELETE FROM {table} WHERE id=?', (row_id,))
        log_activity(con, 'admin_delete', f'{"Soft-deleted" if soft else "Deleted"} {name} #{row_id}',
                     {'resource': name, 'id': row_id, 'soft': soft}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'soft': soft, 'id': row_id}


def restore_row(name: str, row_id: int, principal=None) -> dict:
    spec = _spec(name)
    if not spec['soft_delete']:
        raise ApiError('conflict', 'resource does not support restore')
    con = app_conn()
    try:
        con.execute(f'UPDATE {spec["table"]} SET deleted_at=NULL WHERE id=?', (row_id,))
        log_activity(con, 'admin_restore', f'Restored {name} #{row_id}',
                     {'resource': name, 'id': row_id}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'id': row_id}


def bulk(name: str, payload: dict, principal=None) -> dict:
    spec = _spec(name)
    table = spec['table']
    action = (payload or {}).get('action')
    ids = (payload or {}).get('ids') or []
    if action not in ('delete', 'restore', 'set_status'):
        raise ApiError('bad_request', 'action must be delete|restore|set_status')
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or not ids:
        raise ApiError('bad_request', 'ids must be a non-empty list of integers')
    placeholders = ','.join('?' * len(ids))
    con = app_conn()
    try:
        cols = _columns(con, table)
        if action == 'delete':
            if spec['soft_delete'] and 'deleted_at' in cols:
                con.execute(f'UPDATE {table} SET deleted_at=? WHERE id IN ({placeholders})', [now(), *ids])
            else:
                con.execute(f'DELETE FROM {table} WHERE id IN ({placeholders})', ids)
        elif action == 'restore':
            if not (spec['soft_delete'] and 'deleted_at' in cols):
                raise ApiError('conflict', 'resource does not support restore')
            con.execute(f'UPDATE {table} SET deleted_at=NULL WHERE id IN ({placeholders})', ids)
        else:  # set_status
            value = (payload or {}).get('value')
            if 'status' not in cols:
                raise ApiError('conflict', 'resource has no status column')
            if not value:
                raise ApiError('bad_request', 'value is required for set_status')
            con.execute(f'UPDATE {table} SET status=? WHERE id IN ({placeholders})', [value, *ids])
        log_activity(con, 'admin_bulk', f'Bulk {action} on {len(ids)} {name}',
                     {'resource': name, 'action': action, 'ids': ids}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'affected': len(ids), 'action': action}
