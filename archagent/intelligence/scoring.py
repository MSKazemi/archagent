"""Italy tender fit scoring — keyword-based scoring against HIGH_VALUE_TERMS."""
from __future__ import annotations

import datetime as dt
from typing import Any

HIGH_VALUE_TERMS = {
    'energy_retrofit': ['efficientamento', 'riqualificazione energetica', 'energetico', 'pnrr', 'm2c3', 'm7', 'esco', 'impianti termici', 'hvac', 'riscaldamento', 'condizionamento', 'pompa di calore', 'cappotto termico', 'fotovoltaico', 'superbonus', 'comunità energetica'],
    'public_housing_ppp': ['erp', 'edilizia residenziale pubblica', 'ater', 'iacp', 'case popolari', 'ppp', 'partenariato pubblico privato', 'finanza di progetto', 'concessione', 'social housing'],
    'restoration_safety': ['restauro', 'messa in sicurezza', 'copertura', 'tetto', 'facciata', 'isolamento', 'serramenti', 'edificio storico', 'consolidamento', 'antisismic', 'adeguamento sismico', 'vulnerabilità sismica'],
    'design_services': ['progettazione', 'direzione lavori', 'fattibilità', 'coordinamento sicurezza', 'servizi tecnici', 'engineering services', 'technical services', 'progettazione esecutiva', 'pfte'],
    'school_healthcare': ['edilizia scolastica', 'scuola', 'asilo', 'ospedale', 'presidio sanitario', 'rsa', 'mensa scolastica', 'palestra scolastica'],
}

NOISE_TERMS = [
    'insurance services', 'polizza', 'rifiuti', 'waste', 'prostheses', 'protesi', 'cochlear',
    'multimedia equipment', 'data-processing machines', 'chemicals', 'electric pumps',
]


def clean(value: Any) -> str:
    return ' '.join(str(value or '').replace('\n', ' ').split())


def lead_text(lead: dict) -> str:
    return ' '.join(clean(lead.get(k)) for k in ('title', 'description', 'category', 'buyer_name', 'cpv_codes')).lower()


def score_italy_fit(lead: dict) -> dict:
    text = lead_text(lead)
    score = int(lead.get('relevance_score') or 0)
    reasons: list[str] = []
    risks: list[str] = []
    wedges: list[str] = []

    for wedge, terms in HIGH_VALUE_TERMS.items():
        hits = [term for term in terms if term in text]
        if hits:
            add = min(30, 10 + len(hits) * 5)
            score += add
            wedges.append(wedge.replace('_', ' / '))
            reasons.append(f"{wedge.replace('_', ' ')} signals: {', '.join(hits[:5])}")

    if wedges:
        score += 15
        reasons.append('strong Italy-specific wedge match')

    # PNRR / NextGenerationEU funding is a strong positive signal (priority funding,
    # firm deadlines, premiality clauses) — boost and tag the lead.
    from archagent.intelligence.procurement import detect_pnrr
    pnrr = detect_pnrr(text)
    if pnrr['is_pnrr']:
        score += 10
        reasons.append('PNRR / NextGenerationEU funding signal: ' + ', '.join(pnrr['signals'][:4]))

    value = lead.get('estimated_value')
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0
    if value >= 10_000_000:
        score += 12; reasons.append('large disclosed value above €10M')
    elif value >= 1_000_000:
        score += 7; reasons.append('material disclosed value above €1M')
    else:
        risks.append('estimated value is missing or below €1M')

    deadline = clean(lead.get('deadline_date'))
    if deadline:
        try:
            days = (dt.date.fromisoformat(deadline[:10]) - dt.date.today()).days
            if days < 3:
                risks.append('deadline is extremely close')
            elif days <= 21:
                score += 5; reasons.append('deadline is near enough for urgency')
        except ValueError:
            risks.append('deadline could not be parsed')
    else:
        risks.append('deadline missing')

    noise_hits = [term for term in NOISE_TERMS if term in text]
    if noise_hits:
        score -= 35
        risks.append('possible non-building/noise scope: ' + ', '.join(noise_hits[:4]))

    if not clean(lead.get('source_url')):
        risks.append('source URL missing')

    score = max(0, min(100, score))
    wedge = wedges[0] if wedges else (clean(lead.get('category')) or 'general building opportunity')
    recommended_offer = 'Bid-readiness dossier + partner sourcing'
    if 'energy' in wedge or 'public housing' in wedge:
        recommended_offer = 'Italy energy-retrofit lead radar + bid-readiness package'
    elif 'restoration' in wedge:
        recommended_offer = 'Restoration/roof safety bid package + specialist partner sourcing'
    elif 'design' in wedge:
        recommended_offer = 'Architecture/technical-services tender briefing'

    return {
        'italy_fit_score': score,
        'italy_wedge': wedge,
        'fit_reasons': reasons or ['matches Italy public building opportunity filters'],
        'risks': risks,
        'recommended_offer': recommended_offer,
        'pnrr': pnrr['is_pnrr'],
        'pnrr_signals': pnrr['signals'],
    }


def load_italy_leads(leads_db) -> list[dict]:
    import sqlite3
    con = sqlite3.connect(leads_db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        """
        SELECT * FROM project_leads
        WHERE performance_country='ITA' OR buyer_country='ITA' OR title LIKE 'Italy –%'
        ORDER BY relevance_score DESC, deadline_date ASC, publication_date DESC
        """
    ).fetchall()]
    con.close()
    return rows


def select_top_italy_leads(limit: int = 10, min_score: int = 50) -> list[dict]:
    from archagent.core.config import LEADS_DB
    enriched = []
    for lead in load_italy_leads(LEADS_DB):
        scored = score_italy_fit(lead)
        item = {**lead, **scored}
        if item['italy_fit_score'] >= min_score:
            enriched.append(item)
    enriched.sort(key=lambda x: (x['italy_fit_score'], x.get('estimated_value') or 0), reverse=True)
    return enriched[:limit]
