"""Handler functions for lead-radar export routes."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from archagent.core.config import EXPORT_DIR
from archagent.core.db import app_conn, now
from archagent.api.auth import parse_int_param
from archagent.api.handlers.leads import query_leads, recommendation_for_lead
from archagent.api.handlers.crm import get_customer_profile, profile_to_lead_params


def export_lead_radar(params: dict) -> dict:
    profile = get_customer_profile((params.get('profile_id') or [''])[0])
    fmt = (params.get('format') or ['markdown'])[0].lower()
    default_limit = (profile or {}).get('max_leads') or 30
    limit = min(parse_int_param(params, 'limit', default_limit, min_val=0), 100)
    lead_params = profile_to_lead_params(profile, params)
    lead_params['limit'] = [str(limit)]
    lead_params['sort'] = lead_params.get('sort') or ['score']
    data = query_leads(lead_params)
    items = data['items'][:limit]
    title = f"Lead Radar Report — {(profile or {}).get('company') or (profile or {}).get('name') or 'ArchAgent'}"
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in title)[:70]
    if fmt == 'csv':
        path = EXPORT_DIR / f'lead_radar_{safe}_{stamp}.csv'
        buf = io.StringIO()
        fields = ['source_notice_id', 'deadline_date', 'country_label', 'performance_city', 'category', 'relevance_score', 'estimated_value', 'currency', 'buyer_name', 'title', 'source_url', 'recommendation']
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        for lead in items:
            row = {k: lead.get(k, '') for k in fields}
            row['recommendation'] = recommendation_for_lead(lead)
            writer.writerow(row)
        content = buf.getvalue()
        path.write_text(content, encoding='utf-8')
        payload = {'title': title, 'profile': profile, 'format': 'csv', 'items': items, 'export_path': str(path), 'url': '/exports/' + path.name, 'csv': content}
    else:
        path = EXPORT_DIR / f'lead_radar_{safe}_{stamp}.md'
        lines = [
            f'# {title}', '',
            f'Generated: {now()}', '',
            'Human review required before bidding. Official tender documents, eligibility, scope, and commercial terms must be verified.',
            '',
            '| Score | Deadline | Location | Category | Buyer | Value | Action | Link |',
            '|---:|---|---|---|---|---:|---|---|',
        ]
        for lead in items:
            loc = lead.get('country_label', '') + ((' / ' + lead.get('performance_city')) if lead.get('performance_city') else '')
            lines.append(
                f"| {lead.get('relevance_score', '')} | {lead.get('deadline_date', '')} | {loc.replace('|', '-')} | "
                f"{(lead.get('category') or '').replace('|', '-')} | {(lead.get('buyer_name') or 'Not listed').replace('|', '-')} | "
                f"{lead.get('value_label', '')} | {recommendation_for_lead(lead).replace('|', '-')} | [Source]({lead.get('source_url') or ''}) |"
            )
        lines += [
            '', '## Notes', '',
            '- Suggested commercial offer: Lead Radar subscription or Bid Package depending on urgency and fit.',
            '- Contractor/public listings are unverified until contacted and qualified.',
        ]
        content = '\n'.join(lines) + '\n'
        path.write_text(content, encoding='utf-8')
        payload = {'title': title, 'profile': profile, 'format': 'markdown', 'items': items, 'export_path': str(path), 'url': '/exports/' + path.name, 'markdown': content}
    con = app_conn()
    cur = con.execute(
        'INSERT INTO lead_radar_exports(profile_id,format,title,filters_json,item_count,export_path,created_at) VALUES (?,?,?,?,?,?,?)',
        ((profile or {}).get('id'), payload['format'], title, json.dumps({k: v for k, v in params.items()}, ensure_ascii=False), len(items), str(path), now()),
    )
    con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)',
                ('lead_radar_export', f'Created lead radar export #{cur.lastrowid}', json.dumps({'path': str(path), 'items': len(items)}), now()))
    con.commit()
    con.close()
    payload['id'] = cur.lastrowid
    return payload
