"""Handler functions for leads-related API routes."""
from __future__ import annotations

from archagent.core.db import app_conn, leads_conn, rows_to_dicts
from archagent.api.auth import parse_bool_param, parse_float_param, parse_int_param
from archagent.generation.proposals import lead_to_public_dict


def get_lead(notice_id: str):
    con = leads_conn()
    row = con.execute('SELECT * FROM project_leads WHERE source_notice_id=?', (notice_id,)).fetchone()
    con.close()
    return lead_to_public_dict(dict(row)) if row else None


def query_leads(params: dict) -> dict:
    q = (params.get('q') or [''])[0].strip().lower()
    country = (params.get('country') or [''])[0].strip()
    category = (params.get('category') or [''])[0].strip()
    sort = (params.get('sort') or ['deadline'])[0]
    include_expired = parse_bool_param(params, 'include_expired', False)
    limit = parse_int_param(params, 'limit', 100, min_val=0, max_val=500)
    offset = parse_int_param(params, 'offset', 0, min_val=0)
    where = ['deadline_date IS NOT NULL']
    args = []
    if not include_expired:
        where.append("deadline_date >= date('now')")
    if country:
        where.append('(performance_country=? OR buyer_country=?)')
        args += [country, country]
    if category:
        where.append('category=?')
        args.append(category)
    min_value = parse_float_param(params, 'min_value')
    if min_value is not None:
        where.append('(estimated_value IS NOT NULL AND estimated_value >= ?)')
        args.append(min_value)
    if q:
        where.append('(lower(title) LIKE ? OR lower(description) LIKE ? OR lower(buyer_name) LIKE ? OR lower(performance_city) LIKE ? OR lower(cpv_codes) LIKE ?)')
        like = f'%{q}%'
        args += [like] * 5
    order = {'score': 'relevance_score DESC, deadline_date ASC', 'value': 'estimated_value DESC, relevance_score DESC'}.get(sort, 'deadline_date ASC, relevance_score DESC')
    where_sql = ' AND '.join(where)
    con = leads_conn()
    rows = con.execute(f'SELECT * FROM project_leads WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
    total = con.execute(f'SELECT COUNT(*) FROM project_leads WHERE {where_sql}', args).fetchone()[0]
    con.close()
    return {'total': total, 'items': [lead_to_public_dict(dict(r)) for r in rows], 'include_expired': include_expired}


def stats() -> dict:
    con = leads_conn()
    total = con.execute('SELECT COUNT(*) FROM project_leads').fetchone()[0]
    cats = rows_to_dicts(con.execute('SELECT category, COUNT(*) count FROM project_leads GROUP BY category ORDER BY count DESC').fetchall())
    countries = rows_to_dicts(con.execute('SELECT COALESCE(performance_country,buyer_country,"") country, COUNT(*) count FROM project_leads GROUP BY country ORDER BY count DESC').fetchall())
    earliest = con.execute('SELECT MIN(deadline_date) FROM project_leads').fetchone()[0]
    latest = con.execute('SELECT MAX(deadline_date) FROM project_leads').fetchone()[0]
    visible_eur = con.execute('SELECT SUM(estimated_value) FROM project_leads WHERE currency="EUR"').fetchone()[0] or 0
    con.close()
    acon = app_conn()
    prospects = acon.execute('SELECT COUNT(*) FROM prospects').fetchone()[0]
    proposals = acon.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]
    acon.close()
    return {
        'total_leads': total,
        'categories': cats,
        'countries': countries,
        'earliest_deadline': earliest,
        'latest_deadline': latest,
        'visible_eur_value': visible_eur,
        'prospects': prospects,
        'proposals': proposals,
    }


def recommendation_for_lead(lead: dict) -> str:
    days = lead.get('days_left')
    value = lead.get('estimated_value')
    risks = lead.get('risks') or []
    if days is not None and days <= 3:
        return 'Urgent review: confirm eligibility and download tender documents today.'
    if value:
        return 'Good paid bid-package candidate: value is disclosed and scope can be qualified.'
    if any('Estimated value not disclosed' in r for r in risks):
        return 'Lead-radar candidate: verify tender documents before pricing a bid package.'
    return 'Review fit with customer trade/capacity, then decide bid, partner, or ignore.'
