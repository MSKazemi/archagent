"""Handler functions for dossier, analysis, outreach, and contractor-matching routes."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from archagent.core.audit import log_activity
from archagent.core.config import EXPORT_DIR
from archagent.core.db import app_conn, now, rows_to_dicts
from archagent.generation.dossiers import build_tender_dossier
from archagent.generation.proposals import (
    compliance_matrix_markdown,
    generate_compliance_matrix,
    generate_outreach_pack,
    generate_proposal,
)
from archagent.api.handlers.leads import get_lead
from archagent.api.handlers.bid_profiles import get_bid_profile


# ─── Multipart parser ─────────────────────────────────────────────────────────

def _parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """Parse multipart/form-data body. Returns {name: {data: bytes, filename: str|None}}."""
    boundary = None
    for part in content_type.split(';'):
        p = part.strip()
        if p.startswith('boundary='):
            boundary = p[9:].strip('"')
            break
    if not boundary:
        raise ValueError('No boundary in Content-Type multipart header')

    delimiter = ('--' + boundary).encode()
    parts_raw = body.split(delimiter)
    result: dict[str, dict] = {}

    for raw in parts_raw[1:]:
        if raw.strip() in (b'', b'--', b'--\r\n', b'--\n'):
            continue
        sep = b'\r\n\r\n' if b'\r\n\r\n' in raw else b'\n\n'
        if sep not in raw:
            continue
        header_bytes, content = raw.split(sep, 1)
        # Strip trailing boundary marker and CRLF
        content = content.rstrip(b'\r\n')
        if content.endswith(b'--'):
            content = content[:-2].rstrip(b'\r\n')

        name = filename = None
        for line in header_bytes.decode('utf-8', errors='replace').splitlines():
            low = line.lower()
            if 'content-disposition' in low:
                for seg in line.split(';'):
                    s = seg.strip()
                    if s.lower().startswith('name='):
                        name = s[5:].strip('"')
                    elif s.lower().startswith('filename='):
                        filename = s[9:].strip('"')
        if name:
            result[name] = {'data': content, 'filename': filename}

    return result


# ─── LLM dossier analyze ──────────────────────────────────────────────────────

def analyze_dossier(pdf_bytes: bytes, notice_id: str, bid_profile_id: str | None) -> dict:
    """Run analyst + compliance and persist result to tender_dossiers."""
    from archagent.intelligence.analyst import analyze_tender
    from archagent.intelligence.compliance import check_compliance

    azure_key = os.environ.get('AZURE_OPENAI_KEY', '').strip()
    if not azure_key:
        raise ValueError(
            'AZURE_OPENAI_KEY not configured — set it in .env to enable LLM analysis'
        )

    # SHA-256 cache: skip pdftotext on repeated analysis of same document
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    con = app_conn()
    cached_row = con.execute(
        'SELECT extracted_text, page_count, is_truncated FROM pdf_extractions WHERE pdf_sha256=?',
        (pdf_hash,),
    ).fetchone()
    con.close()

    cached_text = None
    if cached_row:
        cached_text = (cached_row['extracted_text'], cached_row['page_count'], bool(cached_row['is_truncated']))

    def _store_extraction(text: str, pages: int, truncated: bool) -> None:
        try:
            c = app_conn()
            c.execute(
                'INSERT OR IGNORE INTO pdf_extractions'
                '(pdf_sha256,notice_id,extracted_text,page_count,is_truncated,created_at)'
                ' VALUES (?,?,?,?,?,?)',
                (pdf_hash, notice_id or None, text, pages, 1 if truncated else 0, now()),
            )
            c.commit()
            c.close()
        except Exception:
            pass

    result = analyze_tender(
        pdf_bytes, notice_id=notice_id or '',
        _cached_text=cached_text,
        _on_extracted=None if cached_text else _store_extraction,
    )
    if result['status'] == 'error':
        return result

    # Compliance matching (skip if no profile requested)
    if bid_profile_id:
        profile = get_bid_profile(bid_profile_id)
        compliance = check_compliance(result.get('extracted_json') or {}, profile)
    else:
        compliance = check_compliance(result.get('extracted_json') or {}, None)

    # Persist to tender_dossiers
    safe_id = notice_id.replace('/', '-') if notice_id else 'unknown'
    export_path = str(EXPORT_DIR / f'analyst_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json')
    EXPORT_DIR.mkdir(exist_ok=True)

    full_output = {**result, 'compliance': compliance, 'notice_id': notice_id}
    Path(export_path).write_text(json.dumps(full_output, ensure_ascii=False, indent=2), encoding='utf-8')

    con = app_conn()
    cur = con.execute(
        'INSERT INTO tender_dossiers(source_notice_id,source,analyst_json,analyst_cost_eur,'
        'analyst_pages_analyzed,is_partial,phase,export_path,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (
            notice_id or 'upload',
            'dossier_analyze',
            json.dumps(result.get('extracted_json'), ensure_ascii=False),
            result.get('cost_estimate_eur', 0),
            result.get('pages_analyzed', 0),
            1 if result.get('is_truncated') or result.get('is_scanned') else 0,
            'llm',
            export_path,
            now(),
        ),
    )
    dossier_id = cur.lastrowid
    log_activity(con, 'dossier_analyze', f'LLM analysis dossier #{dossier_id} for {notice_id or "upload"}',
                 {'notice_id': notice_id, 'cost_eur': result.get('cost_estimate_eur')})
    con.commit()
    con.close()

    return {**full_output, 'dossier_id': dossier_id, 'export_path': export_path}


# Cooperative cancel: job ids requested to stop. Python threads can't be force-killed,
# so the runner checks membership before the expensive analysis call.
CANCEL_REQUESTS: set[int] = set()


def _run_analysis_job(job_id: int, pdf_bytes: bytes, notice_id: str, bid_profile_id: str | None) -> None:
    """Background thread: run a full analysis and update the job row."""
    con = app_conn()
    con.execute("UPDATE analysis_jobs SET status='running', started_at=? WHERE id=?", (now(), job_id))
    con.commit()
    con.close()
    if job_id in CANCEL_REQUESTS:
        CANCEL_REQUESTS.discard(job_id)
        con = app_conn()
        con.execute("UPDATE analysis_jobs SET status='canceled', error='Canceled before analysis', completed_at=? WHERE id=?",
                    (now(), job_id))
        con.commit()
        con.close()
        return
    try:
        result = analyze_dossier(pdf_bytes, notice_id, bid_profile_id)
        con = app_conn()
        con.execute(
            "UPDATE analysis_jobs SET status='done', result_json=?, completed_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), now(), job_id),
        )
        con.commit()
        con.close()
    except Exception as exc:
        con = app_conn()
        con.execute(
            "UPDATE analysis_jobs SET status='error', error=?, completed_at=? WHERE id=?",
            (str(exc), now(), job_id),
        )
        con.commit()
        con.close()


def submit_analysis_job(pdf_bytes: bytes, notice_id: str, bid_profile_id: str | None) -> dict:
    """Create a job row and start a background analysis thread immediately."""
    con = app_conn()
    cur = con.execute(
        "INSERT INTO analysis_jobs(status,notice_id,bid_profile_id,created_at)"
        " VALUES ('queued',?,?,?)",
        (notice_id or None, bid_profile_id or None, now()),
    )
    job_id = cur.lastrowid
    con.commit()
    con.close()
    t = threading.Thread(
        target=_run_analysis_job,
        args=(job_id, pdf_bytes, notice_id, bid_profile_id),
        daemon=True,
        name=f'analysis-job-{job_id}',
    )
    t.start()
    return {'job_id': job_id, 'status': 'queued'}


def cancel_analysis_job(job_id: int) -> dict:
    """Request cancellation of a job. Queued/not-yet-started jobs cancel immediately;
    running jobs are marked 'canceling' and stop cooperatively at the next checkpoint."""
    con = app_conn()
    row = con.execute('SELECT status FROM analysis_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        con.close()
        raise ValueError(f'Job {job_id} not found')
    status = row['status']
    if status in ('done', 'error', 'canceled'):
        con.close()
        return {'job_id': job_id, 'status': status, 'note': 'already finished'}
    CANCEL_REQUESTS.add(job_id)
    new_status = 'canceled' if status == 'queued' else 'canceling'
    con.execute('UPDATE analysis_jobs SET status=? WHERE id=?', (new_status, job_id))
    con.commit()
    con.close()
    return {'job_id': job_id, 'status': new_status}


def retry_analysis_job(job_id: int) -> dict:
    """Re-enqueue a finished/stuck job, reusing cached PDF text when available.

    The PDF bytes are not stored on the job row; retry depends on a cached extraction.
    Returns a 409-style conflict signal (raises ValueError) when re-upload is required.
    """
    con = app_conn()
    row = con.execute('SELECT * FROM analysis_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        con.close()
        raise ValueError(f'Job {job_id} not found')
    notice_id = row['notice_id']
    has_cached = False
    if notice_id:
        cnt = con.execute('SELECT COUNT(*) FROM pdf_extractions WHERE notice_id=?', (notice_id,)).fetchone()[0]
        has_cached = cnt > 0
    con.close()
    if not has_cached:
        raise ValueError('retry requires re-upload: original PDF is not cached for this job')
    # Re-enqueue: reuse the cached extraction path inside analyze_dossier (keyed by notice_id).
    con = app_conn()
    cur = con.execute(
        "INSERT INTO analysis_jobs(status,notice_id,bid_profile_id,created_at) VALUES ('queued',?,?,?)",
        (notice_id, row['bid_profile_id'], now()),
    )
    new_id = cur.lastrowid
    con.commit()
    con.close()
    t = threading.Thread(target=_run_analysis_job, args=(new_id, b'', notice_id, row['bid_profile_id']),
                         daemon=True, name=f'analysis-job-{new_id}')
    t.start()
    return {'job_id': new_id, 'status': 'queued', 'retried_from': job_id}


def get_analysis_job(job_id: int) -> dict:
    con = app_conn()
    row = con.execute('SELECT * FROM analysis_jobs WHERE id=?', (job_id,)).fetchone()
    con.close()
    if not row:
        raise ValueError(f'Job {job_id} not found')
    d = dict(row)
    if d.get('result_json'):
        try:
            d['result'] = json.loads(d.pop('result_json'))
        except Exception:
            d['result'] = None
    else:
        d.pop('result_json', None)
        d['result'] = None
    return d


def create_tender_dossier_payload(payload: dict, principal=None) -> dict:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    if not notice_id:
        raise ValueError('notice_id is required')
    return build_tender_dossier(notice_id, source=payload.get('source') or 'api', save=payload.get('save', True))


def match_contractors(payload: dict) -> list:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    text = (
        ' '.join([lead.get('category', ''), lead.get('title', ''), ' '.join(lead.get('trades', []))]).lower()
        if lead else (payload.get('query') or '').lower()
    )
    con = app_conn()
    rows = rows_to_dicts(con.execute('SELECT * FROM contractors ORDER BY risk ASC, id ASC').fetchall())
    worker_rows = rows_to_dicts(con.execute(
        'SELECT id,name,worker_type,trades,country,city,address,phone,email,website,verification_status,source_url FROM expert_workers ORDER BY country, city, name LIMIT 1000'
    ).fetchall())
    con.close()
    scored = []
    for row in rows:
        hay = (row['trades'] + ' ' + row['countries'] + ' ' + (row.get('notes') or '')).lower()
        score = sum(1 for token in set(text.replace('/', ' ').replace(',', ' ').split()) if len(token) > 3 and token in hay)
        row['match_score'] = min(100, 45 + score * 12)
        row['record_kind'] = 'seeded_network'
        scored.append(row)
    for row in worker_rows:
        hay = ' '.join(str(row.get(k) or '') for k in ('name', 'worker_type', 'trades', 'country', 'city', 'address')).lower()
        score = sum(1 for token in set(text.replace('/', ' ').replace(',', ' ').split()) if len(token) > 3 and token in hay)
        if score or not text:
            row['match_score'] = min(100, 35 + score * 15)
            row['record_kind'] = 'public_osm_listing'
            scored.append(row)
    return sorted(scored, key=lambda r: r['match_score'], reverse=True)[:80]


def create_outreach_pack(payload: dict, principal=None) -> dict:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead:
        raise ValueError('lead_notice_id is required and must exist')
    matches = match_contractors({'lead_notice_id': notice_id})
    pack = generate_outreach_pack(lead, matches)
    if payload.get('save'):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'outreach_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        body = (
            f"# Outreach Pack — {pack['title']}\n\n"
            f"## Contractor email\n\n{pack['contractor_email']}\n\n"
            f"## Buyer clarification email\n\n{pack['buyer_clarification_email']}\n\n"
            f"## Call script\n\n{pack['call_script']}\n"
        )
        path.write_text(body, encoding='utf-8')
        pack['export_path'] = str(path)
        pack['markdown'] = body
    return pack


def create_project_dossier(payload: dict, principal=None) -> dict:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead:
        raise ValueError('lead_notice_id is required and must exist')
    role = payload.get('company_role') or 'Architecture / construction company'
    package = payload.get('package_type') or 'Bid preparation package'
    proposal_body = generate_proposal(lead, role, package, payload.get('prospect') or '')
    matrix = generate_compliance_matrix(lead)
    compliance_md = compliance_matrix_markdown(matrix)
    matches = match_contractors({'lead_notice_id': notice_id})
    outreach = generate_outreach_pack(lead, matches)
    match_lines = []
    for idx, match in enumerate(matches[:12], 1):
        match_lines.append(
            f"{idx}. {match.get('name', 'Unnamed')} — {match.get('trades', '')} — "
            f"{match.get('city') or match.get('countries') or ''} — score {match.get('match_score')}% — "
            f"{match.get('verification_status') or match.get('record_kind') or ''}"
        )
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
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'dossier_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        path.write_text(markdown, encoding='utf-8')
        result['export_path'] = str(path)
        result['url'] = '/exports/' + path.name
    con = app_conn()
    log_activity(con, 'dossier', f'Generated project dossier for {notice_id}', {'notice_id': notice_id}, principal)
    con.commit()
    con.close()
    return result
