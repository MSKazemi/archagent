#!/usr/bin/env python3
"""ArchAgent real EU project finder.

Fetches real public procurement notices from TED Search API and stores them in
SQLite for ArchAgent BuildingOS lead discovery.

Source docs: https://ted.europa.eu/api/documentation/index.html
API endpoint: https://api.ted.europa.eu/v3/notices/search
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "archagent_actionable_projects.sqlite3"
REPORT_PATH = ROOT / "ACTIONABLE_PROJECTS.md"
CSV_PATH = ROOT / "actionable_projects.csv"
JSON_PATH = ROOT / "actionable_projects.json"
TED_ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"

FIELDS = [
    "publication-number",
    "notice-type",
    "notice-title",
    "title-proc",
    "title-lot",
    "description-proc",
    "description-lot",
    "organisation-name-buyer",
    "organisation-country-buyer",
    "place-of-performance-country-lot",
    "place-of-performance-city-lot",
    "classification-cpv",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "estimated-value-lot",
    "estimated-value-cur-lot",
]

# Construction/building-related CPV codes usually start with 45. The keyword
# searches catch notices where CPV classification is imperfect or mixed.
SEARCHES = [
    {
        "name": "construction_cpv_recent",
        "query": "deadline-receipt-tender-date-lot >= today(0) AND publication-date >= today(-90) AND classification-cpv=45* SORT BY publication-date DESC",
    },
    {
        "name": "renovation_recent",
        "query": "deadline-receipt-tender-date-lot >= today(0) AND publication-date >= today(-180) AND (title-proc~renovation OR description-lot~renovation OR title-proc~réhabilitation OR description-lot~réhabilitation OR title-proc~sanierung OR description-lot~sanierung) SORT BY publication-date DESC",
    },
    {
        "name": "insulation_recent",
        "query": "deadline-receipt-tender-date-lot >= today(0) AND publication-date >= today(-180) AND (title-proc~insulation OR description-lot~insulation OR title-proc~isolation OR description-lot~isolation OR title-proc~dämmung OR description-lot~dämmung) SORT BY publication-date DESC",
    },
    {
        "name": "hvac_energy_recent",
        "query": "deadline-receipt-tender-date-lot >= today(0) AND publication-date >= today(-180) AND (title-proc~HVAC OR description-lot~HVAC OR title-proc~heating OR description-lot~heating OR title-proc~ventilation OR description-lot~ventilation OR title-proc~photovoltaic OR description-lot~photovoltaic) SORT BY publication-date DESC",
    },
    {
        "name": "painting_recent",
        "query": "deadline-receipt-tender-date-lot >= today(0) AND publication-date >= today(-180) AND (title-proc~painting OR description-lot~painting OR title-proc~peinture OR description-lot~peinture OR title-proc~maler OR description-lot~maler) SORT BY publication-date DESC",
    },
]

CATEGORY_RULES = [
    ("energy / HVAC / solar", ["hvac", "ventilation", "heating", "heat pump", "photovoltaic", "solar", "boiler", "air conditioning", "climatisation"]),
    ("insulation / facade / envelope", ["insulation", "isolation", "dämm", "facade", "façade", "window", "glazing", "roof", "envelope"]),
    ("renovation / rehabilitation", ["renovation", "réhabilitation", "rehabilitation", "refurbishment", "sanierung", "modernisation", "upgrade"]),
    ("painting / finishing", ["painting", "peinture", "paint", "coating", "maler", "finishing", "plaster"]),
    ("architecture / design", ["architect", "design", "engineering", "studies", "maîtrise", "project management"]),
    ("construction / civil works", ["construction", "building", "works", "travaux", "bau", "civil"]),
]

# TED contains some global/external procurement notices. Keep the ArchAgent
# first report focused on Europe: EU + EEA + UK/CH and nearby TED countries.
EUROPE_COUNTRIES = {
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE", "NOR", "ISL", "LIE",
    "CHE", "GBR", "UK", "ALB", "BIH", "MNE", "MKD", "SRB", "TUR", "UKR",
}


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            base_url TEXT NOT NULL,
            terms_url TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS project_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_notice_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            buyer_name TEXT,
            buyer_country TEXT,
            performance_country TEXT,
            performance_city TEXT,
            publication_date TEXT,
            deadline_date TEXT,
            estimated_value REAL,
            currency TEXT,
            cpv_codes TEXT,
            category TEXT,
            relevance_score INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            raw_json TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_name, source_notice_id)
        );

        CREATE INDEX IF NOT EXISTS idx_project_leads_publication_date ON project_leads(publication_date DESC);
        CREATE INDEX IF NOT EXISTS idx_project_leads_deadline ON project_leads(deadline_date);
        CREATE INDEX IF NOT EXISTS idx_project_leads_country ON project_leads(performance_country, buyer_country);
        CREATE INDEX IF NOT EXISTS idx_project_leads_category ON project_leads(category);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sources(name, kind, base_url, terms_url)
        VALUES (?, ?, ?, ?)
        """,
        (
            "TED Search API",
            "public_procurement_api",
            TED_ENDPOINT,
            "https://ted.europa.eu/en/legal-notice",
        ),
    )
    conn.commit()
    return conn


