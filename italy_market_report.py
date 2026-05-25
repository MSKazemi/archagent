#!/usr/bin/env python3
"""Generate an Italy-focused ArchAgent market report from local public-data databases."""
from __future__ import annotations

import collections
import datetime as dt
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
LEADS_DB = BASE / "archagent_actionable_projects.sqlite3"
APP_DB = BASE / "archagent_app.sqlite3"
OUT = BASE / "ITALY_MARKET_REPORT.md"

EXCLUDE_TERMS = (
    "insurance services",
    "polizza",
    "rimborso spese mediche",
    "plastic products",
    "cochlear",
    "video-signal",
    "waste",
    "rifiuti",
    "railway or tramway locomotives",
    "ricambi per impianti antincendio",  # parts supply, not a building retrofit job
    "data-processing machines",
    "multimedia equipment",
    "basic inorganic chemicals",
    "electric pumps",
    "machinery and apparatus for filtering",
    "operation of a sewage plant",
    "wastewater-plant",
    "tele filtranti",
    "defosfatante",
)

STRONG_BUILDING_TERMS = (
    "construction",
    "building",
    "restructuring",
    "restoration",
    "renovation",
    "maintenance work",
    "energy",
    "energetico",
    "efficientamento",
    "riqualificazione",
    "ristrutturazione",
    "restauro",
    "messa in sicurezza",
    "edilizia",
    "impianti termici",
    "impianti tecnologici ed energetici",
    "hvac",
    "riscaldamento",
    "facciata",
    "serramenti",
    "isolamento",
    "thermal insulation",
    "roof",
)


CITY_ALIASES = {
    "milan": "Milano",
    "milano": "Milano",
    "milano (mi)": "Milano",
    "roma": "Roma",
    "rome": "Roma",
    "turin": "Torino",
    "torino": "Torino",
    "naples": "Napoli",
    "napoli": "Napoli",
    "bologna": "Bologna",
    "ragusa": "Ragusa",
    "matera matera (mt)": "Matera",
    "vercelli": "Vercelli",
    "viterbo": "Viterbo",
    "genova (ge)": "Genova",
    "sassari (ss)": "Sassari",
    "feldthurns / velturno": "Velturno / Feldthurns",
}

REGION_HINTS = {
    "Milano": "Lombardia",
    "Sesto San Giovanni": "Lombardia",
    "Moglia di Sermide": "Lombardia",
    "Torino": "Piemonte",
    "Cuneo": "Piemonte",
    "Vercelli": "Piemonte",
    "Roma": "Lazio",
    "Viterbo": "Lazio",
    "Napoli": "Campania",
    "Bologna": "Emilia-Romagna",
    "Noceto": "Emilia-Romagna",
    "Matera": "Basilicata",
    "Ragusa": "Sicilia",
    "Messina": "Sicilia",
    "Palermo": "Sicilia",
    "Genova": "Liguria",
    "Aulla": "Toscana",
    "Quarrata": "Toscana",
    "Roncade": "Veneto",
    "Bassano del Grappa": "Veneto",
    "Chioggia": "Veneto",
    "Trento": "Trentino-Alto Adige",
    "Velturno / Feldthurns": "Trentino-Alto Adige",
    "Castelsantangelo sul Nera": "Marche",
    "Sassari": "Sardegna",
    "Terni": "Umbria",
}


def clean(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def normalize_city(value: str) -> str:
    text = clean(value)
    if not text:
        return "Not specified"
    low = text.lower()
    if low in CITY_ALIASES:
        return CITY_ALIASES[low]
    if "messina" in low:
        return "Messina"
    if "alessandria" in low:
        return "Alessandria / Asti / Cuneo"
    if "chioggia" in low:
        return "Chioggia"
    if "castelsantangelo" in low:
        return "Castelsantangelo sul Nera"
    return text


def region_for_city(city: str) -> str:
    return REGION_HINTS.get(city, "Not specified")


def is_qualified_italy_lead(row: dict) -> bool:
    text = clean(" ".join(str(row.get(k) or "") for k in ("title", "description", "category", "cpv_codes"))).lower()
    if any(term in text for term in EXCLUDE_TERMS):
        return False
    try:
        if int(row.get('relevance_score') or 0) < 55:
            return False
    except Exception:
        return False
    cpv = clean(row.get("cpv_codes"))
    if any(code.strip().startswith("45") for code in cpv.replace(";", " ").replace(",", " ").split()):
        return True
    return any(term in text for term in STRONG_BUILDING_TERMS)


def load_leads() -> list[dict]:
    con = sqlite3.connect(LEADS_DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        """
        SELECT * FROM project_leads
        WHERE performance_country='ITA' OR buyer_country='ITA' OR title LIKE 'Italy –%'
        ORDER BY relevance_score DESC, deadline_date ASC, publication_date DESC
        """
    ).fetchall()]
    con.close()
    return [r for r in rows if is_qualified_italy_lead(r)]


