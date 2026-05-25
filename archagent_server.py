#!/usr/bin/env python3
"""ArchAgent BuildingOS local revenue backend.
Dependency-free HTTP API over the existing actionable project SQLite database.
Run: python3 archagent_server.py --port 8091
"""
from __future__ import annotations

import argparse, csv, io, json, os, secrets, sqlite3, subprocess
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from proposal_engine import compliance_matrix_markdown, generate_building_audit, generate_compliance_matrix, generate_crm_followup, generate_outreach_pack, generate_proposal, lead_to_public_dict

BASE = Path(__file__).resolve().parent
LEADS_DB = BASE / 'archagent_actionable_projects.sqlite3'
APP_DB = BASE / 'archagent_app.sqlite3'
EXPORT_DIR = BASE / 'exports'
PRIVATE_API_PREFIXES = ('/api/',)
PUBLIC_GET_PATHS = {'/api/health'}

def configured_token() -> str:
    return os.getenv('ARCHAGENT_TOKEN', '').strip()

def auth_enabled() -> bool:
    return bool(configured_token())

def token_ok(headers) -> bool:
    token = configured_token()
    if not token:
        return True
    supplied = headers.get('X-ArchAgent-Token') or ''
    auth = headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        supplied = auth.split(' ', 1)[1]
    return secrets.compare_digest(supplied, token)

def now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

def app_conn():
    con = sqlite3.connect(APP_DB); con.row_factory = sqlite3.Row; return con

def leads_conn():
    con = sqlite3.connect(LEADS_DB); con.row_factory = sqlite3.Row; return con

