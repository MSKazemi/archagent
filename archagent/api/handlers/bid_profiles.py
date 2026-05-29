"""Handler functions for bid-profile API routes."""
from __future__ import annotations

import json

from archagent.core.audit import log_activity
from archagent.core.db import app_conn, now


def create_bid_profile(payload: dict, principal=None) -> dict:
    name = (payload.get('company_name') or '').strip()
    if not name:
        raise ValueError('company_name is required')
    ts = now()
    con = app_conn()
    cur = con.execute(
        'INSERT INTO bid_profiles(company_name,email,soa_qualifications,ateco_codes,'
        'certifications_held,avg_project_value_eur,geographic_regions,notes,created_at,updated_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (
            name,
            payload.get('email', ''),
            json.dumps(payload.get('soa_qualifications') or [], ensure_ascii=False),
            json.dumps(payload.get('ateco_codes') or [], ensure_ascii=False),
            json.dumps(payload.get('certifications_held') or [], ensure_ascii=False),
            payload.get('avg_project_value_eur'),
            json.dumps(payload.get('geographic_regions') or [], ensure_ascii=False),
            payload.get('notes', ''),
            ts, ts,
        ),
    )
    pid = cur.lastrowid
    log_activity(con, 'bid_profile', f'Created bid profile #{pid} for {name}', {'id': pid}, principal)
    con.commit()
    row = con.execute('SELECT * FROM bid_profiles WHERE id=?', (pid,)).fetchone()
    con.close()
    return dict(row)


def get_bid_profile(profile_id) -> dict | None:
    con = app_conn()
    row = con.execute('SELECT * FROM bid_profiles WHERE id=?', (profile_id,)).fetchone()
    con.close()
    return dict(row) if row else None
