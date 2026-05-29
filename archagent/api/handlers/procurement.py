"""Handler functions for procurement-intelligence API routes (BuildingOS Phase 2)."""
from __future__ import annotations

from archagent.intelligence import procurement as _proc


def soa_categories() -> dict:
    """Return the full SOA category catalog (OG/OS) with descriptions."""
    catalog = _proc.soa_category_catalog()
    return {
        'items': catalog,
        'total': len(catalog),
        'classifications': [
            {'code': code, 'max_value_eur': (None if cap == float('inf') else cap)}
            for code, cap in _proc.SOA_CLASSIFICATIONS
        ],
        'note': 'Categorie SOA — Allegato II.12 D.Lgs 36/2023. OG = opere generali, OS = specializzate.',
    }


def analyze_text(payload: dict) -> dict:
    """
    Deterministic procurement analysis of free tender text + optional structured hints.

    Body: {text?, value_eur?, deadline_date?, certifications?[], ribasso_pct?}
    Returns CIG, inferred SOA class, cauzioni, PNRR signals, deadline urgency,
    document waterfall, D.Lgs 36/2023 checklist, and a go/no-go indication.
    """
    text = str(payload.get('text') or '')
    try:
        value = float(payload.get('value_eur') or 0)
    except (TypeError, ValueError):
        value = 0.0
    certs = payload.get('certifications') or []
    if not isinstance(certs, list):
        certs = [str(certs)]
    try:
        ribasso = float(payload.get('ribasso_pct') or 0)
    except (TypeError, ValueError):
        ribasso = 0.0
    deadline = str(payload.get('deadline_date') or payload.get('deadline') or '')

    cig = _proc.extract_cig(text)
    inferred = _proc.infer_soa_class(value) if value else None
    dl = _proc.deadline_status(deadline)
    pnrr = _proc.detect_pnrr(text)
    extracted = {'value_eur': value, 'cig': cig, 'raw_text': text}

    # A light readiness proxy when no bid profile is supplied: CIG known + SOA inferable
    # + comfortable deadline → higher readiness. This is informational only.
    readiness = 50
    if cig:
        readiness += 15
    if inferred:
        readiness += 10
    if dl['urgency'] in ('comfortable', 'soon'):
        readiness += 15
    elif dl['urgency'] == 'expired':
        readiness -= 40
    readiness = max(0, min(100, readiness))

    return {
        'cig': cig,
        'value_eur': value or None,
        'inferred_soa_class': inferred,
        'cauzione_provvisoria': _proc.cauzione_provvisoria(value, certs),
        'cauzione_definitiva': _proc.cauzione_definitiva(value, ribasso),
        'pnrr': pnrr,
        'deadline_status': dl,
        'document_waterfall': _proc.required_documents(extracted),
        'dlgs36_checklist': _proc.dlgs36_checklist(extracted),
        'go_no_go': _proc.go_no_go(readiness, dl, value_match=True),
        'disclaimer': 'Stime automatiche e deterministiche. Confermare sempre dal disciplinare ufficiale.',
    }
