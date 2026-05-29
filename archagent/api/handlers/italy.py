"""Handler functions for Italy market summary route."""
from __future__ import annotations

from archagent.core.db import app_conn
from archagent.core.config import BASE
from archagent.intelligence.scoring import select_top_italy_leads


def italy_summary() -> dict:
    all_qualified = select_top_italy_leads(limit=100, min_score=50)
    top = all_qualified[:10]
    con = app_conn()
    italy_workers = con.execute("SELECT COUNT(*) FROM expert_workers WHERE country='Italy'").fetchone()[0]
    italy_profiles = con.execute("SELECT COUNT(*) FROM customer_profiles WHERE countries LIKE '%ITA%'").fetchone()[0]
    verified_workers = con.execute("SELECT COUNT(*) FROM expert_workers WHERE country='Italy' AND verification_status NOT IN ('public_listing_unverified','')").fetchone()[0]
    dossiers = con.execute('SELECT COUNT(*) FROM tender_dossiers').fetchone()[0]
    con.close()
    visible_value = sum(float(l.get('estimated_value') or 0) for l in top if (l.get('currency') or '') == 'EUR')
    return {
        'qualified_italy_leads': len(all_qualified),
        'top_leads': top,
        'visible_top10_eur': visible_value,
        'italy_workers': italy_workers,
        'verified_italy_workers': verified_workers,
        'italy_profiles': italy_profiles,
        'tender_dossiers': dossiers,
        'report_path': str(BASE / 'ITALY_MARKET_REPORT.md'),
    }
