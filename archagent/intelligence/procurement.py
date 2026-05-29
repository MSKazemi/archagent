"""
procurement.py — Italian public-procurement domain intelligence (BuildingOS Phase 2).

Pure Python standard library. Encodes the operational rules of Italian public works
procurement under D.Lgs. 36/2023 (Codice dei Contratti Pubblici), grounded in primary
research (see .claude/plans/research/). Every numeric constant below is sourced:

- SOA categories OG1–OG13, OS1–OS35           — Allegato II.12 D.Lgs 36/2023
- SOA classification value bands               — Art. 100 / Allegato II.12
- CIG: 10-char alphanumeric, required > €40K    — ANAC, Art. 19 D.Lgs 36/2023
- Cauzione provvisoria 2%, ISO reductions       — Art. 106 co. 2 / co. 8
- Cauzione definitiva 10% + ribasso surcharge   — Art. 117
- DURC validity 120 days                        — Art. 15 D.M. 30/01/2015

This module is deterministic and side-effect free: no I/O, no LLM, no network.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# SOA categories — Allegato II.12 D.Lgs 36/2023                               #
# --------------------------------------------------------------------------- #
# OG = Opere Generali (general works), OS = Opere Specializzate (specialized).
SOA_CATEGORIES: dict[str, str] = {
    'OG1': 'Edifici civili e industriali — residential, commercial, office, industrial buildings',
    'OG2': 'Restauro e manutenzione di beni immobili tutelati — heritage / listed structures',
    'OG3': 'Strade, autostrade, ponti, ferrovie — roads, highways, bridges, railways',
    'OG4': "Opere d'arte nel sottosuolo — tunnels and underground works",
    'OG5': 'Dighe — dams and hydraulic retention',
    'OG6': 'Acquedotti, gasdotti, oleodotti, irrigazione — pipelines, aqueducts, irrigation',
    'OG7': 'Opere marittime e dragaggio — marine works, dredging, ports',
    'OG8': 'Opere fluviali e di difesa idraulica — river works, flood defense, reclamation',
    'OG9': 'Impianti di produzione di energia elettrica — power generation plants',
    'OG10': 'Impianti di trasformazione e distribuzione energia — HV/MV transformation, distribution',
    'OG11': 'Impianti tecnologici — building systems: HVAC, fire, security, IT infrastructure',
    'OG12': 'Opere di bonifica e protezione ambientale — environmental remediation',
    'OG13': 'Opere di ingegneria naturalistica — bioengineering, slope stabilization',
    'OS1': 'Lavori in terra — earthworks, excavation, soil improvement',
    'OS2-A': 'Superfici decorate di beni culturali — frescoes, mosaics conservation',
    'OS2-B': 'Beni culturali mobili — moveable cultural heritage items',
    'OS3': 'Impianti idrico-sanitari, cucine, lavanderie — plumbing, sanitary, kitchens',
    'OS4': 'Impianti elettromeccanici trasportatori — elevators, escalators',
    'OS5': 'Impianti pneumatici e antintrusione — pneumatic conveyors, anti-intrusion',
    'OS6': 'Finiture in legno, plastica, metallo, vetro — woodwork, metalwork, glazing finishes',
    'OS7': 'Finiture edili — flooring, tiling, plastering, painting',
    'OS8': 'Finiture tecniche — technical building finishes, suspended ceilings',
    'OS9': 'Segnaletica luminosa e sicurezza traffico — traffic signal systems',
    'OS10': 'Segnaletica stradale non luminosa — non-illuminated road signage',
    'OS11': 'Apparecchiature strutturali speciali — seismic isolators, special structural equipment',
    'OS12-A': 'Barriere stradali di sicurezza — guardrails, road safety barriers',
    'OS12-B': 'Barriere paramassi e fermaneve — rockfall barriers, snow fences',
    'OS13': 'Strutture prefabbricate in cemento armato — prefabricated concrete structures',
    'OS14': 'Impianti di smaltimento e recupero rifiuti — waste processing and recovery',
    'OS15': 'Pulizia di acque — water cleaning, aquatic decontamination',
    'OS16': 'Impianti per centrali di produzione energia — power plant components',
    'OS17': 'Linee e impianti di telefonia — telephony installations',
    'OS18-A': 'Componenti strutturali in acciaio — structural steel components',
    'OS18-B': 'Componenti per facciate continue — curtain wall / glazed facade systems',
    'OS19': 'Reti di telecomunicazione e trattamento dati — data networks, telecom',
    'OS20-A': 'Rilevamenti topografici — topographic surveys',
    'OS20-B': 'Indagini geognostiche — geotechnical investigations',
    'OS21': 'Opere strutturali speciali — post-tensioning, soil nailing, geotextiles',
    'OS22': 'Impianti di potabilizzazione e depurazione — water treatment / purification',
    'OS23': 'Demolizioni — demolition works',
    'OS24': 'Verde e arredo urbano — landscaping, green infrastructure, urban furniture',
    'OS25': 'Scavi archeologici — archaeological excavations',
    'OS26': 'Pavimentazioni e sovrastrutture speciali — specialized paving',
    'OS27': 'Impianti per la trazione elettrica — electric traction (rail, tram)',
    'OS28': 'Impianti termici e di condizionamento — heating / HVAC',
    'OS29': 'Armamento ferroviario — railway track systems',
    'OS30': 'Impianti interni elettrici e di comunicazione — internal electrical / TV / telephone',
    'OS31': 'Impianti per la mobilità sostenibile — EV charging, sustainable mobility (new, def. pending)',
    'OS32': 'Strutture in legno — timber structures',
    'OS33': 'Coperture speciali — green roofs, specialized waterproofing',
    'OS34': "Sistemi antirumore per infrastrutture — noise barriers for roads / rail",
    'OS35': 'Interventi a basso impatto ambientale — low-impact / eco-construction (new, def. pending)',
}

# --------------------------------------------------------------------------- #
# SOA classifications — value capacity bands (Art. 100 / Allegato II.12)       #
# --------------------------------------------------------------------------- #
# Ascending order. Cap is the maximum single-contract value the classifica covers.
SOA_CLASSIFICATIONS: list[tuple[str, float]] = [
    ('I', 258_000.0),
    ('II', 516_000.0),
    ('III', 1_033_000.0),
    ('III-bis', 1_500_000.0),
    ('IV', 2_582_000.0),
    ('IV-bis', 3_500_000.0),
    ('V', 5_165_000.0),
    ('VI', 10_329_000.0),
    ('VII', 15_494_000.0),
    ('VIII', float('inf')),  # unlimited
]
_SOA_ORDER = [c for c, _ in SOA_CLASSIFICATIONS]

# Thresholds (euro) under D.Lgs 36/2023.
CIG_THRESHOLD = 40_000.0           # CIG required above this (Art. 19)
SOA_THRESHOLD = 150_000.0          # SOA attestation required for works above this (Art. 100)
EU_THRESHOLD_WORKS = 5_538_000.0   # EU-threshold for works (2024–2025 reg.); DGUE mandatory above

CAUZIONE_PROVVISORIA_PCT = 0.02    # 2% of base value (Art. 106 co. 2)
CAUZIONE_DEFINITIVA_PCT = 0.10     # 10% of base value (Art. 117)
DURC_VALIDITY_DAYS = 120           # Art. 15 D.M. 30/01/2015

# ISO certification reductions on cauzione provvisoria (Art. 106 co. 8).
# ISO 9001 halves the bond; others are additive up to a hard cap of 80%.
_CERT_REDUCTIONS: list[tuple[tuple[str, ...], float]] = [
    (('ISO 9001', 'ISO9001', 'UNI EN ISO 9001'), 0.50),
    (('ISO 14001', 'ISO14001', 'EMAS'), 0.20),
    (('SA8000', 'SA 8000'), 0.10),
    (('ISO 45001', 'ISO45001', 'OHSAS 18001'), 0.05),
]
_MAX_CAUZIONE_REDUCTION = 0.80

# PNRR / NextGenerationEU signal terms (session_B1_PNRR.md).
_PNRR_TERMS = [
    'pnrr', 'next generation eu', 'nextgenerationeu', 'next generationeu',
    'piano nazionale di ripresa e resilienza', 'recovery fund', 'react-eu',
    'superbonus', 'comunità energetica', 'comunita energetica', 'cer',
]
_PNRR_MISSIONS = {
    'm1': 'M1 — Digitalizzazione, innovazione, competitività, cultura',
    'm2': 'M2 — Rivoluzione verde e transizione ecologica',
    'm2c3': 'M2C3 — Efficienza energetica e riqualificazione edifici',
    'm3': 'M3 — Infrastrutture per una mobilità sostenibile',
    'm4': 'M4 — Istruzione e ricerca',
    'm5': 'M5 — Inclusione e coesione',
    'm6': 'M6 — Salute',
}

# CIG: 10 alphanumeric characters (ANAC). e.g. A1234B5678.
_CIG_RE = re.compile(r'\b([0-9A-Za-z]{10})\b')
_CIG_LABEL_RE = re.compile(r'C\.?I\.?G\.?[:\s]*([0-9A-Za-z]{10})', re.IGNORECASE)


# --------------------------------------------------------------------------- #
# SOA helpers                                                                  #
# --------------------------------------------------------------------------- #
def soa_category_catalog() -> list[dict[str, str]]:
    """Return the full SOA category catalog as a list of {code, type, description}."""
    return [
        {'code': code, 'type': 'OG' if code.startswith('OG') else 'OS', 'description': desc}
        for code, desc in SOA_CATEGORIES.items()
    ]


def soa_rank(classification: str) -> int:
    """Numeric rank of a SOA classification (0-based ascending). Unknown → -1."""
    try:
        return _SOA_ORDER.index((classification or '').strip())
    except ValueError:
        return -1


def is_valid_soa_category(code: str) -> bool:
    return (code or '').strip().upper().replace('OS2A', 'OS2-A') in SOA_CATEGORIES \
        or (code or '').strip().upper() in {k.upper() for k in SOA_CATEGORIES}


def infer_soa_class(value_eur: float) -> Optional[str]:
    """
    Minimum SOA classification capable of covering a contract of `value_eur`.

    Below SOA_THRESHOLD (€150K) no SOA attestation is required → returns None.
    Above the table, returns 'VIII' (unlimited).
    """
    try:
        value = float(value_eur or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value < SOA_THRESHOLD:
        return None
    for cls, cap in SOA_CLASSIFICATIONS:
        if value <= cap:
            return cls
    return 'VIII'


# --------------------------------------------------------------------------- #
# CIG                                                                          #
# --------------------------------------------------------------------------- #
def is_valid_cig(code: str) -> bool:
    """A CIG is exactly 10 alphanumeric characters and contains at least one digit."""
    if not code:
        return False
    code = code.strip()
    return bool(re.fullmatch(r'[0-9A-Za-z]{10}', code)) and any(c.isdigit() for c in code)


def extract_cig(text: str) -> Optional[str]:
    """
    Extract the tender's CIG from free text. Prefers a labelled 'CIG: XXXX' match,
    falling back to the first standalone 10-char alphanumeric token with a digit.
    Returns the uppercased CIG or None.
    """
    if not text:
        return None
    m = _CIG_LABEL_RE.search(text)
    if m and is_valid_cig(m.group(1)):
        return m.group(1).upper()
    for m in _CIG_RE.finditer(text):
        token = m.group(1)
        if is_valid_cig(token):
            return token.upper()
    return None


# --------------------------------------------------------------------------- #
# Cauzioni (guarantees)                                                        #
# --------------------------------------------------------------------------- #
def cauzione_provvisoria(base_value: float, certifications: Optional[list[str]] = None) -> dict:
    """
    Bid bond = 2% of base value (Art. 106 co. 2), reduced by held certifications
    (Art. 106 co. 8): ISO 9001 −50%, ISO 14001/EMAS −20%, SA8000 −10%, ISO 45001 −5%,
    additive, capped at −80%.
    """
    try:
        base = float(base_value or 0)
    except (TypeError, ValueError):
        base = 0.0
    standard = base * CAUZIONE_PROVVISORIA_PCT
    held = {c.strip().upper() for c in (certifications or [])}
    reduction = 0.0
    applied: list[str] = []
    for aliases, pct in _CERT_REDUCTIONS:
        if any(a.upper() in held or any(a.upper() in h for h in held) for a in aliases):
            reduction += pct
            applied.append(f'{aliases[0]} (−{int(pct * 100)}%)')
    reduction = min(reduction, _MAX_CAUZIONE_REDUCTION)
    amount = round(standard * (1 - reduction), 2)
    return {
        'standard_eur': round(standard, 2),
        'reduction_pct': round(reduction * 100, 1),
        'reductions_applied': applied,
        'amount_eur': amount,
        'note': 'Cauzione provvisoria 2% base d\'asta; riduzioni Art. 106 co. 8.',
    }


def cauzione_definitiva(base_value: float, ribasso_pct: float = 0.0) -> dict:
    """
    Performance bond = 10% of base value (Art. 117), surcharged for high discounts:
    +2 percentage points for each point of ribasso above 10% (10–20%); above 20% the
    formula continues but the total is capped at 30% of the base value.
    """
    try:
        base = float(base_value or 0)
        ribasso = float(ribasso_pct or 0)
    except (TypeError, ValueError):
        base, ribasso = 0.0, 0.0
    pct = 0.10
    surcharge_note = ''
    if ribasso > 10:
        extra_points = ribasso - 10
        pct = 0.10 + (extra_points * 0.02)
        surcharge_note = f'+{extra_points:.0f} punti di ribasso oltre 10% → +{extra_points * 2:.0f}%'
    pct = min(pct, 0.30)  # hard cap 30%
    amount = round(base * pct, 2)
    return {
        'percent': round(pct * 100, 1),
        'amount_eur': amount,
        'surcharge_note': surcharge_note,
        'note': 'Cauzione definitiva 10% base; maggiorazione per ribasso Art. 117.',
    }


# --------------------------------------------------------------------------- #
# Document waterfall + D.Lgs 36/2023 checklist                                 #
# --------------------------------------------------------------------------- #
def required_documents(extracted: Optional[dict] = None) -> list[dict]:
    """
    The bid document waterfall (session_A5). Each entry: phase, name, validity, note.
    `extracted` (analyst output) tunes a few conditional items (EU-threshold DGUE, SOA).
    """
    extracted = extracted or {}
    value = 0.0
    try:
        value = float(extracted.get('value_eur') or 0)
    except (TypeError, ValueError):
        value = 0.0

    docs: list[dict] = [
        {'phase': 'Qualifica', 'name': 'DGUE / ESPD', 'validity': 'per gara',
         'mandatory': value >= EU_THRESHOLD_WORKS or value == 0,
         'note': 'Documento di Gara Unico Europeo. Part III per ogni amministratore attuale + cessato (3 anni).'},
        {'phase': 'Qualifica', 'name': 'DURC', 'validity': f'{DURC_VALIDITY_DAYS} giorni',
         'mandatory': True,
         'note': 'Regolarità contributiva INPS/INAIL/Casse Edili. Esclusione sostanziale se irregolare (no soccorso).'},
        {'phase': 'Qualifica', 'name': 'PASSOE / FVOE', 'validity': 'per gara',
         'mandatory': True,
         'note': 'Fascicolo Virtuale dell\'Operatore Economico — verifica requisiti ANAC.'},
        {'phase': 'Qualifica', 'name': 'Attestazione SOA', 'validity': '5 anni (3+2 con verifica)',
         'mandatory': value >= SOA_THRESHOLD,
         'note': 'Obbligatoria per lavori > €150.000. Categoria + classifica adeguata.'},
        {'phase': 'Qualifica', 'name': 'Visura/Certificato CCIAA', 'validity': '6 mesi',
         'mandatory': True, 'note': 'Iscrizione Camera di Commercio.'},
        {'phase': 'Qualifica', 'name': 'Documentazione antimafia', 'validity': 'per gara',
         'mandatory': value >= 150_000.0,
         'note': 'Comunicazione/informazione antimafia (sopra soglia).'},
        {'phase': 'Garanzie', 'name': 'Cauzione provvisoria', 'validity': '180 giorni',
         'mandatory': True, 'note': 'Bid bond 2% base; fideiussione a prima richiesta, deve citare il CIG.'},
        {'phase': 'Garanzie', 'name': 'Cauzione definitiva', 'validity': "fino a fine lavori",
         'mandatory': True, 'note': 'Performance bond 10% base (all\'aggiudicazione).'},
        {'phase': 'Offerta', 'name': 'Offerta tecnica', 'validity': 'per gara',
         'mandatory': True, 'note': 'Relazione tecnica firmata digitalmente (QES).'},
        {'phase': 'Offerta', 'name': 'Offerta economica', 'validity': 'per gara',
         'mandatory': True, 'note': 'Ribasso e costi sicurezza/manodopera ex Art. 41 co. 14.'},
        {'phase': 'Economico-finanziaria', 'name': 'Bilanci', 'validity': 'ultimi 3 esercizi',
         'mandatory': value >= EU_THRESHOLD_WORKS,
         'note': 'Per capacità economico-finanziaria (DGUE Parte IV-B).'},
    ]
    return docs


def dlgs36_checklist(extracted: Optional[dict] = None) -> list[dict]:
    """
    D.Lgs 36/2023 bid-readiness checklist: actionable items with status hints
    derived from the extracted tender (CIG present, SOA inferred, deadline, etc.).
    """
    extracted = extracted or {}
    value = extracted.get('value_eur')
    inferred = infer_soa_class(value) if value else None
    cig = extracted.get('cig') or extract_cig(str(extracted.get('raw_text', '')))

    items = [
        {'item': 'CIG identificato', 'status': 'ok' if cig else 'todo',
         'detail': f'CIG {cig}' if cig else 'CIG non rilevato — recuperare da bando/ANAC (obbligatorio > €40K).'},
        {'item': 'Classifica SOA richiesta', 'status': 'info',
         'detail': f'Valore €{float(value):,.0f} → classifica minima {inferred}' if inferred
                   else 'Valore non disponibile o sotto soglia SOA (€150K).'},
        {'item': 'Firma digitale corretta (CAdES/PAdES)', 'status': 'todo',
         'detail': 'Verificare nel disciplinare il tipo di firma richiesto. Firma errata = esclusione.'},
        {'item': 'Soccorso istruttorio (Art. 101)', 'status': 'info',
         'detail': 'Sana solo difetti formali; DURC irregolare e dichiarazioni false NON sanabili.'},
        {'item': 'Costi manodopera e sicurezza (Art. 41 co. 14)', 'status': 'todo',
         'detail': 'Indicare separatamente in offerta economica, pena esclusione.'},
        {'item': 'Sopralluogo obbligatorio', 'status': 'todo',
         'detail': 'Verificare se il bando richiede attestato di sopralluogo entro la scadenza.'},
    ]
    return items


# --------------------------------------------------------------------------- #
# PNRR                                                                         #
# --------------------------------------------------------------------------- #
def detect_pnrr(text: str) -> dict:
    """Detect PNRR / NextGenerationEU funding signals and (best-effort) the mission."""
    low = (text or '').lower()
    signals = [t for t in _PNRR_TERMS if t in low]
    mission = None
    for key, label in _PNRR_MISSIONS.items():
        if re.search(r'\b' + re.escape(key) + r'\b', low):
            mission = label
            break
    return {
        'is_pnrr': bool(signals),
        'signals': signals,
        'mission': mission,
        'note': 'Fondi PNRR: obblighi DNSH, quote giovani/donne, tracciabilità rafforzata.'
        if signals else '',
    }


# --------------------------------------------------------------------------- #
# Deadline tracking                                                            #
# --------------------------------------------------------------------------- #
def deadline_status(deadline_iso: str, today_iso: Optional[str] = None) -> dict:
    """
    Days remaining until a submission deadline + urgency band.
    `today_iso` is injectable for testing (defaults to today via date.today()).
    """
    if not deadline_iso:
        return {'days_remaining': None, 'urgency': 'unknown', 'label': 'Scadenza non disponibile'}
    try:
        deadline = dt.date.fromisoformat(str(deadline_iso)[:10])
    except ValueError:
        return {'days_remaining': None, 'urgency': 'unknown', 'label': 'Scadenza non interpretabile'}
    today = dt.date.fromisoformat(today_iso[:10]) if today_iso else dt.date.today()
    days = (deadline - today).days
    if days < 0:
        urgency, label = 'expired', 'Scaduta'
    elif days <= 3:
        urgency, label = 'critical', 'Critica (≤ 3 giorni)'
    elif days <= 10:
        urgency, label = 'urgent', 'Urgente (≤ 10 giorni)'
    elif days <= 21:
        urgency, label = 'soon', 'Imminente (≤ 21 giorni)'
    else:
        urgency, label = 'comfortable', 'Tempo sufficiente'
    return {'days_remaining': days, 'urgency': urgency, 'label': label}


# --------------------------------------------------------------------------- #
# Go / No-Go decision                                                          #
# --------------------------------------------------------------------------- #
def go_no_go(readiness_score: int, deadline: Optional[dict] = None,
             value_match: bool = True) -> dict:
    """
    Combine compliance readiness, deadline urgency, and value fit into a
    bid recommendation: GO / CONDITIONAL GO / NO-GO with rationale.
    """
    deadline = deadline or {}
    urgency = deadline.get('urgency', 'unknown')
    rationale: list[str] = []
    score = int(readiness_score or 0)

    decision = 'GO'
    if score >= 80:
        rationale.append(f'Readiness alta ({score}/100).')
    elif score >= 55:
        decision = 'CONDITIONAL GO'
        rationale.append(f'Readiness media ({score}/100) — colmare i gap prima di impegnarsi.')
    else:
        decision = 'NO-GO'
        rationale.append(f'Readiness bassa ({score}/100) — requisiti chiave mancanti.')

    if urgency == 'expired':
        decision = 'NO-GO'
        rationale.append('Scadenza superata.')
    elif urgency == 'critical' and decision == 'CONDITIONAL GO':
        decision = 'NO-GO'
        rationale.append('Tempo insufficiente per colmare i gap (scadenza critica).')
    elif urgency in ('urgent', 'critical'):
        rationale.append('Attenzione: finestra di presentazione stretta.')

    if not value_match and decision == 'GO':
        decision = 'CONDITIONAL GO'
        rationale.append('Valore del contratto fuori dal range tipico — valutare capacità finanziaria.')

    return {'decision': decision, 'rationale': rationale, 'readiness_score': score}
