# ArchAgent Actionable Project Database

Created: 2026-05-24

## Purpose

This database is for project leads that can still be applied to. It excludes old historical notices and excludes TED notices without a clear tender deadline.

## Main files

- Database: `/opt/archagent/archagent_actionable_projects.sqlite3`
- Importer: `/opt/archagent/project_finder.py`
- Markdown report: `/opt/archagent/ACTIONABLE_PROJECTS.md`
- CSV export: `/opt/archagent/actionable_projects.csv`
- JSON export: `/opt/archagent/actionable_projects.json`

## Current filter

The importer keeps only notices that match all of these conditions:

1. Source is the official TED Search API.
2. Search scope is `ACTIVE`.
3. Tender deadline is on or after the current date.
4. Deadline is present and parseable.
5. Country is Europe-focused: EU/EEA plus UK, Switzerland, and nearby TED countries.
6. Notice matches building-related construction, renovation, insulation, HVAC/energy/solar, painting, or architecture/design signals.

Current date used during verification: `2026-05-24`.

Verification result:

- Total actionable leads: 425
- Missing deadline: 0
- Expired deadline: 0
- Earliest deadline: 2026-05-25
- Latest deadline: 2026-12-31

## How to refresh data

Run:

```bash
python3 /opt/archagent/project_finder.py --limit-per-search 100 --report-limit 50
```

This refreshes:

- `archagent_actionable_projects.sqlite3`
- `ACTIONABLE_PROJECTS.md`
- `actionable_projects.csv`
- `actionable_projects.json`

## How to inspect the database

Example SQLite query:

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/opt/archagent/archagent_actionable_projects.sqlite3')
conn.row_factory = sqlite3.Row
for row in conn.execute('''
    SELECT relevance_score, publication_date, deadline_date, performance_country,
           performance_city, category, title, source_url
    FROM project_leads
    ORDER BY relevance_score DESC, deadline_date ASC
    LIMIT 20
'''):
    print(dict(row))
PY
```

## Next build step

Connect `archagent_actionable_projects.sqlite3` to `dashboard.html` so the EU Radar screen displays real actionable tenders instead of demo cards.
