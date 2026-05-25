# ArchAgent / BuildingOS

Production-oriented local MVP for architecture, renovation, construction, painting, facade, insulation, HVAC, energy retrofit, proposal generation, and contractor/expert matching workflows.

## Current status

This repository contains a working API-backed MVP:

- Real public procurement lead radar from TED/EU data.
- SQLite lead and application databases.
- Customer/founder web app at `/app`.
- Proposal, compliance matrix, dossier, outreach, CRM follow-up, and building audit generation.
- Public unverified expert/worker/supplier listings from OpenStreetMap/Overpass.
- Token-protected API mode for private hosted deployments.

## Canonical files

- `archagent_server.py` — local HTTP API and app server.
- `app.html` — API-backed BuildingOS portal.
- `index.html` — marketing website.
- `proposal_engine.py` — deterministic proposal/compliance/audit logic.
- `project_finder.py` — TED lead ingestion.
- `expert_worker_importer.py` — OSM/Overpass expert-worker importer.
- `italy_refresh.py` — repeatable Italy refresh: TED leads, OSM listings, seeded profiles, report.
- `tender_document_collector.py` — Italy fit scoring and bid-readiness dossier generation.
- `archagent_production_refresh.py` — backup + refresh + top dossier generation + regression wrapper.
- `archagent_backup.py` — safe timestamped SQLite/export backups.
- `DEPLOYMENT.md` — systemd, backups, HTTPS, and refresh operations guide.
- `italy_market_report.py` — generates `ITALY_MARKET_REPORT.md` from local public-data DBs.
- `seed_italy_profiles.py` — seeds Italy-specific lead-radar customer profiles.
- `smoke_test.py` — main end-to-end smoke test.
- `production_regression_test.py` — production hardening regression test.
- `production_italy_test.py` — Italy scoring/dossier/verification regression test.

## Canonical databases

- `archagent_actionable_projects.sqlite3` is the canonical lead database used by the app.
- `archagent_app.sqlite3` is the canonical application database for prospects, proposals, customer profiles, exports, workers, contractors, followups, and activities.
- `archagent_projects.sqlite3` is legacy/parallel project-finder output and should not be used by new app code unless explicitly migrated.

## Run locally

```bash
cd /opt/archagent
python3 archagent_server.py --port 8091
```

Open:

```text
http://127.0.0.1:8091/app
```

## Run with private API token

```bash
cd /opt/archagent
ARCHAGENT_TOKEN='replace-with-long-random-token' python3 archagent_server.py --port 8091
```

The browser app will prompt once for the token and stores it in `localStorage` as `archagent-token`.

API clients can pass either:

```text
X-ArchAgent-Token: replace-with-long-random-token
Authorization: Bearer replace-with-long-random-token
```

Health check remains public:

```bash
curl http://127.0.0.1:8091/api/health
```

## Test

```bash
cd /opt/archagent
python3 smoke_test.py
python3 production_regression_test.py
python3 production_italy_test.py
```

Expected:

```text
PASS smoke: leads, proposal, compliance, export, prospect, follow-up, match, outreach, dossier, workers, audit
PASS production regression: importer help, auth, customer profiles, lead radar exports
PASS production Italy workflows: scoring, dossiers, summary, worker verification
```

## Refresh project leads

```bash
python3 project_finder.py --limit-per-search 50 --report-limit 50
```

This updates `archagent_actionable_projects.sqlite3`, `ACTIONABLE_PROJECTS.md`, `actionable_projects.csv`, and `actionable_projects.json`.

## Refresh Italy market data

```bash
python3 italy_refresh.py
```

This refreshes Italy-focused TED leads, imports public OSM expert/worker/supplier listings for Rome, Milan, Turin, Naples, and Bologna, seeds Italy customer profiles, and regenerates `ITALY_MARKET_REPORT.md`.

Faster variants:

```bash
python3 italy_refresh.py --skip-workers
python3 italy_refresh.py --skip-tenders
python3 italy_market_report.py
python3 seed_italy_profiles.py
python3 tender_document_collector.py --limit 5 --min-score 60
python3 archagent_production_refresh.py --skip-workers --dossiers 5
```

## Import public expert/worker listings

List configured areas:

```bash
python3 expert_worker_importer.py --list-areas
```

Dry-run one city without writing the database:

```bash
python3 expert_worker_importer.py --areas Berlin --dry-run --sleep 0
```

Full import:

```bash
python3 expert_worker_importer.py
```

Important: OpenStreetMap/Overpass records are public unverified listings, not vetted partners. Do not claim they are qualified until contacted and verified.

## Main API endpoints

```text
GET  /api/health
GET  /api/stats
GET  /api/italy/summary
GET  /api/leads?q=&country=&category=&min_value=&sort=&limit=&offset=
GET  /api/lead?id=<source_notice_id>
GET  /api/customer-profiles
POST /api/customer-profiles
GET  /api/lead-radar/export?profile_id=&country=&category=&format=markdown|csv&limit=
GET  /api/lead-radar/exports
GET  /api/prospects
POST /api/prospects
GET  /api/followups
POST /api/followups
GET  /api/proposals
POST /api/proposals
POST /api/proposals/hermes
GET  /api/proposals/export?id=<proposal_id>
GET  /api/tender-dossiers
POST /api/tender-dossiers
POST /api/compliance
POST /api/dossier
GET  /api/contractors
GET  /api/workers?q=&country=&type=&trade=&verification_status=&limit=&offset=
GET  /api/workers/export?country=&type=&trade=&verification_status=&limit=&format=csv
GET  /api/worker-stats
GET  /api/worker-verifications
POST /api/workers/verify
POST /api/match
POST /api/outreach
POST /api/audit
GET  /api/activities
```

## First commercial wedge

Start with a narrow paid workflow:

1. Customer profile: country, category, trade, capacity, notes.
2. Weekly lead radar export: 10–30 relevant active opportunities.
3. Bid package upsell: compliance matrix, proposal draft, partner outreach.
4. Contractor verification: mark listings contacted/replied/qualified before matchmaking claims.

Do not expose the app publicly without `ARCHAGENT_TOKEN`, HTTPS, backups, and a deployment supervisor. See `DEPLOYMENT.md` for systemd/timer/Caddy examples.