def init_app_db() -> None:
    con = app_conn()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS prospects (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT, company TEXT,
      country TEXT, role TEXT, need TEXT, offer TEXT, status TEXT NOT NULL DEFAULT 'new',
      value_estimate REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proposals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, lead_notice_id TEXT, prospect_id INTEGER,
      company_role TEXT, package_type TEXT, title TEXT, body TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'template', status TEXT NOT NULL DEFAULT 'draft',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS contractors (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, countries TEXT NOT NULL,
      trades TEXT NOT NULL, availability TEXT, commercial_model TEXT,
      risk TEXT NOT NULL DEFAULT 'low', notes TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS activities (
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, message TEXT NOT NULL,
      payload_json TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS followups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      prospect_id INTEGER,
      lead_notice_id TEXT,
      subject TEXT NOT NULL,
      body TEXT NOT NULL,
      call_script TEXT,
      tasks_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customer_profiles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      company TEXT,
      email TEXT,
      countries TEXT,
      categories TEXT,
      trades TEXT,
      min_value REAL,
      max_leads INTEGER NOT NULL DEFAULT 30,
      status TEXT NOT NULL DEFAULT 'pilot',
      notes TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lead_radar_exports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_id INTEGER,
      format TEXT NOT NULL,
      title TEXT NOT NULL,
      filters_json TEXT NOT NULL,
      item_count INTEGER NOT NULL,
      export_path TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS expert_workers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL,
      source_id TEXT NOT NULL,
      name TEXT NOT NULL,
      worker_type TEXT NOT NULL,
      trades TEXT NOT NULL,
      country TEXT,
      city TEXT,
      address TEXT,
      lat REAL,
      lon REAL,
      phone TEXT,
      email TEXT,
      website TEXT,
      opening_hours TEXT,
      languages TEXT,
      verification_status TEXT NOT NULL DEFAULT 'public_listing_unverified',
      source_url TEXT,
      raw_json TEXT NOT NULL,
      imported_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(source, source_id)
    );
    """ )
    if con.execute('SELECT COUNT(*) FROM contractors').fetchone()[0] == 0:
        seed = [
            ('Facade & Insulation Crew', 'DEU,AUT,CHE', 'insulation,facade,envelope,roof', '2-4 weeks', 'referral fee or managed bid support', 'low', 'Good fit for roof/facade insulation tenders.'),
            ('Public Tender Bid Writer', 'EU', 'bid writing,compliance,proposal,procurement', '48h for first review', 'fixed bid package', 'low', 'Prepares compliance matrix and tender response checklist.'),
            ('Painting Contractor Network', 'DEU,FRA,BEL,LUX', 'painting,finishing,interior,facade painting', '1-3 weeks', 'lead fee + success fee', 'medium', 'Useful for small/medium painting frameworks and school/housing jobs.'),
            ('HVAC / Energy Partners', 'BEL,FRA,IRL,NLD', 'HVAC,energy,ventilation,heat pump,solar', '2-5 weeks', 'qualified appointment fee', 'low', 'Useful for public HVAC and energy upgrade opportunities.'),
            ('Architecture + Permit Studio', 'DEU,FRA,NLD', 'architecture,design,permits,planning', 'Discovery call in 7 days', 'scope package + project fee', 'low', 'Can review design/permit-heavy notices and prepare concept scope.'),
            ('General Contractor Marketplace', 'EU', 'construction,renovation,rehabilitation,multi-trade', 'varies', 'success fee', 'medium', 'Fallback for multi-trade work packages.'),
        ]
        con.executemany('INSERT INTO contractors(name,countries,trades,availability,commercial_model,risk,notes,created_at) VALUES (?,?,?,?,?,?,?,?)', [(*row, now()) for row in seed])
    con.commit(); con.close()

def rows_to_dicts(rows): return [dict(r) for r in rows]

def get_lead(notice_id: str):
    con = leads_conn(); row = con.execute('SELECT * FROM project_leads WHERE source_notice_id=?', (notice_id,)).fetchone(); con.close()
    return lead_to_public_dict(dict(row)) if row else None

def query_leads(params):
    q = (params.get('q') or [''])[0].strip().lower(); country = (params.get('country') or [''])[0].strip(); category = (params.get('category') or [''])[0].strip(); sort = (params.get('sort') or ['deadline'])[0]
    limit = min(int((params.get('limit') or ['100'])[0] or 100), 500); offset = int((params.get('offset') or ['0'])[0] or 0)
    where = ['deadline_date IS NOT NULL']; args = []
    if country: where.append('(performance_country=? OR buyer_country=?)'); args += [country, country]
    if category: where.append('category=?'); args.append(category)
    min_value = (params.get('min_value') or [''])[0]
    if min_value not in ('', None):
        where.append('(estimated_value IS NOT NULL AND estimated_value >= ?)')
        args.append(float(min_value))
    if q:
        where.append('(lower(title) LIKE ? OR lower(description) LIKE ? OR lower(buyer_name) LIKE ? OR lower(performance_city) LIKE ? OR lower(cpv_codes) LIKE ?)'); like = f'%{q}%'; args += [like] * 5
    order = {'score':'relevance_score DESC, deadline_date ASC','value':'estimated_value DESC, relevance_score DESC'}.get(sort, 'deadline_date ASC, relevance_score DESC')
    where_sql = ' AND '.join(where); con = leads_conn()
    rows = con.execute(f'SELECT * FROM project_leads WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
    total = con.execute(f'SELECT COUNT(*) FROM project_leads WHERE {where_sql}', args).fetchone()[0]; con.close()
    return {'total': total, 'items': [lead_to_public_dict(dict(r)) for r in rows]}

def stats():
    con = leads_conn(); total = con.execute('SELECT COUNT(*) FROM project_leads').fetchone()[0]
    cats = rows_to_dicts(con.execute('SELECT category, COUNT(*) count FROM project_leads GROUP BY category ORDER BY count DESC').fetchall())
    countries = rows_to_dicts(con.execute('SELECT COALESCE(performance_country,buyer_country,"") country, COUNT(*) count FROM project_leads GROUP BY country ORDER BY count DESC').fetchall())
    earliest = con.execute('SELECT MIN(deadline_date) FROM project_leads').fetchone()[0]; latest = con.execute('SELECT MAX(deadline_date) FROM project_leads').fetchone()[0]
    visible_eur = con.execute('SELECT SUM(estimated_value) FROM project_leads WHERE currency="EUR"').fetchone()[0] or 0; con.close()
    acon = app_conn(); prospects = acon.execute('SELECT COUNT(*) FROM prospects').fetchone()[0]; proposals = acon.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]; acon.close()
    return {'total_leads': total, 'categories': cats, 'countries': countries, 'earliest_deadline': earliest, 'latest_deadline': latest, 'visible_eur_value': visible_eur, 'prospects': prospects, 'proposals': proposals}

def read_json(handler):
    length = int(handler.headers.get('Content-Length') or 0)
    return json.loads(handler.rfile.read(length).decode('utf-8')) if length else {}

def create_prospect(payload):
    con = app_conn(); sql = "INSERT INTO prospects(name,email,company,country,role,need,offer,status,value_estimate,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (payload.get('name') or payload.get('company') or 'Unnamed prospect', payload.get('email',''), payload.get('company',''), payload.get('country',''), payload.get('role',''), payload.get('need',''), payload.get('offer','Lead Radar'), payload.get('status','new'), payload.get('value_estimate'), now(), now()))
    pid = cur.lastrowid; con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('prospect', f'New prospect #{pid}', json.dumps(payload), now()))
    con.commit(); row = con.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone(); con.close(); return dict(row)

def list_table(name):
    allowed = {'prospects','followups','proposals','contractors','activities','customer_profiles','lead_radar_exports'}
    if name not in allowed:
        raise ValueError('invalid table')
    con = app_conn(); rows = con.execute(f'SELECT * FROM {name} ORDER BY id DESC LIMIT 200').fetchall(); con.close(); return rows_to_dicts(rows)

def create_followup(payload):
    prospect_id = payload.get('prospect_id')
    if not prospect_id: raise ValueError('prospect_id is required')
    con = app_conn(); prospect = con.execute('SELECT * FROM prospects WHERE id=?', (prospect_id,)).fetchone()
    if not prospect:
        con.close(); raise ValueError('prospect not found')
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id') or None
    lead = get_lead(notice_id) if notice_id else None
    pack = generate_crm_followup(dict(prospect), lead)
    cur = con.execute('INSERT INTO followups(prospect_id,lead_notice_id,subject,body,call_script,tasks_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)', (prospect_id, notice_id, pack['subject'], pack['email'], pack['call_script'], json.dumps(pack['tasks'], ensure_ascii=False), payload.get('status','draft'), now(), now()))
    fid = cur.lastrowid
    con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('followup', f'Created follow-up #{fid} for prospect #{prospect_id}', json.dumps({'prospect_id': prospect_id, 'notice_id': notice_id}), now()))
    con.commit(); row = con.execute('SELECT * FROM followups WHERE id=?', (fid,)).fetchone(); con.close()
    result = dict(row); result.update(pack); result['id'] = fid; return result

def split_csv(value: str):
    return [x.strip() for x in (value or '').replace(';', ',').split(',') if x.strip()]

def create_customer_profile(payload):
    con = app_conn()
    sql = "INSERT INTO customer_profiles(name,company,email,countries,categories,trades,min_value,max_leads,status,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (
        payload.get('name') or payload.get('company') or 'Unnamed customer profile',
        payload.get('company',''), payload.get('email',''), payload.get('countries',''), payload.get('categories',''), payload.get('trades',''),
        payload.get('min_value') if payload.get('min_value') not in ('', None) else None,
        int(payload.get('max_leads') or 30), payload.get('status','pilot'), payload.get('notes',''), now(), now()))
    pid = cur.lastrowid
    con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('customer_profile', f'Created customer profile #{pid}', json.dumps(payload), now()))
    con.commit(); row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (pid,)).fetchone(); con.close(); return dict(row)

def get_customer_profile(profile_id):
    if not profile_id: return None
    con = app_conn(); row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (profile_id,)).fetchone(); con.close()
    return dict(row) if row else None

def profile_to_lead_params(profile, params):
    lead_params = {k: v for k, v in params.items()}
    if profile:
        countries = split_csv(profile.get('countries'))
        categories = split_csv(profile.get('categories'))
        if countries and not lead_params.get('country'):
            lead_params['country'] = [countries[0]]
        if categories and not lead_params.get('category'):
            lead_params['category'] = [categories[0]]
        if profile.get('min_value') not in ('', None) and not lead_params.get('min_value'):
            lead_params['min_value'] = [str(profile.get('min_value'))]
    return lead_params

def recommendation_for_lead(lead):
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

def export_lead_radar(params):
    profile = get_customer_profile((params.get('profile_id') or [''])[0])
    fmt = (params.get('format') or ['markdown'])[0].lower()
    limit = min(int((params.get('limit') or [str((profile or {}).get('max_leads') or 30)])[0] or 30), 100)
    lead_params = profile_to_lead_params(profile, params)
    lead_params['limit'] = [str(limit)]
    lead_params['sort'] = lead_params.get('sort') or ['score']
    data = query_leads(lead_params)
    items = data['items'][:limit]
    title = f"Lead Radar Report — {(profile or {}).get('company') or (profile or {}).get('name') or 'ArchAgent'}"
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe = ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in title)[:70]
    if fmt == 'csv':
        path = EXPORT_DIR / f'lead_radar_{safe}_{stamp}.csv'
        buf = io.StringIO()
        fields = ['source_notice_id','deadline_date','country_label','performance_city','category','relevance_score','estimated_value','currency','buyer_name','title','source_url','recommendation']
        writer = csv.DictWriter(buf, fieldnames=fields); writer.writeheader()
        for lead in items:
            row = {k: lead.get(k,'') for k in fields}; row['recommendation'] = recommendation_for_lead(lead); writer.writerow(row)
        content = buf.getvalue(); path.write_text(content, encoding='utf-8')
        payload = {'title': title, 'profile': profile, 'format': 'csv', 'items': items, 'export_path': str(path), 'url': '/exports/' + path.name, 'csv': content}
    else:
        path = EXPORT_DIR / f'lead_radar_{safe}_{stamp}.md'
        lines = [f'# {title}', '', f'Generated: {now()}', '', 'Human review required before bidding. Official tender documents, eligibility, scope, and commercial terms must be verified.', '', '| Score | Deadline | Location | Category | Buyer | Value | Action | Link |', '|---:|---|---|---|---|---:|---|---|']
        for lead in items:
            loc = lead.get('country_label','') + ((' / ' + lead.get('performance_city')) if lead.get('performance_city') else '')
            lines.append(f"| {lead.get('relevance_score','')} | {lead.get('deadline_date','')} | {loc.replace('|','-')} | {(lead.get('category') or '').replace('|','-')} | {(lead.get('buyer_name') or 'Not listed').replace('|','-')} | {lead.get('value_label','')} | {recommendation_for_lead(lead).replace('|','-')} | [Source]({lead.get('source_url') or ''}) |")
        lines += ['', '## Notes', '', '- Suggested commercial offer: Lead Radar subscription or Bid Package depending on urgency and fit.', '- Contractor/public listings are unverified until contacted and qualified.']
        content = '\n'.join(lines) + '\n'; path.write_text(content, encoding='utf-8')
        payload = {'title': title, 'profile': profile, 'format': 'markdown', 'items': items, 'export_path': str(path), 'url': '/exports/' + path.name, 'markdown': content}
    con = app_conn()
    cur = con.execute('INSERT INTO lead_radar_exports(profile_id,format,title,filters_json,item_count,export_path,created_at) VALUES (?,?,?,?,?,?,?)', ((profile or {}).get('id'), payload['format'], title, json.dumps({k:v for k,v in params.items()}, ensure_ascii=False), len(items), str(path), now()))
    con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('lead_radar_export', f'Created lead radar export #{cur.lastrowid}', json.dumps({'path': str(path), 'items': len(items)}), now()))
    con.commit(); con.close()
    payload['id'] = cur.lastrowid
    return payload

def query_workers(params):
    q = (params.get('q') or [''])[0].strip().lower(); country = (params.get('country') or [''])[0].strip(); worker_type = (params.get('type') or [''])[0].strip(); trade = (params.get('trade') or [''])[0].strip().lower()
    limit = min(int((params.get('limit') or ['100'])[0] or 100), 500); offset = int((params.get('offset') or ['0'])[0] or 0)
    where = ['1=1']; args = []
    if country: where.append('country=?'); args.append(country)
    if worker_type: where.append('worker_type=?'); args.append(worker_type)
    if trade: where.append('lower(trades) LIKE ?'); args.append(f'%{trade}%')
    if q:
        where.append('(lower(name) LIKE ? OR lower(trades) LIKE ? OR lower(city) LIKE ? OR lower(address) LIKE ?)'); like = f'%{q}%'; args += [like] * 4
    where_sql = ' AND '.join(where); con = app_conn()
    rows = con.execute(f'SELECT id,name,worker_type,trades,country,city,address,lat,lon,phone,email,website,opening_hours,verification_status,source_url FROM expert_workers WHERE {where_sql} ORDER BY country, city, worker_type, name LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
    total = con.execute(f'SELECT COUNT(*) FROM expert_workers WHERE {where_sql}', args).fetchone()[0]
    con.close(); return {'total': total, 'items': rows_to_dicts(rows)}

def worker_stats():
    con = app_conn()
    total = con.execute('SELECT COUNT(*) FROM expert_workers').fetchone()[0]
    types = rows_to_dicts(con.execute('SELECT worker_type, COUNT(*) count FROM expert_workers GROUP BY worker_type ORDER BY count DESC').fetchall())
    countries = rows_to_dicts(con.execute('SELECT country, COUNT(*) count FROM expert_workers GROUP BY country ORDER BY count DESC').fetchall())
    trades = rows_to_dicts(con.execute('SELECT trades, COUNT(*) count FROM expert_workers GROUP BY trades ORDER BY count DESC LIMIT 20').fetchall())
    con.close(); return {'total_workers': total, 'types': types, 'countries': countries, 'trades': trades, 'source': 'OpenStreetMap Overpass public listings', 'verification_status': 'public_listing_unverified'}

def generate_compliance_for_payload(payload):
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead: raise ValueError('lead_notice_id is required and must exist')
    matrix = generate_compliance_matrix(lead)
    markdown = compliance_matrix_markdown(matrix)
    if payload.get('save'):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'compliance_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        path.write_text(markdown, encoding='utf-8')
        matrix['export_path'] = str(path)
    matrix['markdown'] = markdown
    return matrix

def export_proposal(params):
    pid = (params.get('id') or [''])[0]
    if not pid: raise ValueError('proposal id is required')
    con = app_conn(); row = con.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone(); con.close()
    if not row: raise ValueError('proposal not found')
    proposal = dict(row)
    EXPORT_DIR.mkdir(exist_ok=True)
    safe_title = ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in (proposal.get('title') or 'proposal'))[:70]
    path = EXPORT_DIR / f'proposal_{proposal["id"]}_{safe_title}.md'
    markdown = f"# {proposal.get('title') or 'ArchAgent Proposal'}\n\n- Lead notice ID: {proposal.get('lead_notice_id') or ''}\n- Package: {proposal.get('package_type') or ''}\n- Company role: {proposal.get('company_role') or ''}\n- Source: {proposal.get('source') or ''}\n- Status: {proposal.get('status') or ''}\n- Created: {proposal.get('created_at') or ''}\n\n---\n\n{proposal.get('body') or ''}\n"
    path.write_text(markdown, encoding='utf-8')
    return {'id': proposal['id'], 'path': str(path), 'url': '/exports/' + path.name, 'markdown': markdown}

def create_proposal(payload, use_hermes=False):
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id'); lead = get_lead(notice_id) if notice_id else None
    if not lead: raise ValueError('lead_notice_id is required and must exist')
    role = payload.get('company_role') or 'Architecture / construction company'; package = payload.get('package_type') or 'Bid package'; prospect = payload.get('prospect') or ''
    source = 'template'; body = generate_proposal(lead, role, package, prospect)
    if use_hermes and os.getenv('HERMES_PROPOSAL_ENABLED') == '1':
        prompt = f"Improve this ArchAgent bid/proposal draft. Keep it practical, concise, and human-review safe. Do not invent certifications or prices. Return only the improved proposal.\n\n{body}"
        try:
            result = subprocess.run(['hermes', 'chat', '-q', prompt, '-Q', '--toolsets', 'safe'], cwd=str(BASE), text=True, capture_output=True, timeout=180)
            if result.returncode == 0 and result.stdout.strip(): body = result.stdout.strip(); source = 'hermes'
        except Exception as exc: body += f"\n\n[Hermes enhancement skipped: {exc}]"
    con = app_conn(); sql = "INSERT INTO proposals(lead_notice_id,prospect_id,company_role,package_type,title,body,source,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (notice_id, payload.get('prospect_id'), role, package, lead['short_title'], body, source, 'draft', now(), now()))
    pid = cur.lastrowid; con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('proposal', f'Created proposal #{pid} for {notice_id}', json.dumps({'notice_id': notice_id, 'source': source}), now()))
    con.commit(); row = con.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone(); con.close(); return dict(row)

def match_contractors(payload):
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id'); lead = get_lead(notice_id) if notice_id else None
    text = ' '.join([lead.get('category',''), lead.get('title',''), ' '.join(lead.get('trades', []))]).lower() if lead else (payload.get('query') or '').lower()
    con = app_conn(); rows = rows_to_dicts(con.execute('SELECT * FROM contractors ORDER BY risk ASC, id ASC').fetchall()); worker_rows = rows_to_dicts(con.execute('SELECT id,name,worker_type,trades,country,city,address,phone,email,website,verification_status,source_url FROM expert_workers ORDER BY country, city, name LIMIT 1000').fetchall()); con.close(); scored = []
    for row in rows:
        hay = (row['trades'] + ' ' + row['countries'] + ' ' + (row.get('notes') or '')).lower(); score = sum(1 for token in set(text.replace('/',' ').replace(',',' ').split()) if len(token) > 3 and token in hay)
        row['match_score'] = min(100, 45 + score * 12); row['record_kind'] = 'seeded_network'; scored.append(row)
    for row in worker_rows:
        hay = ' '.join(str(row.get(k) or '') for k in ('name','worker_type','trades','country','city','address')).lower(); score = sum(1 for token in set(text.replace('/',' ').replace(',',' ').split()) if len(token) > 3 and token in hay)
        if score or not text:
            row['match_score'] = min(100, 35 + score * 15); row['record_kind'] = 'public_osm_listing'; scored.append(row)
    return sorted(scored, key=lambda r: r['match_score'], reverse=True)[:80]

def create_outreach_pack(payload):
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead: raise ValueError('lead_notice_id is required and must exist')
    matches = match_contractors({'lead_notice_id': notice_id})
    pack = generate_outreach_pack(lead, matches)
    if payload.get('save'):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'outreach_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        body = f"# Outreach Pack — {pack['title']}\n\n## Contractor email\n\n{pack['contractor_email']}\n\n## Buyer clarification email\n\n{pack['buyer_clarification_email']}\n\n## Call script\n\n{pack['call_script']}\n"
        path.write_text(body, encoding='utf-8')
        pack['export_path'] = str(path)
        pack['markdown'] = body
    return pack

def create_project_dossier(payload):
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead: raise ValueError('lead_notice_id is required and must exist')
    role = payload.get('company_role') or 'Architecture / construction company'
    package = payload.get('package_type') or 'Bid preparation package'
    proposal_body = generate_proposal(lead, role, package, payload.get('prospect') or '')
    matrix = generate_compliance_matrix(lead)
    compliance_md = compliance_matrix_markdown(matrix)
    matches = match_contractors({'lead_notice_id': notice_id})
    outreach = generate_outreach_pack(lead, matches)
    match_lines = []
    for idx, match in enumerate(matches[:12], 1):
        match_lines.append(f"{idx}. {match.get('name','Unnamed')} — {match.get('trades','')} — {match.get('city') or match.get('countries') or ''} — score {match.get('match_score')}% — {match.get('verification_status') or match.get('record_kind') or ''}")
    markdown = f"""# ArchAgent Project Dossier — {lead.get('short_title')}

Generated: {now()}
Lead notice ID: {notice_id}
Buyer: {lead.get('buyer_name') or 'Not listed'}
Location: {lead.get('country_label')}{(' / ' + lead.get('performance_city')) if lead.get('performance_city') else ''}
Deadline: {lead.get('deadline_date') or 'Not listed'}
Estimated value: {lead.get('value_label')}
Official source: {lead.get('source_url') or ''}

Note: This is a first-pass operating dossier. Tender documents, commercial terms, legal requirements, and public listings must be checked by a human before submission or outreach.

## 1. Proposal Draft

{proposal_body}

## 2. Compliance Matrix

{compliance_md}

## 3. Top Expert / Worker Matches

Public listings are unverified outreach leads, not vetted partners.

{chr(10).join(match_lines) if match_lines else 'No matches found yet.'}

## 4. Contractor Outreach Email

{outreach['contractor_email']}

## 5. Buyer Clarification Email

{outreach['buyer_clarification_email']}

## 6. Sales Call Script

{outreach['call_script']}
"""
    result = {'title': lead.get('short_title'), 'notice_id': notice_id, 'markdown': markdown, 'matches': matches[:12]}
    if payload.get('save', True):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'dossier_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        path.write_text(markdown, encoding='utf-8')
        result['export_path'] = str(path)
        result['url'] = '/exports/' + path.name
    con = app_conn()
    con.execute('INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)', ('dossier', f'Generated project dossier for {notice_id}', json.dumps({'notice_id': notice_id}), now()))
    con.commit(); con.close()
    return result

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'); self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-ArchAgent-Token'); super().end_headers()
    def json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8'); self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def unauthorized(self): return self.json({'error': 'unauthorized', 'message': 'Set X-ArchAgent-Token or Authorization: Bearer token.'}, 401)
    def require_auth(self, parsed):
        if parsed.path in PUBLIC_GET_PATHS: return True
        if parsed.path.startswith(PRIVATE_API_PREFIXES) and not token_ok(self.headers): return False
        return True
    def do_OPTIONS(self): self.send_response(204); self.end_headers()
    def do_GET(self):
        parsed = urlparse(self.path); params = parse_qs(parsed.query)
        if not self.require_auth(parsed): return self.unauthorized()
        try:
            if parsed.path == '/api/health': return self.json({'ok': True, 'auth_enabled': auth_enabled(), 'time': now()})
            if parsed.path == '/api/stats': return self.json(stats())
            if parsed.path == '/api/leads': return self.json(query_leads(params))
            if parsed.path == '/api/lead': return self.json(get_lead((params.get('id') or [''])[0]) or {})
            if parsed.path == '/api/prospects': return self.json({'items': list_table('prospects')})
            if parsed.path == '/api/customer-profiles': return self.json({'items': list_table('customer_profiles')})
            if parsed.path == '/api/lead-radar/export': return self.json(export_lead_radar(params))
            if parsed.path == '/api/lead-radar/exports': return self.json({'items': list_table('lead_radar_exports')})
            if parsed.path == '/api/followups': return self.json({'items': list_table('followups')})
            if parsed.path == '/api/proposals': return self.json({'items': list_table('proposals')})
            if parsed.path == '/api/proposals/export': return self.json(export_proposal(params))
            if parsed.path == '/api/contractors': return self.json({'items': list_table('contractors')})
            if parsed.path == '/api/workers': return self.json(query_workers(params))
            if parsed.path == '/api/worker-stats': return self.json(worker_stats())
            if parsed.path == '/api/activities': return self.json({'items': list_table('activities')})
            if parsed.path == '/app': self.path = '/app.html'; return super().do_GET()
            return super().do_GET()
        except Exception as exc: return self.json({'error': str(exc)}, 500)
    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.require_auth(parsed): return self.unauthorized()
        try:
            payload = read_json(self)
            if parsed.path == '/api/prospects': return self.json(create_prospect(payload), 201)
            if parsed.path == '/api/customer-profiles': return self.json(create_customer_profile(payload), 201)
            if parsed.path == '/api/followups': return self.json(create_followup(payload), 201)
            if parsed.path == '/api/proposals': return self.json(create_proposal(payload, use_hermes=False), 201)
            if parsed.path == '/api/proposals/hermes': return self.json(create_proposal(payload, use_hermes=True), 201)
            if parsed.path == '/api/compliance': return self.json(generate_compliance_for_payload(payload), 201)
            if parsed.path == '/api/audit': return self.json({'body': generate_building_audit(payload)})
            if parsed.path == '/api/match': return self.json({'items': match_contractors(payload)})
            if parsed.path == '/api/outreach': return self.json(create_outreach_pack(payload), 201)
            if parsed.path == '/api/dossier': return self.json(create_project_dossier(payload), 201)
            return self.json({'error': 'not found'}, 404)
        except Exception as exc: return self.json({'error': str(exc)}, 400)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=8091); args = parser.parse_args(); init_app_db(); os.chdir(BASE)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler); print(f'ArchAgent server running: http://127.0.0.1:{args.port}/app'); print(f'API stats: http://127.0.0.1:{args.port}/api/stats'); server.serve_forever()
if __name__ == '__main__': main()
