"""Italy tender dossier builder — generates bid-readiness markdown dossiers."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from archagent.core.config import APP_DB, EXPORT_DIR, LEADS_DB
from archagent.intelligence import procurement
from archagent.intelligence.scoring import clean, score_italy_fit


def procurement_intelligence(lead: dict) -> dict:
    """Derive D.Lgs 36/2023 procurement intelligence from a lead row."""
    blob = ' '.join(clean(lead.get(k)) for k in
                    ('title', 'description', 'category', 'source_notice_id', 'buyer_name'))
    value = lead.get('estimated_value') or lead.get('value_eur') or 0
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    return {
        'cig': procurement.extract_cig(blob),
        'inferred_soa_class': procurement.infer_soa_class(value),
        'pnrr': procurement.detect_pnrr(blob),
        'deadline_status': procurement.deadline_status(clean(lead.get('deadline_date'))),
        'cauzione_provvisoria': procurement.cauzione_provvisoria(value),
        'cauzione_definitiva': procurement.cauzione_definitiva(value),
        'document_waterfall': procurement.required_documents({'value_eur': value}),
    }


def procurement_section_markdown(pi: dict) -> str:
    """Render the procurement-intelligence section of a dossier."""
    cig = pi['cig'] or '— (recuperare dal bando; obbligatorio > €40K, Art. 19 D.Lgs 36/2023)'
    soa = pi['inferred_soa_class'] or 'sotto soglia SOA (€150K) o valore non disponibile'
    dl = pi['deadline_status']
    dl_line = (f"{dl['days_remaining']} giorni — {dl['label']}"
               if dl['days_remaining'] is not None else dl['label'])
    pnrr_line = (f"Sì — segnali: {', '.join(pi['pnrr']['signals'][:5])}"
                 + (f" · {pi['pnrr']['mission']}" if pi['pnrr']['mission'] else '')
                 if pi['pnrr']['is_pnrr'] else 'Nessun segnale PNRR rilevato')
    cp, cd = pi['cauzione_provvisoria'], pi['cauzione_definitiva']
    docs = '\n'.join(
        f"- **{d['name']}** ({d['phase']}, validità {d['validity']}) — "
        f"{'obbligatorio' if d['mandatory'] else 'eventuale'}: {d['note']}"
        for d in pi['document_waterfall'])
    return f"""## Procurement intelligence (D.Lgs 36/2023)

- **CIG**: {cig}
- **Classifica SOA minima stimata** (dal valore): {soa}
- **Scadenza**: {dl_line}
- **Finanziamento PNRR**: {pnrr_line}
- **Cauzione provvisoria** (2% base): € {cp['amount_eur']:,.0f} (standard € {cp['standard_eur']:,.0f})
- **Cauzione definitiva** (10% base): € {cd['amount_eur']:,.0f}

### Document waterfall (qualifica → garanzie → offerta)
{docs}

> Stime automatiche dal valore a base d'asta. Categoria SOA, importi cauzioni e tipo di firma
> digitale (CAdES/PAdES) vanno confermati dal disciplinare ufficiale."""


def extract_official_links(lead: dict) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    raw_text = lead.get('raw_json') or '{}'
    try:
        raw = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        if not isinstance(raw, dict):
            raw = {}
    except json.JSONDecodeError:
        raw = {}
    raw_links = raw.get('links') or {}
    if not isinstance(raw_links, dict):
        raw_links = {}
    preferred = [
        ('html', 'ITA', 'TED notice — Italian'),
        ('html', 'ENG', 'TED notice — English'),
        ('htmlDirect', 'ITA', 'TED direct HTML — Italian'),
        ('pdf', 'ITA', 'TED PDF — Italian'),
        ('xml', 'MUL', 'TED XML notice'),
    ]
    for family, lang, label in preferred:
        family_links = raw_links.get(family) or {}
        if not isinstance(family_links, dict):
            continue
        url = family_links.get(lang)
        if url:
            links.append({'label': label, 'url': url})
    source_url = clean(lead.get('source_url'))
    if source_url and all(item['url'] != source_url for item in links):
        links.insert(0, {'label': 'Source URL', 'url': source_url})
    return links


def procurement_search_links(lead: dict) -> list[dict[str, str]]:
    notice = clean(lead.get('source_notice_id'))
    buyer = clean(lead.get('buyer_name'))
    query = '+'.join(x for x in [notice, buyer.replace(' ', '+')] if x)
    return [
        {'label': 'TED search', 'url': f'https://ted.europa.eu/en/search/result?query={notice}'},
        {'label': 'Google procurement search', 'url': f'https://www.google.com/search?q={query}+bandi+gara+capitolato'},
        {'label': 'ANAC transparency search', 'url': 'https://www.anticorruzione.it/-/bandi-di-gara-e-contratti'},
    ]


def markdown_links(items: list[dict[str, str]]) -> str:
    return '\n'.join(f"- [{clean(item.get('label'))}]({clean(item.get('url'))})" for item in items if item.get('url')) or '- No official links extracted yet.'


def document_collection_checklist(lead: dict) -> list[str]:
    notice = clean(lead.get('source_notice_id'))
    buyer = clean(lead.get('buyer_name')) or 'buyer/procurement portal'
    return [
        f'Open TED notice {notice} and record all \'Documents\' / \'Buyer profile\' / eSender links.',
        f'Search the official procurement portal for notice ID {notice} and buyer name \'{buyer}\'.',
        'Download disciplinare di gara, capitolato, schema di contratto, computo metrico, elaborati tecnici, DGUE/forms, and clarification documents where available.',
        'Extract eligibility requirements: SOA categories, turnover, certifications, insurance, professional registration, language/submission rules.',
        'Extract evaluation method, award criteria, inspection/site-visit requirements, and mandatory annexes.',
        'Confirm submission portal, digital-signature requirements, guarantee/bond requirements, and final deadline/timezone.',
    ]


def build_tender_dossier(notice_id: str, *, source: str = 'api', save: bool = True) -> dict:
    con = sqlite3.connect(LEADS_DB)
    con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM project_leads WHERE source_notice_id=?', (notice_id,)).fetchone()
    con.close()
    if not row:
        raise ValueError(f'lead not found: {notice_id}')
    lead = dict(row)
    scored = score_italy_fit(lead)
    checklist = document_collection_checklist(lead)
    generated = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    source_url = clean(lead.get('source_url'))
    title = clean(lead.get('title'))
    official_links = extract_official_links(lead)
    search_links = procurement_search_links(lead)
    proc_intel = procurement_intelligence(lead)
    markdown = f"""# Italy Tender Bid-Readiness Dossier — {notice_id}

