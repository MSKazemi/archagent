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


def clean(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


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
    by_city = collections.Counter(clean(l.get("performance_city")) or "Not specified" for l in leads)
    visible_eur = sum(float(l["estimated_value"] or 0) for l in leads if (l.get("currency") or "") == "EUR")
    worker_type = collections.Counter(w.get("worker_type") or "unknown" for w in workers)
    worker_city = collections.Counter(w.get("city") or "Not specified" for w in workers)
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
            f"{clean(lead.get('performance_city')).replace('|','-')} | {clean(lead.get('category')).replace('|','-')} | "
            f"{clean(lead.get('buyer_name')).replace('|','-')[:80]} | {value_label(lead)} | "
            f"{clean(lead.get('title')).replace('|','-')[:150]} | [TED]({lead.get('source_url') or ''}) |"
        )
    lines += ["", "## Italy public expert/worker/supplier coverage", ""]
    lines += [f"- {k}: {v}" for k, v in worker_type.most_common()]
    lines += ["", "### By city", ""]
    lines += [f"- {k}: {v}" for k, v in worker_city.most_common(25)]
    lines += ["", "### By trade group", ""]
    lines += [f"- {k}: {v}" for k, v in worker_trades.most_common(25)]
    lines += ["", "## Sample Italy listings for outreach verification", "", "| Name | Type | Trades | City | Contact/source |", "|---|---|---|---|---|"]
    for worker in workers[:60]:
        contact = worker.get("website") or worker.get("email") or worker.get("phone") or worker.get("source_url") or ""
        lines.append(
            f"| {clean(worker.get('name')).replace('|','-')[:80]} | {worker.get('worker_type') or ''} | "
            f"{clean(worker.get('trades')).replace('|','-')} | {clean(worker.get('city'))} | {contact} |"
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
