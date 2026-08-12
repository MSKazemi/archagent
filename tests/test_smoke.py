#!/usr/bin/env python3
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
"""Smoke tests for ArchAgent BuildingOS local API."""
import json, subprocess, time, urllib.request, urllib.error, sys, os
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent  # project root
PORT = 8092
URL = f'http://127.0.0.1:{PORT}'

# Authenticate via the legacy token (admin-equivalent). This makes smoke deterministic
# regardless of whether the app DB already has users (any user disables the
# "no users → implicit admin" dev path). We force a known token into the server's env
# and send it on every request.
TOKEN = os.environ.get('ARCHAGENT_TOKEN') or 'smoke-test-token'
_HDRS = {'X-ArchAgent-Token': TOKEN}

def fetch(path, payload=None):
    if payload is None:
        return json.load(urllib.request.urlopen(urllib.request.Request(URL + path, headers=_HDRS), timeout=10))
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(URL + path, data=data, headers={'Content-Type': 'application/json', **_HDRS}, method='POST')
    return json.load(urllib.request.urlopen(req, timeout=10))

_env = {**os.environ, 'ARCHAGENT_TOKEN': TOKEN}
proc = subprocess.Popen([sys.executable, 'archagent_server.py', '--port', str(PORT)], cwd=BASE, env=_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(40):
        try:
            stats = fetch('/api/stats')
            break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError('server did not start')
    assert stats['total_leads'] >= 400, stats
    leads = fetch('/api/leads?limit=2')
    assert leads['items'], leads
    lead = leads['items'][0]
    proposal = fetch('/api/proposals', {'lead_notice_id': lead['source_notice_id'], 'company_role': 'Architecture studio', 'package_type': 'Bid package'})
    assert 'Bid preparation checklist' in proposal['body']
    compliance = fetch('/api/compliance', {'lead_notice_id': lead['source_notice_id'], 'save': True})
    assert 'Compliance Matrix' in compliance['markdown'] and compliance['items'], compliance
    exported = fetch(f"/api/proposals/export?id={proposal['id']}")
    assert exported['path'].endswith('.md') and 'Bid preparation checklist' in exported['markdown'], exported
    prospect = fetch('/api/prospects', {'name': 'Smoke Test', 'company': 'ArchAgent', 'offer': 'Lead Radar'})
    assert prospect['id']
    followup = fetch('/api/followups', {'prospect_id': prospect['id'], 'lead_notice_id': lead['source_notice_id']})
    assert 'email' in followup and 'call_script' in followup and followup['tasks'], followup
    match = fetch('/api/match', {'lead_notice_id': lead['source_notice_id']})
    assert match['items']
    outreach = fetch('/api/outreach', {'lead_notice_id': lead['source_notice_id'], 'save': True})
    assert 'contractor_email' in outreach and 'call_script' in outreach and outreach.get('export_path'), outreach
    dossier = fetch('/api/dossier', {'lead_notice_id': lead['source_notice_id'], 'company_role': 'Architecture studio', 'package_type': 'Bid package', 'save': True})
    assert 'Project Dossier' in dossier['markdown'] and dossier.get('export_path') and dossier['matches'], dossier
    workers = fetch('/api/workers?limit=5')
    assert workers['total'] >= 100 and workers['items'], workers
    worker_stats = fetch('/api/worker-stats')
    assert worker_stats['total_workers'] >= 100, worker_stats
    audit = fetch('/api/audit', {'building_type':'Office','year':'1980','problem':'energy loss'})
    assert 'Building Health Report' in audit['body']
    print('PASS smoke: leads, proposal, compliance, export, prospect, follow-up, match, outreach, dossier, workers, audit')
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired: proc.kill()
