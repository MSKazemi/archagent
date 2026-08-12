# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

Pure Python 3 standard library only — no dependencies, no pip install. Runs anywhere Python 3.9+ is installed.

**BuildingOS Phase 1** adds `archagent/intelligence/analyst.py` and `archagent/intelligence/compliance.py`, which require external tools (`pdftotext`, `pdftoppm`) and Azure OpenAI. These modules are optional — all other functionality works without them.

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
python3 tests/test_smoke.py            # end-to-end API test (starts server on port 8092)
python3 tests/test_regression.py       # auth, importer, customer profiles, lead radar exports
python3 tests/test_italy.py            # Italy scoring, dossiers, summary, worker verification
python3 tests/test_db.py               # DB schema creation and WAL mode unit tests
python3 tests/test_admin.py            # identity/RBAC + admin plane (unit + e2e, isolated temp DB)
python3 ops/maintenance.py             # SQLite integrity check
python3 tests/test_analyst.py          # analyst + compliance unit tests (mocked); integration runs only when AZURE_OPENAI_KEY is set
python3 tests/test_procurement.py      # Italian procurement domain logic (SOA, CIG, cauzioni, doc waterfall, PNRR, go/no-go) + handlers
```

`tests/test_smoke.py` authenticates via the legacy `X-ArchAgent-Token` header (forcing a known token into the spawned server), so it passes whether or not the app DB has users. (Previously it relied on the no-users implicit-admin path, which any seeded admin user disables.)

Each test script is self-contained and prints a single `PASS ...` line on success.

## Architecture

**Data layer — two SQLite databases:**
- `archagent_actionable_projects.sqlite3` — read-mostly lead database (`project_leads` table), fed by TED/EU procurement ingestion. This is the canonical leads DB.
- `archagent_app.sqlite3` — read-write app database holding prospects, proposals, customer profiles, follow-ups, activities, workers, verifications, exports, dossiers, and bid profiles. Initialized with seed contractors on first run by `init_app_db()`, which then runs `run_migrations()` and `bootstrap_admin()`.
  - `bid_profiles` table — company bid qualification profiles; SOA qualifications stored as `[{category, classification}]` JSON.
  - Identity/ops tables (via migrations): `users`, `sessions`, `api_keys`, `login_attempts`, `schema_migrations`, plus `error_log`, `backups`, `feature_flags`, `settings`. `activities` gains `actor_user_id`/`actor_type`/`actor_ip`; mutable entities gain `deleted_at` (soft delete).
- `archagent_projects.sqlite3` — legacy parallel output from `project_finder.py`; not used by new code.

**Package structure (`archagent/`):**
```
archagent/
├── core/          config.py, db.py, maintenance.py, backup.py
│                  + migrations.py, passwords.py, rbac.py, auth_db.py, sessions.py, audit.py
├── ingestion/     ted.py (TED EU), osm.py (OpenStreetMap)
├── intelligence/  analyst.py (PDF/LLM), compliance.py, scoring.py (Italy fit)
├── generation/    proposals.py (templates), dossiers.py (Italy dossiers)
├── markets/italy/ refresh.py, report.py
└── api/           server.py (HTTP funnel), auth.py, errors.py, validation.py,
                   ratelimit.py, metrics.py, pagination.py,
                   handlers/ (one module per route group + admin_* modules)
