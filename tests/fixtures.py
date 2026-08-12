#!/usr/bin/env python3
"""Synthetic test fixtures — isolated databases seeded with fabricated records.

The end-to-end tests used to run against whatever happened to be in the developer's
real `archagent_app.sqlite3` / `archagent_actionable_projects.sqlite3`, and asserted on
row counts (">= 400 leads") that only held because a populated production database was
committed to the repository. That database is gone: it carried real procurement leads and
real business contact details, and data is not source.

So the tests now build their own world. `isolate()` points both databases at a fresh temp
directory, and the seeders fill them with obviously-fake, deterministic records. Nothing
here touches a real database, and every threshold the tests assert is a number seeded
right here rather than an accident of history.

Import this *before* importing any `archagent` module, because `core.config` resolves the
database paths at import time.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# How much of each entity the seeders create. Tests assert against these constants
# instead of magic numbers, so changing a fixture size can never silently weaken a test.
LEAD_COUNT = 60
ITALY_LEAD_COUNT = 40          # of LEAD_COUNT, how many are Italian and high-scoring
WORKER_COUNT = 30

_ITALY_TITLES = [
    'Lavori di efficientamento energetico e riqualificazione energetica scuola',
    'Interventi di restauro e messa in sicurezza copertura edificio scolastico',
    'Riqualificazione energetica edilizia residenziale pubblica ERP finanziata PNRR',
    'Progettazione e direzione lavori isolamento facciata asilo comunale',
    'Messa in sicurezza tetto e coibentazione presidio sanitario RSA',
]
_ITALY_CITIES = ['Roma', 'Milano', 'Torino', 'Napoli', 'Bologna']
_OTHER = [
    ('DEU', 'Berlin', 'Sanierung eines Verwaltungsgebaeudes'),
    ('FRA', 'Lyon', "Travaux de renovation d'un batiment public"),
    ('ESP', 'Madrid', 'Obras de rehabilitacion de edificio municipal'),
]


def isolate() -> dict:
    """Point both databases at a fresh temp dir. Returns the env overrides applied."""
    tmp = Path(tempfile.mkdtemp(prefix='archagent-test-'))
    env = {
        'ARCHAGENT_APP_DB': str(tmp / 'app.sqlite3'),
        'ARCHAGENT_LEADS_DB': str(tmp / 'leads.sqlite3'),
    }
    os.environ.update(env)
    return env


def _future(days: int) -> str:
    import datetime as dt
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def seed_leads() -> int:
    """Insert LEAD_COUNT fabricated procurement notices. Returns the count."""
    import json
    from archagent.core.db import LEADS_DB
    from archagent.ingestion.ted import init_db

    con = init_db(LEADS_DB)
    rows = []
    for i in range(LEAD_COUNT):
        if i < ITALY_LEAD_COUNT:
            title = f'{_ITALY_TITLES[i % len(_ITALY_TITLES)]} — lotto {i + 1}'
            city = _ITALY_CITIES[i % len(_ITALY_CITIES)]
            country, category = 'ITA', 'renovation / rehabilitation'
            # Large value + near deadline + PNRR wording push these above the
            # italy_fit_score thresholds the Italy tests assert on.
            value, description = 12_000_000.0, (
                'Intervento di efficientamento energetico finanziato dal PNRR '
                'missione M2C3. Comprende isolamento, facciata, copertura e '
                'riqualificazione energetica dell\'edificio scolastico.'
            )
            deadline = _future(14 + (i % 7))
        else:
            j = i - ITALY_LEAD_COUNT
            country, city, title = _OTHER[j % len(_OTHER)]
            category, value = 'construction / civil works', 750_000.0
            description = 'Public building renovation works.'
            deadline = _future(45 + j)
        notice = f'TEST-{i + 1:04d}-2026'
        rows.append((
            'TEST Fixture Source', notice, title, description,
            f'Fabricated Buyer {i + 1}', country, country, city,
            _future(-30), deadline, value, 'EUR', '45000000', category,
            25, f'https://example.invalid/notice/{notice}',
            json.dumps({'fixture': True, 'index': i}),
        ))
    con.executemany(
        'INSERT OR IGNORE INTO project_leads('
        'source_name, source_notice_id, title, description, buyer_name, buyer_country,'
        'performance_country, performance_city, publication_date, deadline_date,'
        'estimated_value, currency, cpv_codes, category, relevance_score, source_url, raw_json)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        rows,
    )
    con.commit()
    total = con.execute('SELECT COUNT(*) FROM project_leads').fetchone()[0]
    con.close()
    return total


def seed_workers() -> int:
    """Insert WORKER_COUNT fabricated expert/worker listings. Returns the count."""
    import json
    from archagent.core.db import app_conn, now

    trades = ['architecture, design, permits', 'insulation, facade, envelope',
              'roofing, waterproofing', 'HVAC, plumbing, energy', 'painting, finishing']
    con = app_conn()
    rows = []
    for i in range(WORKER_COUNT):
        rows.append((
            'test_fixture', f'fixture/{i + 1}', f'Fabricated Studio {i + 1}',
            'expert' if i % 2 else 'worker', trades[i % len(trades)],
            'Italy', _ITALY_CITIES[i % len(_ITALY_CITIES)], f'{i + 1} Via Esempio',
            41.9 + i / 1000, 12.5 + i / 1000, '', '', 'https://example.invalid',
            '', 'it', 'public_listing_unverified',
            'https://example.invalid/listing', json.dumps({'fixture': True}),
            now(), now(),
        ))
    con.executemany(
        'INSERT OR IGNORE INTO expert_workers('
        'source, source_id, name, worker_type, trades, country, city, address, lat, lon,'
        'phone, email, website, opening_hours, languages, verification_status, source_url,'
        'raw_json, imported_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        rows,
    )
    con.commit()
    total = con.execute('SELECT COUNT(*) FROM expert_workers').fetchone()[0]
    con.close()
    return total


def seed_italy_profiles() -> int:
    """Seed the Italy lead-radar customer profiles. Returns the profile count."""
    from archagent.core.config import APP_DB
    from archagent.core.db import app_conn
    from ops.seed_italy import PROFILES, seed_profiles  # type: ignore

    seed_profiles(APP_DB)
    con = app_conn()
    total = con.execute('SELECT COUNT(*) FROM customer_profiles').fetchone()[0]
    con.close()
    return max(total, len(PROFILES))


def seed_all() -> dict:
    """Initialise both databases and seed everything. Call after isolate()."""
    from archagent.core.db import init_app_db, init_leads_db

    init_app_db()
    init_leads_db()
    return {
        'leads': seed_leads(),
        'workers': seed_workers(),
        'profiles': seed_italy_profiles(),
    }


if __name__ == '__main__':
    isolate()
    print(seed_all())