def load_workers() -> list[dict]:
    con = sqlite3.connect(APP_DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM expert_workers WHERE country='Italy' ORDER BY city, worker_type, name").fetchall()]
    con.close()
    return rows


def value_label(row: dict) -> str:
    if row.get("estimated_value"):
        return f"{float(row['estimated_value']):,.0f} {row.get('currency') or ''}".strip()
    return "Not disclosed"


def main() -> int:
    leads = load_leads()
    workers = load_workers()
    by_cat = collections.Counter(l.get("category") or "unknown" for l in leads)
    by_city = collections.Counter(normalize_city(l.get("performance_city")) for l in leads)
    by_region = collections.Counter(region_for_city(normalize_city(l.get("performance_city"))) for l in leads)
    visible_eur = sum(float(l["estimated_value"] or 0) for l in leads if (l.get("currency") or "") == "EUR")
    worker_type = collections.Counter(w.get("worker_type") or "unknown" for w in workers)
    worker_city = collections.Counter(normalize_city(w.get("city")) for w in workers)
    worker_region = collections.Counter(region_for_city(normalize_city(w.get("city"))) for w in workers)
    worker_trades = collections.Counter(w.get("trades") or "unknown" for w in workers)
    with_contact = sum(1 for w in workers if w.get("phone") or w.get("email") or w.get("website"))

    lines = [
        "# ArchAgent Italy Market Report",
        "",
        f"Generated: {dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()}",
        "",
        "Sources:",
        "- TED Search API active public procurement notices stored in `archagent_actionable_projects.sqlite3`.",
        "- OpenStreetMap/Overpass public business listings stored in `archagent_app.sqlite3`.",
        "",
        "Important: worker/company listings are public unverified records, not vetted partners. Tender records are notice-level leads; official tender documents still need human review before bidding.",
        "",
        "## Executive summary",
        "",
        f"- ArchAgent-qualified active Italy building/retrofit/tender leads: {len(leads)}.",
        f"- Visible disclosed Italy EUR opportunity value: €{visible_eur:,.0f}.",
        f"- Italy public expert/worker/supplier listings imported: {len(workers)}.",
        f"- Italy listings with at least one phone/email/website field: {with_contact}.",
        "",
        "## Italy tender leads by category",
        "",
    ]
    lines += [f"- {k}: {v}" for k, v in by_cat.most_common()]
    lines += ["", "## Italy tender leads by region", ""]
    lines += [f"- {k}: {v}" for k, v in by_region.most_common(25)]
    lines += ["", "## Italy tender leads by city / area", ""]
    lines += [f"- {k}: {v}" for k, v in by_city.most_common(25)]
    lines += [
        "",
        "## Top Italy opportunities",
        "",
        "| Score | Notice | Deadline | City | Category | Buyer | Value | Title | Source |",
        "|---:|---|---|---|---|---|---:|---|---|",
    ]
    for lead in leads[:35]:
        lines.append(
            f"| {lead.get('relevance_score')} | {lead.get('source_notice_id')} | {lead.get('deadline_date') or ''} | "
            f"{normalize_city(lead.get('performance_city')).replace('|','-')} | {clean(lead.get('category')).replace('|','-')} | "
            f"{clean(lead.get('buyer_name')).replace('|','-')[:80]} | {value_label(lead)} | "
            f"{clean(lead.get('title')).replace('|','-')[:150]} | [TED]({lead.get('source_url') or ''}) |"
        )
    lines += ["", "## Italy public expert/worker/supplier coverage", ""]
    lines += [f"- {k}: {v}" for k, v in worker_type.most_common()]
    lines += ["", "### By region", ""]
    lines += [f"- {k}: {v}" for k, v in worker_region.most_common(25)]
    lines += ["", "### By city", ""]
    lines += [f"- {k}: {v}" for k, v in worker_city.most_common(25)]
    lines += ["", "### By trade group", ""]
    lines += [f"- {k}: {v}" for k, v in worker_trades.most_common(25)]
    lines += ["", "## Sample Italy listings for outreach verification", "", "| Name | Type | Trades | City | Contact/source |", "|---|---|---|---|---|"]
    for worker in workers[:60]:
        contact = worker.get("website") or worker.get("email") or worker.get("phone") or worker.get("source_url") or ""
        lines.append(
            f"| {clean(worker.get('name')).replace('|','-')[:80]} | {worker.get('worker_type') or ''} | "
            f"{clean(worker.get('trades')).replace('|','-')} | {normalize_city(worker.get('city'))} | {contact} |"
        )
    lines += [
        "",
        "## Recommended Italy wedge",
        "",
        "1. Start with energy-efficiency/PPP/ERP housing retrofit leads because the current Italy data shows several high-value public building energy-efficiency concessions.",
        "2. Secondary wedge: restoration, roof/safety works, and public-building maintenance where the scope is easier to package for architects/contractors.",
        "3. Build a verified Italy partner list from the public listings: contact, qualify, mark availability/certifications/languages before using them in customer-facing matching.",
        "4. For each selected Italy tender, download official documents and build a source-cited compliance matrix before pricing a bid package.",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)
    print(f"qualified_italy_leads={len(leads)} italy_workers={len(workers)} visible_eur={visible_eur:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
