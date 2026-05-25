#!/usr/bin/env python3
"""Italy tender scoring and first-pass dossier generation.

This module deliberately stays dependency-free. It works from the local TED-derived
SQLite lead database and produces human-review-safe operating dossiers. It does
not claim to download official tender documents yet; instead it records the
source URL, likely portal/search actions, and a concrete checklist for document
collection before bidding.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
LEADS_DB = BASE / "archagent_actionable_projects.sqlite3"
APP_DB = BASE / "archagent_app.sqlite3"
EXPORT_DIR = BASE / "exports"

HIGH_VALUE_TERMS = {
    "energy_retrofit": ["efficientamento", "riqualificazione energetica", "energetico", "pnrr", "m7", "esco", "impianti termici", "hvac", "riscaldamento", "condizionamento"],
    "public_housing_ppp": ["erp", "edilizia residenziale pubblica", "ater", "iacp", "case popolari", "ppp", "partenariato pubblico privato", "finanza di progetto", "concessione"],
    "restoration_safety": ["restauro", "messa in sicurezza", "copertura", "tetto", "facciata", "isolamento", "serramenti", "edificio storico"],
    "design_services": ["progettazione", "direzione lavori", "fattibilità", "coordinamento sicurezza", "servizi tecnici", "engineering services", "technical services"],
}

NOISE_TERMS = [
    "insurance services", "polizza", "rifiuti", "waste", "prostheses", "protesi", "cochlear",
    "multimedia equipment", "data-processing machines", "chemicals", "electric pumps",
]


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def lead_text(lead: dict) -> str:
    return " ".join(clean(lead.get(k)) for k in ("title", "description", "category", "buyer_name", "cpv_codes")).lower()


def score_italy_fit(lead: dict) -> dict:
    text = lead_text(lead)
    score = int(lead.get("relevance_score") or 0)
    reasons: list[str] = []
    risks: list[str] = []
    wedges: list[str] = []

    for wedge, terms in HIGH_VALUE_TERMS.items():
        hits = [term for term in terms if term in text]
        if hits:
            add = min(30, 10 + len(hits) * 5)
            score += add
            wedges.append(wedge.replace("_", " / "))
            reasons.append(f"{wedge.replace('_', ' ')} signals: {', '.join(hits[:5])}")

    if wedges:
        score += 15
        reasons.append('strong Italy-specific wedge match')

    value = lead.get("estimated_value")
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0
    if value >= 10_000_000:
        score += 12; reasons.append("large disclosed value above €10M")
    elif value >= 1_000_000:
        score += 7; reasons.append("material disclosed value above €1M")
    else:
        risks.append("estimated value is missing or below €1M")

    deadline = clean(lead.get("deadline_date"))
    if deadline:
        try:
            days = (dt.date.fromisoformat(deadline[:10]) - dt.date.today()).days
            if days < 3:
                risks.append("deadline is extremely close")
            elif days <= 21:
                score += 5; reasons.append("deadline is near enough for urgency")
        except ValueError:
            risks.append("deadline could not be parsed")
    else:
        risks.append("deadline missing")

    noise_hits = [term for term in NOISE_TERMS if term in text]
    if noise_hits:
        score -= 35
        risks.append("possible non-building/noise scope: " + ", ".join(noise_hits[:4]))

    if not clean(lead.get("source_url")):
        risks.append("source URL missing")

    score = max(0, min(100, score))
    wedge = wedges[0] if wedges else (clean(lead.get("category")) or "general building opportunity")
    recommended_offer = "Bid-readiness dossier + partner sourcing"
    if "energy" in wedge or "public housing" in wedge:
        recommended_offer = "Italy energy-retrofit lead radar + bid-readiness package"
    elif "restoration" in wedge:
        recommended_offer = "Restoration/roof safety bid package + specialist partner sourcing"
    elif "design" in wedge:
        recommended_offer = "Architecture/technical-services tender briefing"

    return {
        "italy_fit_score": score,
        "italy_wedge": wedge,
        "fit_reasons": reasons or ["matches Italy public building opportunity filters"],
        "risks": risks,
        "recommended_offer": recommended_offer,
    }


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def load_italy_leads() -> list[dict]:
    con = sqlite3.connect(LEADS_DB)
    con.row_factory = sqlite3.Row
    rows = [row_to_dict(r) for r in con.execute(
        """
        SELECT * FROM project_leads
        WHERE performance_country='ITA' OR buyer_country='ITA' OR title LIKE 'Italy –%'
        ORDER BY relevance_score DESC, deadline_date ASC, publication_date DESC
        """
    ).fetchall()]
    con.close()
    return rows


def select_top_italy_leads(limit: int = 10, min_score: int = 50) -> list[dict]:
    enriched = []
    for lead in load_italy_leads():
        scored = score_italy_fit(lead)
        item = {**lead, **scored}
        if item["italy_fit_score"] >= min_score:
            enriched.append(item)
    enriched.sort(key=lambda x: (x["italy_fit_score"], x.get("estimated_value") or 0), reverse=True)
    return enriched[:limit]


def document_collection_checklist(lead: dict) -> list[str]:
    notice = clean(lead.get("source_notice_id"))
    buyer = clean(lead.get("buyer_name")) or "buyer/procurement portal"
    return [
        f"Open TED notice {notice} and record all 'Documents' / 'Buyer profile' / eSender links.",
        f"Search the official procurement portal for notice ID {notice} and buyer name '{buyer}'.",
        "Download disciplinare di gara, capitolato, schema di contratto, computo metrico, elaborati tecnici, DGUE/forms, and clarification documents where available.",
        "Extract eligibility requirements: SOA categories, turnover, certifications, insurance, professional registration, language/submission rules.",
        "Extract evaluation method, award criteria, inspection/site-visit requirements, and mandatory annexes.",
        "Confirm submission portal, digital-signature requirements, guarantee/bond requirements, and final deadline/timezone.",
    ]


def build_tender_dossier(notice_id: str, *, source: str = "api", save: bool = True) -> dict:
    con = sqlite3.connect(LEADS_DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM project_leads WHERE source_notice_id=?", (notice_id,)).fetchone()
    con.close()
    if not row:
        raise ValueError(f"lead not found: {notice_id}")
    lead = row_to_dict(row)
    scored = score_italy_fit(lead)
    checklist = document_collection_checklist(lead)
    generated = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    source_url = clean(lead.get("source_url"))
    title = clean(lead.get("title"))
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

## Italy fit score

- Score: {scored['italy_fit_score']}/100
- Wedge: {scored['italy_wedge']}
- Recommended offer: {scored['recommended_offer']}

### Fit reasons
{chr(10).join('- ' + r for r in scored['fit_reasons'])}

### Risks / checks
{chr(10).join('- ' + r for r in (scored['risks'] or ['No automatic risk flags; still requires human review.']))}

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
    result = {"notice_id": notice_id, "lead": lead, **scored, "checklist": checklist, "markdown": markdown}
    if save:
        EXPORT_DIR.mkdir(exist_ok=True)
        safe_source = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in source)[:40]
        path = EXPORT_DIR / f"tender_dossier_{notice_id}_{safe_source}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(markdown, encoding="utf-8")
        result["export_path"] = str(path)
        result["url"] = "/exports/" + path.name
        app = sqlite3.connect(APP_DB)
        app.execute(
            """
            INSERT INTO tender_dossiers(source_notice_id, source, italy_fit_score, italy_wedge, recommended_offer, risks_json, export_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (notice_id, source, scored["italy_fit_score"], scored["italy_wedge"], scored["recommended_offer"], str(scored["risks"]), str(path), generated),
        )
        app.execute(
            "INSERT INTO activities(kind,message,payload_json,created_at) VALUES (?,?,?,?)",
            ("tender_dossier", f"Generated Italy tender dossier for {notice_id}", f"{{\"notice_id\":\"{notice_id}\",\"source\":\"{source}\"}}", generated),
        )
        app.commit(); app.close()
    return result


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Generate top Italy bid-readiness dossiers.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--notice-id", help="Generate one dossier for a specific notice ID")
    args = parser.parse_args()
    if args.notice_id:
        d = build_tender_dossier(args.notice_id, source="cli", save=True)
        print(d["export_path"])
    else:
        for lead in select_top_italy_leads(args.limit, args.min_score):
            d = build_tender_dossier(lead["source_notice_id"], source="cli", save=True)
            print(f"{lead['source_notice_id']} score={lead['italy_fit_score']} {d['export_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
