"""Handler functions for worker/expert-related API routes."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from archagent.core.config import EXPORT_DIR
from archagent.core.db import app_conn, now, rows_to_dicts
from archagent.api.auth import parse_int_param


def query_workers(params: dict) -> dict:
    q = (params.get('q') or [''])[0].strip().lower()
    country = (params.get('country') or [''])[0].strip()
    worker_type = (params.get('type') or [''])[0].strip()
    trade = (params.get('trade') or [''])[0].strip().lower()
    verification_status = (params.get('verification_status') or [''])[0].strip()
    limit = parse_int_param(params, 'limit', 100, min_val=0, max_val=500)
    offset = parse_int_param(params, 'offset', 0, min_val=0)
    where = ['1=1']
    args = []
    if country:
        where.append('country=?')
        args.append(country)
    if worker_type:
        where.append('worker_type=?')
        args.append(worker_type)
    if verification_status:
        where.append('verification_status=?')
        args.append(verification_status)
    if trade:
        where.append('lower(trades) LIKE ?')
        args.append(f'%{trade}%')
    if q:
        where.append('(lower(name) LIKE ? OR lower(trades) LIKE ? OR lower(city) LIKE ? OR lower(address) LIKE ?)')
        like = f'%{q}%'
        args += [like] * 4
    where_sql = ' AND '.join(where)
    con = app_conn()
    rows = con.execute(
        f'SELECT id,name,worker_type,trades,country,city,address,lat,lon,phone,email,website,opening_hours,verification_status,source_url FROM expert_workers WHERE {where_sql} ORDER BY country, city, worker_type, name LIMIT ? OFFSET ?',
        args + [limit, offset],
    ).fetchall()
    total = con.execute(f'SELECT COUNT(*) FROM expert_workers WHERE {where_sql}', args).fetchone()[0]
    con.close()
    return {'total': total, 'items': rows_to_dicts(rows)}


def export_workers(params: dict) -> dict:
    fmt = (params.get('format') or ['csv'])[0].lower()
    if fmt != 'csv':
        raise ValueError('only csv worker export is supported')
    data = query_workers(params)
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    country = (params.get('country') or ['all'])[0] or 'all'
    safe_country = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in country)[:40]
    path = EXPORT_DIR / f'worker_candidates_{safe_country}_{stamp}.csv'
    fields = ['id', 'name', 'worker_type', 'trades', 'country', 'city', 'address', 'phone', 'email', 'website', 'verification_status', 'source_url']
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for item in data['items']:
        writer.writerow({k: item.get(k, '') for k in fields})
    content = buf.getvalue()
    path.write_text(content, encoding='utf-8')
    return {'format': 'csv', 'total': data['total'], 'item_count': len(data['items']), 'export_path': str(path), 'url': '/exports/' + path.name, 'csv': content}


def worker_stats() -> dict:
    con = app_conn()
    total = con.execute('SELECT COUNT(*) FROM expert_workers').fetchone()[0]
    types = rows_to_dicts(con.execute('SELECT worker_type, COUNT(*) count FROM expert_workers GROUP BY worker_type ORDER BY count DESC').fetchall())
    countries = rows_to_dicts(con.execute('SELECT country, COUNT(*) count FROM expert_workers GROUP BY country ORDER BY count DESC').fetchall())
    trades = rows_to_dicts(con.execute('SELECT trades, COUNT(*) count FROM expert_workers GROUP BY trades ORDER BY count DESC LIMIT 20').fetchall())
    verifications = rows_to_dicts(con.execute('SELECT verification_status, COUNT(*) count FROM expert_workers GROUP BY verification_status ORDER BY count DESC').fetchall())
    con.close()
    return {
        'total_workers': total,
        'types': types,
        'countries': countries,
        'trades': trades,
        'verifications': verifications,
        'source': 'OpenStreetMap Overpass public listings',
        'verification_status': 'public_listing_unverified',
    }


def verify_worker(payload: dict, principal=None) -> dict:
    from archagent.core.audit import log_activity
    worker_id = int(payload.get('worker_id') or payload.get('id') or 0)
    if not worker_id:
        raise ValueError('worker_id is required')
    status = payload.get('verification_status') or payload.get('status') or 'contacted'
    allowed = {'public_listing_unverified', 'contacted', 'replied', 'qualified', 'rejected', 'qualified_test'}
    if status not in allowed:
        raise ValueError('invalid verification_status')
    con = app_conn()
    row = con.execute('SELECT * FROM expert_workers WHERE id=?', (worker_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError('worker not found')
    verified_at = now()
    con.execute('UPDATE expert_workers SET verification_status=?, updated_at=? WHERE id=?', (status, verified_at, worker_id))
    cur = con.execute(
        '''INSERT INTO worker_verifications(worker_id,verification_status,contact_status,capabilities,regions_served,languages,certifications,notes,verified_by,verified_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (worker_id, status, payload.get('contact_status', ''), payload.get('capabilities', ''), payload.get('regions_served', ''), payload.get('languages', ''), payload.get('certifications', ''), payload.get('notes', ''), payload.get('verified_by', 'ArchAgent operator'), verified_at),
    )
    log_activity(con, 'worker_verification', f'Updated worker #{worker_id} verification to {status}', payload, principal)
    con.commit()
    updated = dict(con.execute('SELECT id,name,worker_type,trades,country,city,phone,email,website,verification_status,source_url FROM expert_workers WHERE id=?', (worker_id,)).fetchone())
    updated['verification_id'] = cur.lastrowid
    con.close()
    return updated
