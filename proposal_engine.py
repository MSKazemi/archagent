#!/usr/bin/env python3
"""ArchAgent proposal and monetization helpers.

This module is intentionally dependency-free so the product can run anywhere.
It produces useful first-draft bid packages from real tender data and can be
replaced later by a Hermes/LLM workflow for deeper reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

COUNTRY_NAMES = {
    'DEU': 'Germany', 'FRA': 'France', 'CHE': 'Switzerland', 'IRL': 'Ireland',
    'BEL': 'Belgium', 'POL': 'Poland', 'NOR': 'Norway', 'SWE': 'Sweden',
    'LUX': 'Luxembourg', 'FIN': 'Finland', 'AUT': 'Austria', 'NLD': 'Netherlands',
    'ESP': 'Spain', 'ITA': 'Italy', 'DNK': 'Denmark', 'CZE': 'Czechia',
    'PRT': 'Portugal', 'EST': 'Estonia', 'LTU': 'Lithuania', 'LVA': 'Latvia',
}

TRADE_RULES = {
    'painting': ['Painting contractor', 'Surface preparation crew', 'Site inspector'],
    'finishing': ['Painting contractor', 'Interior finishing crew', 'Quantity surveyor'],
    'insulation': ['Facade/insulation installer', 'Energy auditor', 'Scaffolding contractor', 'Site inspector'],
    'facade': ['Facade contractor', 'Architect', 'Structural engineer', 'Scaffolding contractor'],
    'envelope': ['Building envelope specialist', 'Energy auditor', 'Window installer'],
    'energy': ['Energy auditor', 'HVAC contractor', 'Controls specialist', 'Commissioning engineer'],
    'hvac': ['HVAC contractor', 'Mechanical engineer', 'Controls specialist', 'Commissioning engineer'],
    'solar': ['Solar installer', 'Electrical contractor', 'Roofing contractor'],
    'architecture': ['Architect', 'Planning consultant', 'Cost estimator', 'MEP engineer'],
    'design': ['Architect', 'Interior designer', 'Cost estimator'],
    'construction': ['General contractor', 'Site manager', 'Quantity surveyor', 'Safety coordinator'],
    'civil': ['Civil works contractor', 'Site manager', 'Surveyor'],
    'renovation': ['General contractor', 'Architect', 'Demolition/prep crew', 'Finishing crew'],
    'rehabilitation': ['Renovation contractor', 'Architect', 'Structural engineer', 'Site inspector'],
    'roof': ['Roofing contractor', 'Waterproofing specialist', 'Scaffolding contractor'],
}

PACKAGE_PRICES = {
    'lead-radar': '€149/month starter, €299/month for country/category monitoring',
    'bid-package': '€750–€2,000 per opportunity depending on complexity',
    'managed-match': '5–10% success or management fee on awarded work',
    'enterprise': 'Custom monthly retainer for portfolios and contractors',
}


def clean(value) -> str:
    if value is None:
        return ''
    return ' '.join(str(value).replace('\n', ' ').replace('\r', ' ').split())


def country_label(code: str) -> str:
    return COUNTRY_NAMES.get(clean(code), clean(code) or 'Europe')


def value_label(value, currency: str = '') -> str:
    try:
        if value is None or value == '':
            return 'Not disclosed'
        return f"{float(value):,.0f} {currency or ''}".strip()
    except Exception:
        return clean(value) or 'Not disclosed'


def days_until(deadline: str) -> Optional[int]:
    try:
        return (datetime.strptime(deadline[:10], '%Y-%m-%d').date() - date.today()).days
    except Exception:
        return None


def infer_trades(lead: Dict) -> List[str]:
    text = ' '.join(clean(lead.get(k)) for k in ('title', 'category', 'description', 'cpv_codes')).lower()
    trades: List[str] = []
    for keyword, roles in TRADE_RULES.items():
        if keyword in text:
            for role in roles:
                if role not in trades:
                    trades.append(role)
    if not trades:
        trades = ['Project manager', 'Estimator', 'Qualified local contractor', 'Site inspector']
    return trades[:8]


def infer_risks(lead: Dict) -> List[str]:
    risks = []
    deadline = clean(lead.get('deadline_date'))
    remaining = days_until(deadline) if deadline else None
    if remaining is not None and remaining <= 7:
        risks.append('Very short deadline: confirm documents and eligibility immediately.')
    if not lead.get('estimated_value'):
        risks.append('Estimated value not disclosed: pricing assumptions must be checked from the official notice.')
    title = clean(lead.get('title')).lower()
    if any(w in title for w in ['school', 'hospital', 'ehpad', 'health', 'education']):
        risks.append('Public/occupied building: safety, phasing, access, and continuity requirements may be strict.')
    if any(w in title for w in ['roof', 'facade', 'fassade', 'étanchéité', 'waterproof']):
        risks.append('Envelope work: weather, scaffolding, waterproofing, and warranty details need review.')
    if not risks:
        risks.append('Check tender language, mandatory certificates, submission portal, and lot structure.')
    return risks


def short_title(title: str) -> str:
    title = clean(title)
    return title.split(' – ', 2)[-1] if ' – ' in title else title


def lead_to_public_dict(row: Dict) -> Dict:
    data = dict(row)
    data['country_label'] = country_label(data.get('performance_country') or data.get('country') or data.get('buyer_country'))
    data['short_title'] = short_title(data.get('title', 'Untitled opportunity'))
    data['value_label'] = value_label(data.get('estimated_value'), data.get('currency'))
    data['days_left'] = days_until(clean(data.get('deadline_date')))
    data['trades'] = infer_trades(data)
    data['risks'] = infer_risks(data)
    return data


def generate_proposal(lead: Dict, company_role: str, package_type: str, prospect: str = '') -> str:
    lead = lead_to_public_dict(lead)
    title = lead['short_title']
    city = clean(lead.get('performance_city'))
    location = lead['country_label'] + (f" / {city}" if city else '')
    trades = infer_trades(lead)
    risks = infer_risks(lead)
    deadline = clean(lead.get('deadline_date')) or 'Not listed'
    source = clean(lead.get('source_url'))
    buyer = clean(lead.get('buyer_name')) or 'Not listed'
    offer = PACKAGE_PRICES.get('bid-package')

    return f"""{package_type} — ArchAgent draft

