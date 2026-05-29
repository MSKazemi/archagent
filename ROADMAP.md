# ArchAgent / BuildingOS — Roadmap

Italian public-works tender intelligence. Pure Python 3 standard library (zero runtime deps).

## Shipped

### Phase 0 — Lead Radar & Italy pipeline
- TED/EU procurement ingestion → canonical leads DB (`project_leads`).
- Italy fit scoring (`intelligence/scoring.py`), bid-readiness dossiers (`generation/dossiers.py`).
- Deterministic, template-based proposals / compliance matrices / outreach (`generation/proposals.py`).
- OSM worker/expert ingestion (unverified until manually promoted).
- Production refresh workflows (`ops/refresh.py`, `ops/refresh_italy.py`), backups, maintenance.

### Phase 1 — AI bid intelligence
- `intelligence/analyst.py` — LLM tender-PDF analysis via Azure OpenAI (`pdftotext` / `pdftoppm` vision fallback, 150-page cap).
- `intelligence/compliance.py` — rule-based compliance scoring (SOA 50 / certs 30 / geo 10 / value 10).
- Async analysis jobs; `POST /api/dossier/analyze`.

### Phase 1.5 — Identity, RBAC, admin plane
- PBKDF2 auth, DB-backed sessions, scoped API keys, login lockout.
- Roles admin/analyst/sales/viewer; route→permission mapping; request funnel (size cap, rate limit, validation, metrics, audit).
- 5-tab admin console; enterprise backup (both DBs) / restore / verify; data export (CSV/JSON/zip); retention purge; DB stats.
- 3D landing page (Three.js hero).

### Phase 2 — Italian procurement intelligence  ✅ (2026-05-29)
- `intelligence/procurement.py` — pure-stdlib, deterministic, **evidence-based** D.Lgs 36/2023 domain logic (sourced from the 24 research sessions).
- **10 new features:** SOA category catalog (OG1–13, OS1–35) + `GET /api/soa-categories`; CIG extraction/validation; SOA-class inference from value; cauzione provvisoria (ISO reductions); cauzione definitiva (ribasso surcharge); document-waterfall generator; D.Lgs 36/2023 checklist; PNRR tagger; deadline/urgency tracker; go/no-go scorer; `POST /api/procurement/analyze-text`.
- **5 improvements:** compliance.py (additive procurement keys, legacy contract preserved); proposals matrix (Italy leads get the real document waterfall with legal citations, not 15 generic rows); proposals (sales pitch/pricing stripped from buyer-facing docs); dossiers.py (procurement-intelligence section); scoring.py (PNRR boost + broader Italian terms).
- Tests: `tests/test_procurement.py` (36 tests).

## Next — Phase 3 candidates (prioritized from research + an internal simulated-expert design review)

High priority (close the credibility gaps the CTO review flagged):
1. **Auto-fetch + auto-analyze** — when a lead is flagged, auto-download TED/ANAC attachments and run the analyst pipeline. No manual PDF upload. *(review finding #1 — not yet done.)*
2. **Real partner verification** — replace the unverified OSM scrape with SOA-qualified, geography-filtered subcontractors; never show out-of-region listings (no Dublin contractors for Italian PNRR work). *(review finding #3.)*
3. **Frontend surfacing of Phase 2** — expose SOA catalog, `analyze-text`, cauzioni, go/no-go, and the document waterfall in the app + admin UI (currently API-only).

Medium priority (deepen the domain):
4. **ANAC / BDNCP integration** — live CIG lookup at `simog.anac.it`/FVOE; historical award + repeat-winner intelligence from `dati.anticorruzione.it` open datasets.
5. **DGUE/ESPD assist** — pre-fill and validate the DGUE structure (Part III director declarations are the most-missed item).
6. **Soccorso istruttorio classifier** — flag whether a given defect is formal (recoverable) vs. substantive (exclusion).
7. **PNRR obligations module** — DNSH clauses, youth/female-quota premiality, reinforced traceability.

Operational / security (deferred from the 2026-05-29 close):
8. **Security hardening** — rotate the dev `admin/admin` credentials; add backup encryption + off-site copy (S3/rsync). Backups are local-disk only today.
9. **Configure a git remote + push** — no `origin` is set; releases are committed/tagged locally only.

## Quality bar
- Pure stdlib; no runtime dependencies. Every procurement rule must cite a source.
- `intelligence/*` domain logic stays I/O-free and deterministic.
- `compliance.py` legacy contract (`gaps`/`matched_requirements`/`bid_readiness_score`) is stable — extend additively.
- Official tender documents always require human review before any bid or customer delivery.

> **Known env note:** `test_smoke` / `test_regression` / `test_admin` spawn a live HTTP server and must run in a normal shell (some sandboxes block socket bind).