Generated: {generated}
Source: {source}

## Lead snapshot

- Notice ID: {notice_id}
- Title: {title}
- Buyer: {clean(lead.get('buyer_name')) or 'Not listed'}
- Location: {clean(lead.get('performance_city')) or 'Not specified'}, Italy
- Deadline: {clean(lead.get('deadline_date')) or 'Not listed'}
- Estimated value: {clean(lead.get('estimated_value')) or 'Not disclosed'} {clean(lead.get('currency'))}
- Category: {clean(lead.get('category'))}
- TED/source URL: {source_url}

## Official source links

{markdown_links(official_links)}

## Procurement portal search links

{markdown_links(search_links)}

## Italy fit score

- Score: {scored['italy_fit_score']}/100
- Wedge: {scored['italy_wedge']}
- Recommended offer: {scored['recommended_offer']}

### Fit reasons
{chr(10).join('- ' + r for r in scored['fit_reasons'])}

### Risks / checks
{chr(10).join('- ' + r for r in (scored['risks'] or ['No automatic risk flags; still requires human review.']))}

{procurement_section_markdown(proc_intel)}

## Official document collection checklist
{chr(10).join(f'{idx}. {item}' for idx, item in enumerate(checklist, 1))}

## Preliminary scope summary

{clean(lead.get('description'))[:2500] or title}

## Next action

1. Assign a human operator to collect the official procurement files.
2. Build a source-cited compliance matrix from the downloaded documents.
3. Match verified Italy partners only after confirming SOA/certification/region fit.
4. Offer the customer a paid bid-readiness package if eligibility and deadline are viable.

Human-review warning: this dossier is generated from public notice-level data. Do not submit a bid or contact a buyer/partner until the official tender documents are downloaded and checked.
"""
    result = {'notice_id': notice_id, 'lead': lead, **scored, 'official_links': official_links, 'search_links': search_links, 'checklist': checklist, 'procurement_intelligence': proc_intel, 'markdown': markdown}
    if save:
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_source = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in source)[:40]
        path = EXPORT_DIR / f"tender_dossier_{notice_id}_{safe_source}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(markdown, encoding='utf-8')
        result['export_path'] = str(path)
        result['url'] = '/exports/' + path.name
        app = sqlite3.connect(APP_DB)
        app.execute(
            'INSERT INTO tender_dossiers(source_notice_id,source,italy_fit_score,italy_wedge,recommended_offer,risks_json,export_path,created_at) VALUES (?,?,?,?,?,?,?,?)',
            (notice_id, source, scored['italy_fit_score'], scored['italy_wedge'], scored['recommended_offer'], str(scored['risks']), str(path), generated),
        )
        app.execute(
            'INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)',
            ('tender_dossier', f'Generated Italy tender dossier for {notice_id}', f'{{"notice_id":"{notice_id}","source":"{source}"}}', generated),
        )
        app.commit()
        app.close()
    return result
