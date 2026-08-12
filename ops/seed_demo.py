#!/usr/bin/env python3
"""Seed the databases with fabricated demo data so the app is explorable immediately.

No real data ships with this repository, so a fresh clone starts empty and every screen
looks broken until a TED refresh runs (which needs network and takes minutes). This
seeder fills both databases with invented-but-plausible records in about a second.

**Everything it writes is fabricated.** The buyers, notices, values and partner listings
are made up; the notice IDs are not real TED references. It is for demos, screenshots and
local development — never for anything that informs a real bid.

    python3 ops/seed_demo.py                 # seed
    python3 ops/seed_demo.py --clear         # remove demo rows again
    python3 ops/seed_demo.py --leads 80      # more leads

Demo rows are tagged `source_name='TED Europa (demo)'` / `source='demo_seed'`, so
`--clear` removes exactly what this script created and leaves real data untouched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archagent.core.config import APP_DB, LEADS_DB
from archagent.core.db import app_conn, init_app_db, init_leads_db, now
from archagent.ingestion.ted import init_db

LEAD_SOURCE = 'TED Europa (demo)'
WORKER_SOURCE = 'demo_seed'

BUYERS = [
    'Comune di Valdirosa', 'Comune di Montecastello', 'ATER Provincia di Selvana',
    'Azienda Sanitaria Locale Valpiana', 'Istituto Comprensivo Borgonuovo',
    'Provincia di Terranova', 'Unione dei Comuni della Val Serena',
    'Consorzio Edilizia Pubblica Marenca', 'Comune di Rocca Lenta',
    'Agenzia Regionale Patrimonio Aurelia',
]
CITIES = ['Roma', 'Milano', 'Torino', 'Napoli', 'Bologna', 'Firenze', 'Genova', 'Bari']

TEMPLATES = [
    ("Efficientamento energetico e riqualificazione energetica dell'edificio scolastico",
     'Intervento di efficientamento energetico finanziato dal PNRR missione M2C3: cappotto '
     'termico, sostituzione infissi, impianto termico e fotovoltaico.',
     'energy / HVAC / solar', (2_500_000, 18_000_000)),
    ('Restauro conservativo e messa in sicurezza della copertura',
     'Lavori di restauro, messa in sicurezza e rifacimento della copertura, comprensivi di '
     'isolamento e opere di facciata.',
     'renovation / rehabilitation', (400_000, 4_000_000)),
    ('Riqualificazione energetica di edilizia residenziale pubblica ERP',
     'Riqualificazione energetica di alloggi ERP, efficientamento involucro e impianti, '
     'finanziamento PNRR.',
     'insulation / facade / envelope', (1_200_000, 12_000_000)),
    ('Servizi di progettazione e direzione lavori per adeguamento sismico',
     'Servizi tecnici di progettazione, direzione lavori e coordinamento sicurezza per '
     'adeguamento sismico e messa in sicurezza.',
     'architecture / design', (90_000, 800_000)),
    ('Interventi di edilizia scolastica e messa in sicurezza asilo comunale',
     'Edilizia scolastica: messa in sicurezza, isolamento e riqualificazione energetica '
     "dell'asilo comunale.",
     'renovation / rehabilitation', (300_000, 3_500_000)),
    ('Ristrutturazione presidio sanitario e adeguamento impianti RSA',
     'Ristrutturazione del presidio sanitario, adeguamento impianti termici e messa in '
     'sicurezza della struttura RSA.',
     'energy / HVAC / solar', (800_000, 9_000_000)),
]
NON_IT = [
    ('DEU', 'Berlin', 'Energetische Sanierung eines Schulgebaeudes', 'renovation / rehabilitation'),
    ('FRA', 'Lyon', "Travaux de renovation energetique d'un groupe scolaire", 'energy / HVAC / solar'),
    ('ESP', 'Madrid', 'Obras de rehabilitacion energetica de edificio municipal', 'renovation / rehabilitation'),
    ('NLD', 'Utrecht', 'Renovatie en verduurzaming van een gemeentelijk pand', 'insulation / facade / envelope'),
    ('BEL', 'Gent', 'Renovatie van een openbaar schoolgebouw', 'renovation / rehabilitation'),
]
STUDIOS = [
    ('Studio Tecnico Valdirosa', 'expert', 'architecture, design, permits, planning'),
    ('Coibentazioni Montecastello SRL', 'worker', 'insulation, facade, envelope'),
    ('Impianti Termici Selvana', 'worker', 'HVAC, plumbing, energy'),
    ('Coperture e Lattonerie Borgonuovo', 'worker', 'roofing, waterproofing'),
    ('Ingegneria Strutturale Terranova', 'expert', 'structural, seismic, engineering'),
    ('Restauri Val Serena', 'worker', 'restoration, masonry, finishing'),
    ('Fotovoltaico Marenca', 'worker', 'solar, electrical, energy'),
    ('Progettazione Rocca Lenta', 'expert', 'design, permits, site supervision'),
    ('Serramenti Aurelia', 'worker', 'windows, doors, glazing'),
    ('Edilizia Generale Valpiana', 'worker', 'general contracting, civil works'),
]


def _future(days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def seed_leads(total: int, rng: random.Random) -> int:
    n_it = round(total * 0.73)
    con = init_db(LEADS_DB)
    rows = []
    for i in range(total):
        idx = i + 1
        notice = f'{100000 + idx * 37}-2026'
        if i < n_it:
            title, desc, category, (lo, hi) = TEMPLATES[i % len(TEMPLATES)]
            city = CITIES[i % len(CITIES)]
            # Only some carry PNRR wording, so fit scores spread instead of all maxing out.
            description = desc if i % 3 else desc.replace(
                'finanziato dal PNRR missione M2C3', 'a valere su fondi comunali')
            row = (LEAD_SOURCE, notice, f'{title} — {city} (lotto {i % 4 + 1})', description,
                   BUYERS[i % len(BUYERS)], 'ITA', 'ITA', city,
                   _future(-rng.randrange(5, 40)),
                   _future(rng.choice([4, 9, 12, 16, 20, 27, 34, 41, 55, 68])),
                   float(rng.randrange(lo, hi, 10_000)), 'EUR',
                   rng.choice(['45210000', '45320000', '45331000', '71220000']), category,
                   rng.choice([5, 10, 12, 15, 18, 20, 22, 25]))
        else:
            country, city, title, category = NON_IT[(i - n_it) % len(NON_IT)]
            row = (LEAD_SOURCE, notice, title, 'Public building renovation works.',
                   f'Municipal Authority {city}', country, country, city,
                   _future(-rng.randrange(5, 40)), _future(rng.randrange(20, 80)),
                   float(rng.randrange(200_000, 3_000_000, 10_000)), 'EUR',
                   '45210000', category, rng.choice([3, 5, 8]))
        rows.append(row + (f'https://ted.europa.eu/udl?uri=TED:NOTICE:{notice}',
                           json.dumps({'demo': True})))
    con.executemany(
        'INSERT OR IGNORE INTO project_leads('
        'source_name, source_notice_id, title, description, buyer_name, buyer_country,'
        'performance_country, performance_city, publication_date, deadline_date,'
        'estimated_value, currency, cpv_codes, category, relevance_score, source_url, raw_json)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM project_leads WHERE source_name=?', (LEAD_SOURCE,)).fetchone()[0]
    con.close()
    return n


def seed_workers(rng: random.Random) -> int:
    con = app_conn()
    rows = []
    for i, (name, kind, trades) in enumerate(STUDIOS * 3):
        city = CITIES[i % len(CITIES)]
        suffix = '' if i < len(STUDIOS) else f' — filiale {city}'
        rows.append((
            WORKER_SOURCE, f'demo/node/{9000 + i}', f'{name}{suffix}', kind, trades,
            'Italy', city, f'Via Esempio {i + 1}, {city}', 41.9 + i / 500, 12.5 + i / 500,
            '', '', 'https://example.invalid', '', 'it',
            'qualified' if i % 7 == 0 else 'public_listing_unverified',
            'https://example.invalid/listing', json.dumps({'demo': True}), now(), now(),
        ))
    con.executemany(
        'INSERT OR IGNORE INTO expert_workers('
        'source, source_id, name, worker_type, trades, country, city, address, lat, lon,'
        'phone, email, website, opening_hours, languages, verification_status, source_url,'
        'raw_json, imported_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        rows)
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM expert_workers WHERE source=?', (WORKER_SOURCE,)).fetchone()[0]
    con.close()
    return n


def clear() -> tuple[int, int]:
    lcon = init_db(LEADS_DB)
    leads = lcon.execute('DELETE FROM project_leads WHERE source_name=?', (LEAD_SOURCE,)).rowcount
    lcon.commit(); lcon.close()
    acon = app_conn()
    workers = acon.execute('DELETE FROM expert_workers WHERE source=?', (WORKER_SOURCE,)).rowcount
    acon.commit(); acon.close()
    return leads, workers


def main() -> None:
    ap = argparse.ArgumentParser(description='Seed fabricated demo data (never real data).')
    ap.add_argument('--leads', type=int, default=60, help='how many demo leads (default 60)')
    ap.add_argument('--seed', type=int, default=20260812, help='RNG seed for reproducibility')
    ap.add_argument('--clear', action='store_true', help='delete demo rows and exit')
    args = ap.parse_args()

    init_app_db()
    init_leads_db()

    if args.clear:
        leads, workers = clear()
        print(f'demo data cleared: {leads} leads, {workers} workers removed')
        return

    rng = random.Random(args.seed)
    leads = seed_leads(args.leads, rng)
    workers = seed_workers(rng)
    from ops.seed_italy import seed_profiles
    seed_profiles(APP_DB)
    print(f'demo data seeded: {leads} leads, {workers} partner listings, Italy profiles ready')
    print('All records are fabricated. Start the server and open /app to explore.')


if __name__ == '__main__':
    main()