Opportunity
- Title: {title}
- Buyer: {buyer}
- Location: {location}
- Deadline: {deadline}
- Estimated value: {lead['value_label']}
- Category: {clean(lead.get('category')) or 'Building work'}
- Source: {source}

Positioning
We are preparing this for: {company_role}.
{('Prospect/client: ' + prospect) if prospect else 'Prospect/client: not assigned yet.'}

Fit summary
This opportunity appears relevant for {', '.join(trades[:4])}. ArchAgent can turn the official notice into a bid-ready work package: scope summary, required trades, compliance checklist, partner quote requests, and proposal text.

Recommended delivery team
{chr(10).join('- ' + role for role in trades)}

Bid preparation checklist
- Open the official source notice and download all tender documents.
- Confirm eligibility, submission language, buyer portal, lot structure, and mandatory certificates.
- Extract measurable scope: quantities, surfaces, systems, site constraints, warranty, and inspection requirements.
- Build a compliance matrix with every requested document and deadline.
- Request partner/subcontractor quotes for specialist trades.
- Prepare commercial assumptions, exclusions, timeline, safety plan, and quality controls.
- Review with a human expert before submission.

Risks to check
{chr(10).join('- ' + risk for risk in risks)}

ArchAgent commercial offer
- Lead Radar: {PACKAGE_PRICES['lead-radar']}
- Bid Package: {offer}
- Managed Match: {PACKAGE_PRICES['managed-match']}

Suggested outreach email
Subject: Active {clean(lead.get('category')) or 'building'} opportunity — {location}

Hi,

We found an active building opportunity that appears relevant to your work: {title}. The tender deadline is {deadline}. We can review the official documents, extract the scope, identify required trades, prepare a bid/proposal checklist, and help match delivery partners where needed.

If useful, we can prepare a first bid package for this opportunity within 24–48 hours.

