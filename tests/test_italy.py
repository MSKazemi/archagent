#!/usr/bin/env python3
"""Regression tests for production-grade Italy workflows."""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import sqlite3
from pathlib import Path

from archagent.core.config import APP_DB
from archagent.core.db import init_app_db
from archagent.api.handlers.italy import italy_summary
from archagent.api.handlers.workers import query_workers, verify_worker, export_workers
from archagent.intelligence.scoring import score_italy_fit, select_top_italy_leads
from archagent.generation.dossiers import build_tender_dossier

BASE = Path(__file__).resolve().parent.parent  # project root


def cleanup():
    con = sqlite3.connect(APP_DB)
    con.execute("DELETE FROM tender_dossiers WHERE source='production_italy_test'")
    con.execute("DELETE FROM worker_verifications WHERE notes LIKE '%production_italy_test%'")
    con.execute("UPDATE expert_workers SET verification_status='public_listing_unverified' WHERE verification_status='qualified_test'")
    con.execute("DELETE FROM activities WHERE payload_json LIKE '%production_italy_test%' OR message LIKE '%production_italy_test%'")
    con.commit()
    con.close()
    for path in (BASE / 'exports').glob('*production_italy_test*'):
        path.unlink()


def main():
    init_app_db()
    cleanup()

    top = select_top_italy_leads(limit=5)
    assert top, 'expected top Italy leads'
    assert all(item['italy_fit_score'] >= 50 for item in top), top
    assert any('energy' in item['italy_wedge'].lower() or 'retrofit' in item['italy_wedge'].lower() for item in top), top

    sample = {
        'title': 'PNRR efficientamento energetico edifici ERP con impianti termici e riqualificazione energetica',
        'description': 'PPP finanza di progetto per public housing',
        'category': 'energy / HVAC / solar',
        'estimated_value': 12000000,
        'source_url': 'https://ted.europa.eu/en/notice/-/detail/test-production-italy',
        'deadline_date': '2099-12-31',
    }
    scored = score_italy_fit(sample)
    assert scored['italy_fit_score'] >= 80, scored
    assert scored['recommended_offer'], scored

    dossier = build_tender_dossier(top[0]['source_notice_id'], source='production_italy_test', save=True)
    assert dossier['notice_id'] == top[0]['source_notice_id'], dossier
    assert dossier['italy_fit_score'] >= 50, dossier
    assert dossier['export_path'].endswith('.md'), dossier
    assert 'Official document collection checklist' in dossier['markdown'], dossier['markdown'][:1000]
    assert dossier['official_links'], dossier
    assert 'Official source links' in dossier['markdown'], dossier['markdown'][:1000]
    assert 'Procurement portal search links' in dossier['markdown'], dossier['markdown'][:1000]

    summary = italy_summary()
    assert summary['qualified_italy_leads'] >= 20, summary
    assert summary['italy_workers'] >= 200, summary
    assert summary['italy_profiles'] >= 3, summary

    workers = query_workers({'country': ['Italy'], 'limit': ['1']})['items']
    assert workers, 'expected Italy workers'
    worker_id = workers[0]['id']
    updated = verify_worker({'worker_id': worker_id, 'verification_status': 'qualified_test', 'notes': 'production_italy_test verification note', 'capabilities': 'HVAC, retrofit'})
    assert updated['verification_status'] == 'qualified_test', updated
    filtered = query_workers({'country': ['Italy'], 'verification_status': ['qualified_test'], 'limit': ['10']})
    assert any(w['id'] == worker_id for w in filtered['items']), filtered
    exported = export_workers({'country': ['Italy'], 'verification_status': ['qualified_test'], 'limit': ['10'], 'format': ['csv']})
    assert exported['item_count'] >= 1 and exported['export_path'].endswith('.csv'), exported
    Path(exported['export_path']).unlink(missing_ok=True)

    cleanup()
    print('PASS production Italy workflows: scoring, dossiers, summary, worker verification')


if __name__ == '__main__':
    main()
