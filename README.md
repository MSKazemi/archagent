# ArchAgent / BuildingOS

Italian and EU public-works tender intelligence: ingest public procurement notices,
score them for fit, and generate bid-readiness dossiers, compliance matrices, and
proposal drafts.

**Pure Python 3 standard library — zero runtime dependencies.** Runs anywhere Python
3.9+ is installed.

## What it does

- **Lead radar** — ingests public procurement notices from the TED/EU Search API.
- **Italy fit scoring** — keyword/term scoring tuned for Italian public works, with a PNRR boost.
- **Italian procurement intelligence** — deterministic encoding of D.Lgs 36/2023: SOA category
  catalog (OG1–13, OS1–35), CIG extraction/validation, SOA class inference, cauzione
  provvisoria/definitiva, document waterfall with legal citations, PNRR tagging, deadline
  urgency, go/no-go scoring. No LLM required.
- **Document generation** — deterministic, template-based bid dossiers, compliance matrices,
  outreach packs, CRM follow-ups, and building audits.
- **AI tender analysis (optional)** — LLM analysis of tender PDFs via Azure OpenAI, with a
  `pdftotext` / `pdftoppm`-vision fallback for scanned documents.
- **Identity, RBAC and an admin plane** — PBKDF2 auth, DB-backed sessions, scoped API keys,
  role-based route permissions, rate limiting, audit trail, backup/restore, data export,
  retention purge — all stdlib.

## No data ships with this repository

The SQLite databases are **runtime data, not source**, and are deliberately not committed:
they hold real procurement leads, business contact details, and credentials. A fresh clone
creates empty databases on first run; populate them yourself with `ops/refresh.py`.

The same applies to `exports/`, `backups/`, and `data/` — all generated, all gitignored.

## Quick start

```bash
git clone <your-fork-url> archagent && cd archagent
cp .gitignore.example .gitignore   # keeps databases, exports and .env out of git
cp .env.example .env               # then edit: set a long ARCHAGENT_TOKEN
python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Open `http://127.0.0.1:8091/app` (or `/admin` for the admin console, `/` for the landing page).
`.env` is loaded automatically at startup.

Populate the lead database:

```bash
python3 ops/refresh_italy.py --skip-workers   # TED leads + profiles + market report
python3 ops/refresh.py --skip-workers --dossiers 5   # backup → refresh → dossiers → tests
```

Docker:

```bash
cp .env.example .env
export ARCHAGENT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
docker compose up --build
```

## Authentication

Two mechanisms, both optional for purely local use:

- **User accounts** — set `ARCHAGENT_ADMIN_EMAIL` / `ARCHAGENT_ADMIN_PASSWORD` to seed the
  first admin on first run, then `POST /api/auth/login` to get an `archagent_session` cookie.
  Manage further users from the admin console.
- **Legacy token** — `ARCHAGENT_TOKEN` authenticates as an admin-equivalent principal via
  `X-ArchAgent-Token: <token>` or `Authorization: Bearer <token>`.

`GET /api/health` is always public. With no users and no token configured, a local
deployment falls back to an implicit admin for development convenience — never expose such
a deployment to a network.

**Do not expose this app publicly without** `ARCHAGENT_TOKEN` or seeded user accounts, HTTPS,
backups, and a process supervisor. See `docs/DEPLOYMENT.md` for systemd, timer, and reverse
proxy examples.

## Layout

```
archagent/
├── core/          config, db, migrations, passwords, rbac, auth_db, sessions, audit,
│                  backup, maintenance
├── ingestion/     ted.py (TED EU procurement), osm.py (OpenStreetMap Overpass)
├── intelligence/  analyst.py (PDF/LLM), compliance.py, procurement.py (D.Lgs 36/2023),
│                  scoring.py (Italy fit)
├── generation/    proposals.py, dossiers.py
├── markets/italy/ refresh.py, report.py
└── api/           server.py, auth, errors, validation, ratelimit, metrics, pagination,
                   handlers/ (one module per route group)

archagent_server.py   entry-point shim
ops/                  refresh, backup, export, maintenance, healthcheck, seeding
frontend/             index.html (landing), app.html (portal), admin.html (admin console)
deploy/               systemd service + timer units
docs/                 deployment guide and reference notes
tests/                self-contained test scripts
```

## Tests

Each script is self-contained and prints a single `PASS ...` line on success.

```bash
python3 tests/test_db.py           # schema creation, WAL mode
python3 tests/test_admin.py        # identity/RBAC + admin plane (isolated temp DB)
python3 tests/test_admin_data.py   # export/backup/restore plane
python3 tests/test_procurement.py  # Italian procurement domain logic
python3 tests/test_analyst.py      # analyst + compliance (mocked; integration needs Azure)
python3 tests/test_maintenance.py  # maintenance helpers
python3 tests/test_smoke.py        # end-to-end API test (starts a server on port 8092)
python3 tests/test_regression.py   # auth, importer, customer profiles, radar exports
python3 tests/test_italy.py        # Italy scoring, dossiers, summary, worker verification
python3 ops/maintenance.py         # SQLite integrity check
```

`test_smoke.py`, `test_regression.py`, and `test_italy.py` exercise a live server against the
local databases; run a refresh first if you want them to see real leads.

## Configuration

See `.env.example` for the full, commented list. The essentials:

| Variable | Purpose |
|---|---|
| `ARCHAGENT_TOKEN` | Legacy admin-equivalent API token |
| `ARCHAGENT_ADMIN_EMAIL` / `_PASSWORD` | Seeds the first admin user on first run |
| `ARCHAGENT_APP_DB` / `ARCHAGENT_LEADS_DB` | Database path overrides |
| `AZURE_OPENAI_*` | Required only for `POST /api/dossier/analyze` |
| `SMTP_*` / `NOTIFY_EMAIL` | Optional email alerts for pilot requests |

## Data provenance and limits

- Procurement notices come from the **TED EU Search API**; see the
  [TED legal notice](https://ted.europa.eu/en/legal-notice) for reuse terms.
- Expert/worker listings come from **OpenStreetMap** via Overpass and are imported as
  `public_listing_unverified`. They are public business listings, **not vetted partners**,
  and must never be presented as qualified until contacted and manually promoted. OSM data
  is © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright).
- Generated dossiers are **first-pass operating documents**. Official tender documents
  require human review before any bid or client delivery.

## Author

Mohsen Seyedkazemi Ardebili — [mskazemi.github.io](https://mskazemi.github.io) ·
[github.com/MSKazemi](https://github.com/MSKazemi)
