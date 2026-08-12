#!/usr/bin/env python3
"""
compliance_agent.py — BuildingOS Phase 1: rule-based bid compliance matching.

Compares analyst.py extraction output against a company's bid_profile
to produce a bid readiness score and gap report.

SOA classification ordering (D.Lgs. 36/2023, Codice dei Contratti Pubblici):
  I ≤ €150K | II ≤ €300K | III ≤ €516K | III-bis ≤ €1.03M | IV ≤ €1.55M
  IV-bis ≤ €2.58M | V ≤ €5.17M | VI ≤ €10.33M | VII ≤ €15.49M | VIII = unlimited
"""
from __future__ import annotations

from typing import Any

from archagent.intelligence import procurement

# Ordered list of SOA classification levels (ascending value capacity)
_SOA_ORDER = [
    'I', 'II', 'III', 'III-bis', 'IV', 'IV-bis', 'V', 'VI', 'VII', 'VIII',
]


def _soa_rank(classification: str) -> int:
    """Return numeric rank for a SOA classification. Unknown → -1."""
    try:
        return _SOA_ORDER.index(classification.strip())
    except ValueError:
        return -1


def _normalize_str(s: Any) -> str:
    return str(s).strip().upper() if s else ''


def check_compliance(extracted_json: dict, bid_profile: dict) -> dict:
    """
    Match tender requirements against a company bid profile.

    Args:
        extracted_json: Output from analyst.py (extracted_json field)
        bid_profile: Row from bid_profiles table (dict with JSON fields parsed)

    Returns:
        {
          bid_readiness_score: int 0-100,
          gaps: [str],              # specific missing items
          matched_requirements: [str],
          warnings: [str],
          profile_missing: bool,    # True if bid_profile is None/empty
        }
    """
    if not bid_profile:
        return {
            'bid_readiness_score': 0,
            'gaps': ['No bid profile found — create a bid profile to enable compliance matching'],
            'matched_requirements': [],
            'warnings': [],
            'profile_missing': True,
        }

    gaps: list[str] = []
    matched: list[str] = []
    warnings: list[str] = []

    # Parse JSON fields from bid_profile (stored as JSON strings in SQLite)
    def _parse_json_field(field: str, default: Any) -> Any:
        raw = bid_profile.get(field)
        if isinstance(raw, str):
            try:
                return __import__('json').loads(raw)
            except Exception:
                return default
        return raw if raw is not None else default

    held_soa: list[dict] = _parse_json_field('soa_qualifications', [])
    held_certs: list[str] = _parse_json_field('certifications_held', [])
    held_regions: list[str] = _parse_json_field('geographic_regions', [])
    held_ateco: list[str] = _parse_json_field('ateco_codes', [])
    avg_value = bid_profile.get('avg_project_value_eur')

    # Build lookup: {category: max_classification_rank} from held qualifications
    held_soa_map: dict[str, int] = {}
    for qual in held_soa:
        cat = _normalize_str(qual.get('category', ''))
        cls_rank = _soa_rank(qual.get('classification', ''))
        if cat and cls_rank >= 0:
            held_soa_map[cat] = max(held_soa_map.get(cat, -1), cls_rank)

    # 1. SOA matching (weight: 50 points total)
    required_soa: list[dict] = extracted_json.get('soa_required') or []
    soa_score = 0
    per_soa_points = 50 // max(len(required_soa), 1) if required_soa else 50

    if not required_soa:
        matched.append('SOA: none required (or not found in document)')
        soa_score = 50
    else:
        for req in required_soa:
            cat = _normalize_str(req.get('category', ''))
            cls = (req.get('classification') or '').strip()
            cls_rank = _soa_rank(cls)

            if not cat:
                continue

            held_rank = held_soa_map.get(cat, -1)
            if held_rank == -1:
                gaps.append(
                    f'SOA {cat}: not held (required: {cls or "any"}) — '
                    f'must obtain SOA {cat} qualification'
                )
            elif cls_rank >= 0 and held_rank < cls_rank:
                held_cls = _SOA_ORDER[held_rank]
                gaps.append(
                    f'SOA {cat}: classification insufficient — '
                    f'you have {held_cls}, tender requires {cls}'
                )
            else:
                matched.append(f'SOA {cat}-{_SOA_ORDER[held_rank] if held_rank >= 0 else "?"}')
                soa_score += per_soa_points

    # 2. Certification matching (weight: 30 points total)
    required_certs: list[str] = extracted_json.get('certifications_required') or []
    cert_score = 0
    per_cert_points = 30 // max(len(required_certs), 1) if required_certs else 30

    if not required_certs:
        matched.append('Certifications: none required (or not found in document)')
        cert_score = 30
    else:
        held_certs_upper = {_normalize_str(c) for c in held_certs}
        for cert in required_certs:
            cert_upper = _normalize_str(cert)
            # Fuzzy match: "ISO 9001" matches "ISO 9001:2015"
            found = any(cert_upper in h or h in cert_upper for h in held_certs_upper)
            if found:
                matched.append(f'Certification: {cert}')
                cert_score += per_cert_points
            else:
                gaps.append(f'Certification missing: {cert}')

    # 3. Geographic matching (weight: 10 points)
    geo_score = 0
    geo_scope = extracted_json.get('geographic_scope')
    if not geo_scope:
        geo_score = 10
        matched.append('Geography: not restricted (or not found in document)')
    elif not held_regions:
        warnings.append('Geographic match unknown — no regions specified in bid profile')
        geo_score = 5  # partial credit
    else:
        geo_norm = _normalize_str(geo_scope)
        held_norms = {_normalize_str(r) for r in held_regions}
        # Simple region match: "ITA-ER" covers Emilia-Romagna, "ITA" covers all Italy
        if 'ITA' in held_norms or any(
            geo_norm in h or h in geo_norm or h == 'ITA'
            for h in held_norms
        ):
            geo_score = 10
            matched.append(f'Geography: {geo_scope}')
        else:
            gaps.append(
                f'Geographic mismatch: tender is in {geo_scope}, '
                f'your profile covers {", ".join(held_regions)}'
            )

    # 4. Contract value vs average project value (weight: 10 points)
    value_score = 0
    tender_value = extracted_json.get('value_eur')
    if not tender_value or not avg_value:
        value_score = 10
        if tender_value and not avg_value:
            warnings.append('Avg project value not set in bid profile — skipping value check')
    else:
        ratio = tender_value / avg_value
        if ratio <= 3.0:
            value_score = 10
            matched.append(f'Contract value: €{tender_value:,.0f} within range (avg: €{avg_value:,.0f})')
        elif ratio <= 5.0:
            value_score = 5
            warnings.append(
                f'Contract value €{tender_value:,.0f} is {ratio:.1f}x your average project '
                f'(€{avg_value:,.0f}) — may require additional financial guarantees'
            )
        else:
            gaps.append(
                f'Contract value €{tender_value:,.0f} is {ratio:.1f}x your average project '
                f'(€{avg_value:,.0f}) — high financial capacity risk'
            )

    total_score = min(100, soa_score + cert_score + geo_score + value_score)

    # --- Procurement intelligence (additive, non-breaking) -------------------
    tender_value = extracted_json.get('value_eur')
    # If the document declared no explicit SOA but a value is present, infer the
    # minimum classifica required so the gap report is not silent (design-review fix #2).
    inferred_soa = procurement.infer_soa_class(tender_value) if tender_value else None
    if inferred_soa and not required_soa:
        warnings.append(
            f'No explicit SOA category in document, but value €{float(tender_value):,.0f} '
            f'implies classifica ≥ {inferred_soa} for the works component.'
        )

    cig = extracted_json.get('cig') or procurement.extract_cig(
        ' '.join(str(extracted_json.get(k, '')) for k in ('raw_text', 'object', 'title', 'notes')))

    cauzione_prov = procurement.cauzione_provvisoria(tender_value or 0, held_certs)
    pnrr = procurement.detect_pnrr(
        ' '.join(str(extracted_json.get(k, '')) for k in ('raw_text', 'object', 'title', 'description')))
    dl_status = procurement.deadline_status(extracted_json.get('deadline') or extracted_json.get('deadline_date') or '')
    decision = procurement.go_no_go(total_score, dl_status, value_match=(value_score >= 10))

    return {
        'bid_readiness_score': total_score,
        'gaps': gaps,
        'matched_requirements': matched,
        'warnings': warnings,
        'profile_missing': False,
        # --- new in Phase 2 ---
        'cig': cig,
        'inferred_soa_class': inferred_soa,
        'cauzione_provvisoria': cauzione_prov,
        'pnrr': pnrr,
        'deadline_status': dl_status,
        'go_no_go': decision,
        'document_waterfall': procurement.required_documents(extracted_json),
        'dlgs36_checklist': procurement.dlgs36_checklist({**extracted_json, 'cig': cig}),
    }
