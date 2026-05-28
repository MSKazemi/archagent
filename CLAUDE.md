# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

Pure Python 3 standard library only — no dependencies, no pip install. Runs anywhere Python 3.9+ is installed.

**BuildingOS Phase 1** adds `analyst.py` and `compliance_agent.py`, which require external tools (`pdftotext`, `pdftoppm`) and Azure OpenAI. These modules are optional — all other functionality works without them.

## Run the server

```bash
python3 archagent_server.py --host 127.0.0.1 --port 8091
# With token auth:
ARCHAGENT_TOKEN='***' python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Open `http://127.0.0.1:8091/app`. `.env` is loaded automatically at startup; copy `.env.example` to `.env` to configure locally.

Env vars for Azure OpenAI (required for `POST /api/dossier/analyze`):
- `AZURE_OPENAI_KEY` — API key
- `AZURE_OPENAI_ENDPOINT` — e.g. `https://your-resource.openai.azure.com`
- `AZURE_OPENAI_DEPLOYMENT` — e.g. `gpt-4o`
- `AZURE_OPENAI_API_VERSION` — e.g. `2024-10-21`

## Tests

```bash
python3 smoke_test.py                  # end-to-end API test (starts server on port 8092)
python3 production_regression_test.py  # auth, importer, customer profiles, lead radar exports
python3 production_italy_test.py       # Italy scoring, dossiers, summary, worker verification
python3 archagent_maintenance.py       # SQLite integrity check
python3 test_analyst.py               # analyst.py + compliance_agent.py unit tests (mocked); integration test runs only when AZURE_OPENAI_KEY is set
```

Each test script is self-contained and prints a single `PASS ...` line on success.

## Architecture

**Data layer — two SQLite databases:**
- `archagent_actionable_projects.sqlite3` — read-mostly lead database (`project_leads` table), fed by TED/EU procurement ingestion. This is the canonical leads DB.
- `archagent_app.sqlite3` — read-write app database holding prospects, proposals, customer profiles, follow-ups, activities, workers, verifications, exports, dossiers, and bid profiles. Initialized with seed contractors on first run by `init_app_db()`.
  - `bid_profiles` table — company bid qualification profiles; SOA qualifications stored as `[{category, classification}]` JSON.
- `archagent_projects.sqlite3` — legacy parallel output from `project_finder.py`; not used by new code.

**Server (`archagent_server.py`):**
Single-file HTTP server using `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`. All API logic lives in plain functions; the `Handler` class does routing by matching `parsed.path` string literals. Auth is opt-in via `ARCHAGENT_TOKEN`; `/api/health` is always public. Static files (`.html`, `exports/`) are served by the parent `SimpleHTTPRequestHandler.do_GET()`.

**Document generation (`proposal_engine.py`):**
Deterministic, template-based. Generates proposals, compliance matrices, CRM follow-ups, outreach packs, and building audits from lead data using rule tables (`TRADE_RULES`, `PACKAGE_PRICES`). No LLM by default; a `hermes` CLI subprocess integration is gated by `HERMES_PROPOSAL_ENABLED=1`.

**Lead ingestion (`project_finder.py`):**
Fetches TED EU procurement notices by keyword/CPV search. Writes to `archagent_actionable_projects.sqlite3`.

**Italy pipeline:**
- `tender_document_collector.py` — scores Italy fit using keyword term-matching against `HIGH_VALUE_TERMS`/`NOISE_TERMS`, generates bid-readiness dossiers saved to `exports/`
- `italy_refresh.py` — orchestrates full Italy data refresh: TED leads + OSM workers + seed profiles + market report
- `archagent_production_refresh.py` — production wrapper: backup → italy_refresh → dossiers → tests → maintenance

**Worker/expert data (`expert_worker_importer.py`):**
Queries OpenStreetMap Overpass API for construction/architecture businesses. All imported records are `verification_status = public_listing_unverified` until manually promoted via `POST /api/workers/verify`.

**Tender analysis — BuildingOS Phase 1:**
- `analyst.py` — LLM tender PDF analysis via Azure OpenAI. Entry point: `analyze_tender(pdf_bytes, notice_id='')`. Returns `{status, extracted_json, cost_estimate_eur, pages_analyzed, is_truncated, is_scanned, warnings, error}`. Uses `pdftotext` for digital PDFs; falls back to `pdftoppm` + gpt-4o vision for scanned ones. Hard cap: 150 pages. Required env vars: `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`.
- `compliance_agent.py` — Rule-based bid compliance matching. Entry point: `check_compliance(extracted_json, bid_profile_row)`. Returns `{bid_readiness_score, gaps, matched_requirements, warnings, profile_missing}`. Scoring breakdown: SOA 50 pts, certifications 30 pts, geography 10 pts, contract value 10 pts.

**API endpoints (BuildingOS Phase 1):**
- `GET /api/bid-profiles` — list all bid profiles
- `POST /api/bid-profiles` — create a bid profile (JSON body: `company_name`, `soa_qualifications`, `certifications_held`, `ateco_codes`, `geographic_regions`, `avg_project_value_eur`, `notes`)
- `POST /api/dossier/analyze` — multipart/form-data: `pdf_file` (required), `notice_id` (optional), `bid_profile_id` (optional). Returns structured analysis + compliance check. Requires Azure OpenAI configured.

## Key constraints

- All writes to `APP_DB` log an `activities` row for audit trail.
- Lead `source_notice_id` is the cross-system primary key linking leads to proposals, dossiers, and outreach.
- OSM worker records must never be presented as vetted partners until `verification_status` is `qualified`.
- Official tender documents require human review before any bid or customer delivery — the system generates first-pass operating dossiers only.

## Refresh operations

```bash
# Fast daily refresh (no OSM):
python3 archagent_production_refresh.py --skip-workers --dossiers 5

# Full weekly refresh (includes OSM):
python3 archagent_production_refresh.py --dossiers 5

# Italy data only:
python3 italy_refresh.py --skip-workers   # skip slow Overpass queries
python3 italy_market_report.py            # regenerate report from existing DB

# Backups:
python3 archagent_backup.py --include-exports --keep 30
```

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