def request_ted(query: str, limit: int) -> dict[str, Any]:
    payload = {
        "query": query,
        "fields": FIELDS,
        "page": 1,
        "limit": limit,
        "scope": "ACTIVE",
        "onlyLatestVersions": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TED_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ArchAgent-BuildingOS-Prototype/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TED API error {exc.code}: {body}") from exc


def flatten(value: Any, preferred_langs: tuple[str, ...] = ("eng", "en", "fra", "deu", "ger", "spa", "ita")) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [flatten(v) for v in value]
        seen = []
        for part in parts:
            if part and part not in seen:
                seen.append(part)
        return "; ".join(seen)
    if isinstance(value, dict):
        for lang in preferred_langs:
            if lang in value:
                return flatten(value[lang])
        for _, v in value.items():
            text = flatten(v)
            if text:
                return text
    return ""


def first_list_value(value: Any) -> str:
    if isinstance(value, list) and value:
        return flatten(value[0])
    return flatten(value)


def normalize_date(value: Any) -> str:
    text = first_list_value(value)
    if not text:
        return ""
    # TED dates often look like 2026-06-01+02:00 or 2026-05-20+02:00
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    match = re.match(r"(\d{8})", text)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return text[:10]


def parse_float(value: Any) -> float | None:
    text = first_list_value(value).replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def ted_url(publication_number: str) -> str:
    return f"https://ted.europa.eu/en/notice/-/detail/{publication_number}"


def classify(title: str, description: str, cpv_codes: str) -> tuple[str, int]:
    haystack = f"{title} {description} {cpv_codes}".lower()
    score = 35
    category = "construction / building works"

    for name, keywords in CATEGORY_RULES:
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits:
            category = name
            score += min(35, hits * 12)
            break

    if any(code.startswith("45") for code in re.findall(r"\d{8}", cpv_codes)):
        score += 20
    if any(term in haystack for term in ["renovation", "réhabilitation", "insulation", "hvac", "facade", "photovoltaic", "painting", "window"]):
        score += 15
    if any(term in haystack for term in ["school", "hospital", "housing", "apartment", "hotel", "office", "public building"]):
        score += 10
    return category, min(score, 100)


def normalize_notice(notice: dict[str, Any]) -> dict[str, Any]:
    publication_number = flatten(notice.get("publication-number"))
    title = flatten(notice.get("notice-title")) or flatten(notice.get("title-proc")) or flatten(notice.get("title-lot")) or f"TED notice {publication_number}"
    description = flatten(notice.get("description-proc")) or flatten(notice.get("description-lot"))
    cpv_codes = flatten(notice.get("classification-cpv"))
    category, score = classify(title, description, cpv_codes)
    return {
        "source_name": "TED Search API",
        "source_notice_id": publication_number,
        "title": title.strip(),
        "description": description.strip(),
        "buyer_name": flatten(notice.get("organisation-name-buyer")),
        "buyer_country": first_list_value(notice.get("organisation-country-buyer")),
        "performance_country": first_list_value(notice.get("place-of-performance-country-lot")),
        "performance_city": first_list_value(notice.get("place-of-performance-city-lot")),
        "publication_date": normalize_date(notice.get("publication-date")),
        "deadline_date": normalize_date(notice.get("deadline-receipt-tender-date-lot")),
        "estimated_value": parse_float(notice.get("estimated-value-lot")),
        "currency": first_list_value(notice.get("estimated-value-cur-lot")),
        "cpv_codes": cpv_codes,
        "category": category,
        "relevance_score": score,
        "source_url": ted_url(publication_number) if publication_number else "",
        "raw_json": json.dumps(notice, ensure_ascii=False, sort_keys=True),
    }


def is_european_actionable(lead: dict[str, Any], today: dt.date | None = None) -> bool:
    """Return True only for Europe-focused notices with an applyable future deadline."""
    today = today or dt.date.today()
    country = (lead.get("performance_country") or lead.get("buyer_country") or "").strip()
    if country not in EUROPE_COUNTRIES:
        return False
    deadline = (lead.get("deadline_date") or "").strip()
    if not deadline:
        return False
    try:
        return dt.date.fromisoformat(deadline) >= today
    except ValueError:
        return False


def upsert_lead(conn: sqlite3.Connection, lead: dict[str, Any]) -> bool:
    before = conn.total_changes
    conn.execute(
        """
        INSERT INTO project_leads(
            source_name, source_notice_id, title, description, buyer_name,
            buyer_country, performance_country, performance_city,
            publication_date, deadline_date, estimated_value, currency,
            cpv_codes, category, relevance_score, source_url, raw_json
        ) VALUES (
            :source_name, :source_notice_id, :title, :description, :buyer_name,
            :buyer_country, :performance_country, :performance_city,
            :publication_date, :deadline_date, :estimated_value, :currency,
            :cpv_codes, :category, :relevance_score, :source_url, :raw_json
        )
        ON CONFLICT(source_name, source_notice_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            buyer_name=excluded.buyer_name,
            buyer_country=excluded.buyer_country,
            performance_country=excluded.performance_country,
            performance_city=excluded.performance_city,
            publication_date=excluded.publication_date,
            deadline_date=excluded.deadline_date,
            estimated_value=excluded.estimated_value,
            currency=excluded.currency,
            cpv_codes=excluded.cpv_codes,
            category=excluded.category,
            relevance_score=excluded.relevance_score,
            source_url=excluded.source_url,
            raw_json=excluded.raw_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        lead,
    )
    return conn.total_changes > before


def import_projects(limit_per_search: int = 50) -> dict[str, Any]:
    conn = init_db()
    imported = 0
    skipped_not_actionable = 0
    seen = set()
    search_stats = []
    today = dt.date.today()
    for search in SEARCHES:
        data = request_ted(search["query"], limit_per_search)
        notices = data.get("notices", [])
        accepted_for_search = 0
        for notice in notices:
            lead = normalize_notice(notice)
            if not lead["source_notice_id"] or lead["source_notice_id"] in seen:
                continue
            seen.add(lead["source_notice_id"])
            if not is_european_actionable(lead, today=today):
                skipped_not_actionable += 1
                continue
            upsert_lead(conn, lead)
            imported += 1
            accepted_for_search += 1
        search_stats.append({"name": search["name"], "returned": len(notices), "accepted_actionable": accepted_for_search})
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM project_leads").fetchone()[0]
    return {
        "imported_unique_this_run": imported,
        "skipped_not_actionable": skipped_not_actionable,
        "total_in_database": total,
        "searches": search_stats,
    }


def top_projects(conn: sqlite3.Connection, limit: int = 25, europe_only: bool = True) -> list[sqlite3.Row]:
    country_clause = ""
    params: list[Any] = []
    if europe_only:
        placeholders = ",".join("?" for _ in EUROPE_COUNTRIES)
        country_clause = f"""
            WHERE COALESCE(NULLIF(performance_country, ''), NULLIF(buyer_country, '')) IN ({placeholders})
        """
        params.extend(sorted(EUROPE_COUNTRIES))
    params.append(limit)
    return list(
        conn.execute(
            f"""
            SELECT * FROM project_leads
            {country_clause}
            ORDER BY relevance_score DESC, publication_date DESC, deadline_date ASC
            LIMIT ?
            """,
            params,
        )
    )


def write_report(limit: int = 25) -> None:
    conn = init_db()
    rows = top_projects(conn, limit)
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Applyable Real Building Projects",
        "",
        f"Generated: {generated}",
        "",
        "Source used now: TED Search API, the public EU Tenders Electronic Daily search API.",
        "",
        f"Filter: Europe-focused notices with a tender deadline on or after {dt.date.today().isoformat()}. Old/historical notices and notices without a clear deadline are excluded.",
        "",
        "These are real public procurement opportunities imported into `archagent_actionable_projects.sqlite3`. Relevance is a simple first-pass score for ArchAgent building/renovation/energy/painting fit.",
        "",
        "| Score | Published | Deadline | Country / City | Category | Title | Value | Link |",
        "|---:|---|---|---|---|---|---:|---|",
    ]
    for row in rows:
        country_city = " / ".join(x for x in [row["performance_country"] or row["buyer_country"], row["performance_city"]] if x)
        title = (row["title"] or "").replace("|", "-")[:120]
        value = ""
        if row["estimated_value"]:
            value = f"{row['estimated_value']:,.0f} {row['currency'] or ''}".strip()
        lines.append(
            f"| {row['relevance_score']} | {row['publication_date'] or ''} | {row['deadline_date'] or ''} | {country_city} | {row['category']} | {title} | {value} | [TED]({row['source_url']}) |"
        )
    lines.extend(
        [
            "",
            "## Next importer sources",
            "",
            "1. National procurement portals for target countries.",
            "2. City planning / building permit portals.",
            "3. Energy grant and EPC upgrade programs.",
            "4. Private/local job intake form on the ArchAgent website.",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_exports(limit: int = 200) -> None:
    import csv

    conn = init_db()
    rows = top_projects(conn, limit)
    fieldnames = [
        "relevance_score",
        "publication_date",
        "deadline_date",
        "country",
        "performance_city",
        "category",
        "title",
        "estimated_value",
        "currency",
        "source_url",
        "source_notice_id",
        "buyer_name",
        "cpv_codes",
    ]
    records = []
    for row in rows:
        records.append(
            {
                "relevance_score": row["relevance_score"],
                "publication_date": row["publication_date"],
                "deadline_date": row["deadline_date"],
                "country": row["performance_country"] or row["buyer_country"],
                "performance_city": row["performance_city"],
                "category": row["category"],
                "title": row["title"],
                "estimated_value": row["estimated_value"],
                "currency": row["currency"],
                "source_url": row["source_url"],
                "source_notice_id": row["source_notice_id"],
                "buyer_name": row["buyer_name"],
                "cpv_codes": row["cpv_codes"],
            }
        )
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    JSON_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(limit: int = 10) -> None:
    conn = init_db()
    rows = top_projects(conn, limit)
    print(f"Database: {DB_PATH}")
    print(f"Report:   {REPORT_PATH}")
    print(f"Top {len(rows)} project leads:\n")
    for idx, row in enumerate(rows, 1):
        country_city = " / ".join(x for x in [row["performance_country"] or row["buyer_country"], row["performance_city"]] if x)
        title = textwrap.shorten(row["title"] or "", width=110, placeholder="…")
        print(f"{idx:02d}. [{row['relevance_score']:3d}] {row['publication_date'] or '?'} {country_city or '?'} — {title}")
        print(f"    category: {row['category']} | deadline: {row['deadline_date'] or 'unknown'} | {row['source_url']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Find and store real EU building projects for ArchAgent.")
    parser.add_argument("--limit-per-search", type=int, default=50, help="TED notices to fetch per search query")
    parser.add_argument("--report-limit", type=int, default=25, help="Rows to include in markdown report")
    parser.add_argument("--init-only", action="store_true", help="Create database schema only")
    args = parser.parse_args(argv)

    init_db()
    if args.init_only:
        print(f"Initialized database at {DB_PATH}")
        return 0

    stats = import_projects(args.limit_per_search)
    write_report(args.report_limit)
    write_exports(max(args.report_limit, 200))
    print(json.dumps(stats, indent=2))
    print_summary(min(args.report_limit, 10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