Best,
ArchAgent
""".strip()


def generate_compliance_matrix(lead: Dict) -> Dict:
    """Create a practical first-pass compliance matrix for a tender lead.

    This is deliberately conservative: it identifies common submission workstreams
    but does not claim the official tender documents have been reviewed.
    """
    lead = lead_to_public_dict(lead)
    deadline = clean(lead.get('deadline_date')) or 'Not listed'
    source = clean(lead.get('source_url')) or 'Official notice required'
    trades = infer_trades(lead)
    risks = infer_risks(lead)
    category = clean(lead.get('category')) or 'building works'
    buyer = clean(lead.get('buyer_name')) or 'Contracting authority'
    location = lead['country_label'] + (f" / {clean(lead.get('performance_city'))}" if clean(lead.get('performance_city')) else '')

    base_items = [
        ('Official notice and tender documents', 'Bid manager', 'Download full tender pack, appendices, forms, drawings, and addenda.', 'Critical', 'Not started'),
        ('Eligibility and exclusion grounds', 'Commercial lead', 'Company registration, tax/social declarations, exclusion/self-declaration forms.', 'Critical', 'Not started'),
        ('Technical capability evidence', 'Operations lead', 'Relevant references, CVs, equipment, method statements, quality/safety evidence.', 'High', 'Not started'),
        ('Scope and quantity extraction', 'Estimator', 'Measured scope from BOQ/specifications/drawings; assumptions and exclusions log.', 'Critical', 'Not started'),
        ('Pricing and commercial schedule', 'Estimator', 'Cost build-up, subcontractor quotes, overheads, risk allowance, final price schedule.', 'Critical', 'Not started'),
        ('Programme and site methodology', 'Project manager', 'Delivery timeline, phasing, access, occupied-building constraints, mobilisation plan.', 'High', 'Not started'),
        ('Health, safety, and environmental plan', 'HSE lead', 'Risk assessments, waste handling, site controls, permits, environmental requirements.', 'High', 'Not started'),
        ('Submission portal and format check', 'Bid manager', 'Portal account, file naming, signatures, language, deadline, upload confirmation.', 'Critical', 'Not started'),
        ('Human expert review', 'Director / reviewer', 'Final commercial, legal, and technical review before submission.', 'Critical', 'Not started'),
    ]
    for trade in trades[:6]:
        base_items.append((f'{trade} work package', 'Trade lead', f'Confirm scope, qualification evidence, quote request, availability, and interface risks for {trade}.', 'Medium', 'Not started'))

    items = []
    for idx, (requirement, owner, evidence, priority, status) in enumerate(base_items, 1):
        items.append({
            'id': idx,
            'requirement': requirement,
            'owner': owner,
            'evidence_needed': evidence,
            'priority': priority,
            'status': status,
        })
    return {
        'title': lead['short_title'],
        'buyer': buyer,
        'category': category,
        'location': location,
        'deadline': deadline,
        'source_url': source,
        'disclaimer': 'First-pass ArchAgent matrix. Must be checked against the official tender documents before submission.',
        'risks': risks,
        'items': items,
    }


def compliance_matrix_markdown(matrix: Dict) -> str:
    lines = [
        f"# Compliance Matrix — {clean(matrix.get('title'))}",
        '',
        f"Buyer: {clean(matrix.get('buyer'))}",
        f"Category: {clean(matrix.get('category'))}",
        f"Location: {clean(matrix.get('location'))}",
        f"Deadline: {clean(matrix.get('deadline'))}",
        f"Source: {clean(matrix.get('source_url'))}",
        '',
        f"Note: {clean(matrix.get('disclaimer'))}",
        '',
        '## Requirements',
        '',
        '| # | Requirement | Owner | Evidence needed | Priority | Status |',
        '|---|---|---|---|---|---|',
    ]
    for item in matrix.get('items', []):
        lines.append(f"| {item['id']} | {clean(item['requirement'])} | {clean(item['owner'])} | {clean(item['evidence_needed'])} | {clean(item['priority'])} | {clean(item['status'])} |")
    lines += ['', '## Risks to check', '']
    lines += [f"- {clean(r)}" for r in matrix.get('risks', [])]
    return '\n'.join(lines).strip() + '\n'


def generate_outreach_pack(lead: Dict, matches: List[Dict]) -> Dict:
    lead = lead_to_public_dict(lead)
    title = lead['short_title']
    location = lead['country_label'] + (f" / {clean(lead.get('performance_city'))}" if clean(lead.get('performance_city')) else '')
    deadline = clean(lead.get('deadline_date')) or 'not listed'
    category = clean(lead.get('category')) or 'building work'
    buyer = clean(lead.get('buyer_name')) or 'the contracting authority'
    top_matches = matches[:8]
    match_lines = [f"- {clean(m.get('name'))} ({clean(m.get('worker_type') or m.get('record_kind') or 'partner')}): {clean(m.get('trades'))}" for m in top_matches]
    contractor_email = f"""Subject: Possible {category} opportunity — {location}

Hi,

ArchAgent found an active public project that may fit your work:

{title}

Location: {location}
Buyer: {buyer}
Deadline: {deadline}

We are preparing a bid-readiness package: scope summary, compliance checklist, trade interfaces, and partner quote requests. Would you like us to check whether this opportunity is worth pursuing for your team?

If yes, please reply with:
- Relevant trade capacity
- Availability around the tender timeline
- Any geographic limits
- References/certifications you can share

Best,
ArchAgent
""".strip()
    buyer_email = f"""Subject: Clarification request — {title[:90]}

Hello,

We are reviewing the public notice for {title} and would like to confirm the current tender-document access, submission portal, lot structure, and any mandatory site visit or certification requirements.

Could you please confirm where bidders should download the full document package and whether any addenda have been issued?

Thank you,
ArchAgent
""".strip()
    call_script = f"""Quick call script