```

**Identity, RBAC & request hardening (pure stdlib, zero deps):**
- `core/migrations.py` — versioned schema migrations (`schema_migrations` table). Runs after `init_app_db()`. FK enforcement (`PRAGMA foreign_keys=ON`) applies to the new auth tables only; existing tables get indexes, not retrofitted FK clauses.
- `core/passwords.py` — PBKDF2-HMAC-SHA256 (240k iters) with per-user salt; constant-time verify.
- `core/rbac.py` — roles `admin`/`analyst`/`sales`/`viewer`; `admin` is a wildcard. `permission_for_route(method, path)` maps routes to `resource:verb` permissions.
- `core/auth_db.py` — users + scoped API keys, `bootstrap_admin()` (from `ARCHAGENT_ADMIN_EMAIL`/`_PASSWORD` on first run), `authenticate_user()` with login-attempt lockout (5 failures / 15 min).
- `core/sessions.py` — DB-backed sessions (cookie `archagent_session`, only the SHA-256 hash stored); survive restart, support revocation.
- `core/audit.py` — `log_activity(con, kind, message, payload, principal)`; every write path attributes the actor in `activities`.
- `api/server.py` request funnel (`Handler._dispatch`): parse → size cap (413) → resolve `Principal` → rate limit (429 + Retry-After) → permission (401/403) → validate → route → respond → record metrics. Legacy `X-ArchAgent-Token`/Bearer still authenticates as an admin-equivalent principal while `ARCHAGENT_LEGACY_TOKEN_ENABLED=1`. Unauthenticated local dev with no users → implicit admin.
- `api/errors.py` (stable error-code taxonomy + request-id), `validation.py` (`validate(payload, spec)`), `ratelimit.py` (token-bucket tiers: default/write/expensive), `metrics.py` (in-process p50/p95/p99 + recent errors), `pagination.py` (`list_table` → `{items,total,limit,offset}`).
- All `/api/...` routes are also reachable under `/api/v1/...` (aliases). List endpoints now return the pagination envelope additively (existing `.items` readers unaffected).

**Server (`archagent_server.py`):**
Thin entry-point shim. HTTP logic lives in `archagent/api/server.py` + `archagent/api/handlers/`. Routes dispatch by `parsed.path` string matching. Auth is opt-in via `ARCHAGENT_TOKEN`; `/api/health` is always public. Static files served from `frontend/` and `exports/`.

**Document generation (`archagent/generation/proposals.py`):**
Deterministic, template-based. Generates proposals, compliance matrices, CRM follow-ups, outreach packs, and building audits. No LLM by default; `hermes` CLI integration gated by `HERMES_PROPOSAL_ENABLED=1`.

**Lead ingestion (`archagent/ingestion/ted.py`):**
Fetches TED EU procurement notices by keyword/CPV search. Writes to `archagent_actionable_projects.sqlite3`.

**Italy pipeline:**
- `archagent/intelligence/scoring.py` — scores Italy fit using keyword term-matching
- `archagent/generation/dossiers.py` — generates bid-readiness dossiers saved to `exports/`
- `ops/refresh_italy.py` — orchestrates full Italy data refresh: TED leads + OSM workers + seed profiles + market report
- `ops/refresh.py` — production wrapper: backup → italy_refresh → dossiers → tests → maintenance

**Worker/expert data (`archagent/ingestion/osm.py`):**
Queries OpenStreetMap Overpass API for construction/architecture businesses. All imported records are `verification_status = public_listing_unverified` until manually promoted via `POST /api/workers/verify`.

**Tender analysis — BuildingOS Phase 1:**
- `archagent/intelligence/analyst.py` — LLM tender PDF analysis via Azure OpenAI. Entry point: `analyze_tender(pdf_bytes, notice_id='')`. Returns `{status, extracted_json, cost_estimate_eur, pages_analyzed, is_truncated, is_scanned, warnings, error}`. Uses `pdftotext` for digital PDFs; falls back to `pdftoppm` + gpt-4o vision for scanned ones. Hard cap: 150 pages.
- `archagent/intelligence/compliance.py` — Rule-based bid compliance matching. Entry point: `check_compliance(extracted_json, bid_profile_row)`. Scoring breakdown: SOA 50 pts, certifications 30 pts, geography 10 pts, contract value 10 pts. Additively returns Phase-2 procurement intelligence (`cig`, `inferred_soa_class`, `cauzione_provvisoria`, `pnrr`, `deadline_status`, `go_no_go`, `document_waterfall`, `dlgs36_checklist`) without changing the legacy `gaps`/`matched_requirements`/`bid_readiness_score` contract.

**Italian procurement domain — BuildingOS Phase 2 (`archagent/intelligence/procurement.py`):**
Pure-stdlib, deterministic, evidence-based encoding of D.Lgs 36/2023 (sourced in `.claude/plans/research/`). No I/O, no LLM. Primitives: `SOA_CATEGORIES` (OG1–13, OS1–35) + `soa_category_catalog()`; `extract_cig`/`is_valid_cig` (10-char alphanumeric, required >€40K); `infer_soa_class(value)` (value→classifica, None below €150K); `cauzione_provvisoria(base, certs)` (2% with ISO 9001 −50%/ISO 14001 −20%/SA8000 −10%/ISO 45001 −5%, capped −80%); `cauzione_definitiva(base, ribasso)` (10% + 2pt/ribasso-point over 10%, capped 30%); `required_documents(extracted)` (DGUE→DURC→PASSOE→SOA→cauzioni→antimafia→CCIAA→bilanci waterfall); `dlgs36_checklist`; `detect_pnrr(text)`; `deadline_status(deadline, today)`; `go_no_go(score, deadline, value_match)`. Wired into `compliance.py`, `proposals.generate_compliance_matrix` (Italy leads now get the real document waterfall with legal citations instead of generic rows), and `dossiers.py` (procurement-intelligence section).

**API endpoints (BuildingOS Phase 1):**
- `GET /api/bid-profiles` — list all bid profiles
- `POST /api/bid-profiles` — create a bid profile (JSON body: `company_name`, `soa_qualifications`, `certifications_held`, `ateco_codes`, `geographic_regions`, `avg_project_value_eur`, `notes`)
- `POST /api/dossier/analyze` — multipart/form-data: `pdf_file` (required), `notice_id` (optional), `bid_profile_id` (optional). Returns structured analysis + compliance check. Requires Azure OpenAI configured.
- `GET /api/soa-categories` — full SOA category catalog (OG/OS) + classification value bands. Reference data, no deps.
- `POST /api/procurement/analyze-text` — deterministic Italian procurement analysis of free text + hints (`text`, `value_eur`, `deadline_date`, `certifications[]`, `ribasso_pct`). Returns CIG, inferred SOA class, cauzioni, PNRR signals, deadline urgency, document waterfall, D.Lgs 36/2023 checklist, go/no-go. No LLM, no Azure required.

**Auth & admin plane endpoints:**
- `POST /api/auth/login` — `{email, password}` → sets `archagent_session` cookie. Public. `POST/GET /api/auth/logout` revokes it.
- `GET /api/admin/me` — current principal (role + permissions). Admin console reads this to gate the UI.
- `GET/PATCH/DELETE /api/admin/resources/{name}[/{id}]`, `POST .../{id}/restore`, `POST .../{name}/bulk` — generic CRUD over a registry (prospects, proposals, customer_profiles, bid_profiles, expert_workers, tender_dossiers, contractors) with search/filter/sort/pagination and soft-delete. Perm `resources:read|write`.
- `GET /api/admin/ops/{metrics|errors|health|jobs|db-stats|retention}`, `POST /api/admin/ops/jobs/{id}/{retry|cancel}`, `POST /api/admin/ops/retention/purge` — observability + job control + DB statistics (per-table row/soft-delete counts, file+WAL sizes) + data-retention preview/purge (purges soft-deleted rows and `activities`/`error_log`/`login_attempts` older than `days`; `{confirm:true}` required). Perm `ops:read|write`.
- `GET /api/admin/export`, `GET /api/admin/export/{db}/{table}?format=csv|json`, `POST /api/admin/export/all`, `GET /api/admin/export/download?name=...` — data export plane (`admin_data.py`): list exportable tables (both DBs) with row counts; export one allow-listed table to CSV/JSON; export everything as a zip of CSVs + `manifest.json`; download a generated export. Secret columns (password hashes) are redacted; auth-secret tables excluded. Perm `ops:read|write`.
- `GET /api/admin/audit/{activities|logins|history}`, `GET /api/admin/audit/gdpr/export`, `POST /api/admin/audit/gdpr/{preview|erase}` — attributed audit, login log, per-entity change history, GDPR subject export/anonymize. Perm `audit:read` / `compliance:export|erase`.
- `GET/POST /api/admin/security/{api-keys|users|backups}`, `POST .../api-keys/{id}/rotate`, `DELETE .../{api-keys|sessions}/{id}`, `PATCH .../users/{id}`, `GET .../sessions`, `GET/PATCH .../rate-limit`, `GET .../feature-flags`, `PATCH .../feature-flags/{key}`, `GET .../config-status` — keys (secret shown once), users, sessions, backups, flags, redacted config. Admin only.
- Backups (`admin_data.py`): `POST .../security/backups` snapshots **both** databases (app + leads) via the SQLite online backup API, runs `PRAGMA integrity_check`, records one `backups` row per DB (with `db_name`/`kind`/`verified`), and prunes to keep N. `POST .../security/backups/{id}/verify` re-checks integrity; `POST .../security/backups/{id}/restore` (`{confirm:true}`) takes a pre-restore safety backup then atomically replaces the live DB file; `GET .../security/backups/{id}/download` streams the file. The `backups` table gained `db_name|kind|verified|note` in migration m9.

The admin console (`frontend/admin.html`, served at `/admin`) is a 6-tab UI (Overview, Data, Ops, Export, Audit, Security) consuming the above via session-cookie auth. The Ops tab adds DB statistics + retention; Export is the data-export center; Security adds backup download/verify/restore, feature-flag editing, and rate-limit view.

Scheduled backups: `deploy/archagent-backup.{service,timer}` run `ops/backup.py --include-exports --keep 30` daily (install alongside the existing `archagent-refresh` timer).

## Key constraints

- All writes to `APP_DB` log an `activities` row for audit trail.
- Lead `source_notice_id` is the cross-system primary key linking leads to proposals, dossiers, and outreach.
- OSM worker records must never be presented as vetted partners until `verification_status` is `qualified`.
- Official tender documents require human review before any bid or customer delivery — the system generates first-pass operating dossiers only.

## Refresh operations

```bash
# Fast daily refresh (no OSM):
python3 ops/refresh.py --skip-workers --dossiers 5

# Full weekly refresh (includes OSM):
python3 ops/refresh.py --dossiers 5

# Italy data only:
python3 ops/refresh_italy.py --skip-workers   # skip slow Overpass queries
python3 -m archagent.markets.italy.report     # regenerate report from existing DB

# Backups:
python3 ops/backup.py --include-exports --keep 30

# Fabricated demo data for screenshots / local exploration (no network):
python3 ops/seed_demo.py
python3 ops/seed_demo.py --clear

# Full data export (zip of every table, both DBs) → exports/:
python3 ops/export.py
# Export + purge old records (destructive, explicit opt-in):
python3 ops/export.py --purge --days 365 --confirm
```

Scheduled units in `deploy/`: `archagent-backup.{service,timer}` (daily 02:00),
`archagent-export.{service,timer}` (weekly Mon 03:00, export only — never purges),
`archagent-refresh.{service,timer}` (daily 06:30).

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
