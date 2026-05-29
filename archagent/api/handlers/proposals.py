"""Handler functions for proposal generation API routes."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

from archagent.core.audit import log_activity
from archagent.core.config import BASE, EXPORT_DIR
from archagent.core.db import app_conn, now
from archagent.generation.proposals import (
    compliance_matrix_markdown,
    generate_building_audit,
    generate_compliance_matrix,
    generate_proposal,
)
from archagent.api.handlers.leads import get_lead


def export_proposal(params: dict) -> dict:
    pid = (params.get('id') or [''])[0]
    if not pid:
        raise ValueError('proposal id is required')
    con = app_conn()
    row = con.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
    con.close()
    if not row:
        raise ValueError('proposal not found')
    proposal = dict(row)
    EXPORT_DIR.mkdir(exist_ok=True)
    safe_title = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in (proposal.get('title') or 'proposal'))[:70]
    path = EXPORT_DIR / f'proposal_{proposal["id"]}_{safe_title}.md'
    markdown = (
        f"# {proposal.get('title') or 'ArchAgent Proposal'}\n\n"
        f"- Lead notice ID: {proposal.get('lead_notice_id') or ''}\n"
        f"- Package: {proposal.get('package_type') or ''}\n"
        f"- Company role: {proposal.get('company_role') or ''}\n"
        f"- Source: {proposal.get('source') or ''}\n"
        f"- Status: {proposal.get('status') or ''}\n"
        f"- Created: {proposal.get('created_at') or ''}\n\n"
        f"---\n\n{proposal.get('body') or ''}\n"
    )
    path.write_text(markdown, encoding='utf-8')
    return {'id': proposal['id'], 'path': str(path), 'url': '/exports/' + path.name, 'markdown': markdown}


def create_proposal(payload: dict, use_hermes: bool = False, principal=None) -> dict:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead:
        raise ValueError('lead_notice_id is required and must exist')
    role = payload.get('company_role') or 'Architecture / construction company'
    package = payload.get('package_type') or 'Bid package'
    prospect = payload.get('prospect') or ''
    source = 'template'
    body = generate_proposal(lead, role, package, prospect)
    if use_hermes and os.getenv('HERMES_PROPOSAL_ENABLED') == '1':
        prompt = (
            f"Improve this ArchAgent bid/proposal draft. Keep it practical, concise, and human-review safe. "
            f"Do not invent certifications or prices. Return only the improved proposal.\n\n{body}"
        )
        try:
            result = subprocess.run(
                ['hermes', 'chat', '-q', prompt, '-Q', '--toolsets', 'safe'],
                cwd=str(BASE), text=True, capture_output=True, timeout=180,
            )
            if result.returncode == 0 and result.stdout.strip():
                body = result.stdout.strip()
                source = 'hermes'
        except Exception as exc:
            body += f"\n\n[Hermes enhancement skipped: {exc}]"
    con = app_conn()
    sql = "INSERT INTO proposals(lead_notice_id,prospect_id,company_role,package_type,title,body,source,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
    cur = con.execute(sql, (notice_id, payload.get('prospect_id'), role, package, lead['short_title'], body, source, 'draft', now(), now()))
    pid = cur.lastrowid
    log_activity(con, 'proposal', f'Created proposal #{pid} for {notice_id}',
                 {'notice_id': notice_id, 'source': source}, principal)
    con.commit()
    row = con.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
    con.close()
    return dict(row)


def generate_compliance_for_payload(payload: dict) -> dict:
    notice_id = payload.get('lead_notice_id') or payload.get('notice_id')
    lead = get_lead(notice_id) if notice_id else None
    if not lead:
        raise ValueError('lead_notice_id is required and must exist')
    matrix = generate_compliance_matrix(lead)
    markdown = compliance_matrix_markdown(matrix)
    if payload.get('save'):
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_id = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in notice_id)
        path = EXPORT_DIR / f'compliance_{safe_id}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.md'
        path.write_text(markdown, encoding='utf-8')
        matrix['export_path'] = str(path)
    matrix['markdown'] = markdown
    return matrix


def generate_building_audit_handler(payload: dict) -> str:
    return generate_building_audit(payload)