1. We found a live {category} opportunity in {location} with deadline {deadline}.
2. We can prepare a first bid package: official notice summary, compliance matrix, risk check, and partner quote requests.
3. We already identified these possible supply-side matches:
{chr(10).join(match_lines) if match_lines else '- No matches yet'}
4. Decision question: should we spend 24–48 hours turning this into a bid-ready package?
""".strip()
    return {'title': title, 'deadline': deadline, 'location': location, 'contractor_email': contractor_email, 'buyer_clarification_email': buyer_email, 'call_script': call_script, 'top_matches': top_matches}


def generate_building_audit(payload: Dict) -> str:
    btype = clean(payload.get('building_type') or 'Building')
    year = clean(payload.get('year') or 'unknown year')
    problem = clean(payload.get('problem') or 'not specified')
    climate = clean(payload.get('climate') or 'local climate')
    notes = clean(payload.get('notes') or '')
    return f"""Building Health Report — draft

Asset
- Type: {btype}
- Year: {year}
- Main problem: {problem}
- Climate: {climate}
- Notes: {notes or 'No extra notes'}

Likely findings
- Heat loss or comfort issue should be checked against envelope condition, windows, roof, and ventilation.
- Moisture/condensation risk should be reviewed before adding insulation.
- Energy systems and controls may need commissioning, balancing, or replacement.

Recommended next steps
- Collect photos, floor plans, utility bills, and material details.
- Perform site audit and thermal/moisture inspection.
- Prepare options: essential repair, balanced retrofit, premium energy upgrade.
- Match required experts and request comparable quotes.
- Create proposal with assumptions, exclusions, timeline, warranty, and inspection checklist.
""".strip()


def generate_crm_followup(prospect: Dict, lead: Optional[Dict] = None) -> Dict:
    """Create a practical sales follow-up pack for a saved prospect.

    The output is deterministic and human-review safe: it does not invent facts
    about the prospect, the project, or ArchAgent's delivery credentials.
    """
    name = clean(prospect.get('name') or prospect.get('company') or 'there')
    company = clean(prospect.get('company')) or name
    offer = clean(prospect.get('offer')) or 'Lead Radar'
    need = clean(prospect.get('need')) or 'finding relevant building opportunities and turning them into bid-ready packages'
    country = clean(prospect.get('country')) or 'your target market'
    value = value_label(prospect.get('value_estimate'), 'EUR') if prospect.get('value_estimate') not in (None, '') else 'Not estimated'

    lead_summary = 'No specific lead selected yet.'
    lead_subject = f'{offer} follow-up for {company}'
    if lead:
        lead = lead_to_public_dict(lead)
        location = lead['country_label'] + (f" / {clean(lead.get('performance_city'))}" if clean(lead.get('performance_city')) else '')
        lead_summary = f"Selected lead: {lead['short_title']} — {location}, deadline {clean(lead.get('deadline_date')) or 'not listed'}, value {lead['value_label']}. Source: {clean(lead.get('source_url'))}"
        lead_subject = f"Active {clean(lead.get('category')) or 'building'} opportunity for {company}"

    email = f"""Subject: {lead_subject}

Hi {name},

I wanted to follow up on {company}'s need around {need}.

{lead_summary}

ArchAgent can help with a lightweight first step: filtered live opportunities, a bid-readiness checklist, compliance matrix, and partner quote/outreach pack where useful. The first draft is prepared for human review, not as a final legal or commercial submission.

Would it be useful to schedule a 20-minute fit call this week and decide whether a €750+ bid package or a €149/month lead radar is the right starting point?

Best,
ArchAgent
""".strip()

    call_script = f"""CRM follow-up call script

1. Confirm target work: trade/category, country/region ({country}), contract size, and capacity.
2. Confirm current pain: {need}.
3. Present the offer: {offer}; estimated value in CRM: {value}.
4. If a lead is selected, open the source notice together and verify deadline, eligibility, lot structure, and required documents.
5. Close on one paid next step: Lead Radar subscription, fixed-price bid package, or contractor/worker matching package.
6. Log outcome: interested, nurture, not-fit, or won.
""".strip()

    tasks = [
        'Verify prospect trade, countries, bid capacity, and preferred contract size.',
        'Send follow-up email with one concrete next step and clear human-review disclaimer.',
        'If prospect is interested, generate a full project dossier for the most relevant live lead.',
        'Schedule a 20-minute fit call and request references/certificates needed for tenders.',
        'Update CRM status after the call: interested, nurture, not-fit, or won.',
    ]
    return {'subject': lead_subject, 'email': email, 'call_script': call_script, 'tasks': tasks, 'lead_summary': lead_summary, 'disclaimer': 'CRM follow-up draft. Verify prospect needs, consent, and all tender facts before sending.'}
