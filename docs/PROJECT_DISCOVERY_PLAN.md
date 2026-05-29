# Real Project Discovery Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build ArchAgent's real project discovery pipeline, starting with public European procurement notices and then expanding to permits, grants, and private/local leads.

**Architecture:** Use a small SQLite database first so the product can store and review real leads immediately. Source adapters ingest notices from public APIs, normalize them into `project_leads`, classify job type, score relevance, and export reports for review. Later this can move to PostgreSQL with scheduled workers and an admin UI.

**Tech Stack:** Python standard library, SQLite, TED Search API, Markdown/CSV/JSON exports.

---

## Task 1: Create first project lead database

**Objective:** Store real project opportunities in a structured local database.

**Files:**
- Create: `/opt/archagent/archagent_projects.sqlite3`
- Create: `/opt/archagent/project_finder.py`

**Schema:**
- `sources`: public data source registry.
- `project_leads`: normalized project lead table with title, description, buyer, country, city, publication date, deadline, value, CPV codes, category, relevance score, source URL, and raw JSON.

**Verification:**
Run:
```bash
python3 /opt/archagent/project_finder.py --init-only
```
Expected: SQLite database exists.

## Task 2: Connect TED Search API

**Objective:** Fetch real public procurement notices from Tenders Electronic Daily.

**Source:**
- API docs: `https://ted.europa.eu/api/documentation/index.html`
- Endpoint: `https://api.ted.europa.eu/v3/notices/search`

**Queries:**
- Recent construction CPV notices: `classification-cpv=45*`
- Recent renovation keywords.
- Recent insulation/facade/window keywords.
- Recent HVAC/energy/solar keywords.
- Recent painting/finishing keywords.

**Verification:**
Run:
```bash
python3 /opt/archagent/project_finder.py --limit-per-search 30 --report-limit 20
```
Expected: database populated and report generated.

## Task 3: Export human-readable project reports

**Objective:** Let the founder review leads quickly without a backend UI yet.

**Files:**
- `/opt/archagent/FOUND_PROJECTS.md`
- `/opt/archagent/found_projects.csv`
- `/opt/archagent/found_projects.json`

**Verification:**
Open the markdown report or CSV and confirm real TED links appear.

## Task 4: Improve relevance scoring

**Objective:** Rank opportunities better for ArchAgent's business model.

**Scoring signals:**
- Building/renovation/energy/painting category match.
- Deadline still open.
- Location in target market.
- Estimated value available.
- Buyer type: municipality, school, housing, hospital, property portfolio.
- Required trades: architect, energy auditor, HVAC, insulation, painter, roofer, facade worker, contractor.

## Task 5: Add next real sources

**Objective:** Expand beyond TED.

**Priority sources:**
1. One target country's national procurement portal.
2. One city building permit/planning portal.
3. One national energy grant or EPC retrofit program.
4. Private/local job intake form from the ArchAgent website.

## Task 6: Move from local prototype to app backend

**Objective:** Convert the local prototype into a real product backend.

**Recommended next stack:**
- PostgreSQL for production database.
- FastAPI or Node/Next.js API routes for backend.
- Scheduled ingestion workers.
- Admin dashboard for reviewing, scoring, assigning, and contacting leads.
- Expert/worker profiles and quote-request workflow.
