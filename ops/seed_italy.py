#!/usr/bin/env python3
"""Seed Italy-specific customer profiles for ArchAgent lead-radar demos/pilots."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archagent.core.config import APP_DB
from archagent.core.db import init_app_db, now

PROFILES = [
    {
        'name': 'Italy energy retrofit / ESCO public buildings',
        'company': 'ArchAgent Italy Pilot — Energy Retrofit',
        'email': 'pilot+italy-energy@example.com',
        'countries': 'ITA',
        'categories': 'energy / HVAC / solar',
        'trades': 'efficientamento energetico, riqualificazione energetica, HVAC, impianti termici, ERP, PPP, ESCO',
        'min_value': 500000,
        'max_leads': 25,
        'status': 'pilot',
        'notes': 'Best first Italy wedge: public housing, schools, municipal buildings, PPP/finanza di progetto, energy-efficiency concessions.',
    },
    {
        'name': 'Italy restoration / roof / safety works',
        'company': 'ArchAgent Italy Pilot — Restoration',
        'email': 'pilot+italy-restoration@example.com',
        'countries': 'ITA',
        'categories': 'renovation / rehabilitation, insulation / facade / envelope, construction / building works',
        'trades': 'restauro, messa in sicurezza, copertura, tetto, facciata, isolamento, serramenti, edilizia storica',
        'min_value': 250000,
        'max_leads': 25,
        'status': 'pilot',
        'notes': 'Secondary Italy wedge for architects and specialist contractors: restoration, roof/safety, envelope works, historic/public buildings.',
    },
    {
        'name': 'Italy architecture / technical design services',
        'company': 'ArchAgent Italy Pilot — Architecture Services',
        'email': 'pilot+italy-architecture@example.com',
        'countries': 'ITA',
        'categories': 'architecture / design, renovation / rehabilitation, construction / civil works',
        'trades': 'progettazione, direzione lavori, coordinamento sicurezza, fattibilità tecnico economica, computo metrico, BIM',
        'min_value': 100000,
        'max_leads': 25,
        'status': 'pilot',
        'notes': 'For design studios and engineering firms: service tenders tied to public-building renovation and technical design.',
    },
]


def seed_profiles(db_path: Path = APP_DB) -> int:
    init_app_db()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    inserted = 0
    for profile in PROFILES:
        existing = con.execute('SELECT id FROM customer_profiles WHERE name=?', (profile['name'],)).fetchone()
        values = (
            profile['company'],
            profile['email'],
            profile['countries'],
            profile['categories'],
            profile['trades'],
            profile['min_value'],
            profile['max_leads'],
            profile['status'],
            profile['notes'],
            now(),
        )
        if existing:
            con.execute(
                'UPDATE customer_profiles SET company=?,email=?,countries=?,categories=?,trades=?,min_value=?,max_leads=?,status=?,notes=?,updated_at=? WHERE name=?',
                values + (profile['name'],),
            )
        else:
            con.execute(
                'INSERT INTO customer_profiles(name,company,email,countries,categories,trades,min_value,max_leads,status,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (profile['name'],) + values + (now(),),
            )
            inserted += 1
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM customer_profiles WHERE countries LIKE '%ITA%'").fetchone()[0]
    con.close()
    print(f'seeded_or_updated={len(PROFILES)} inserted={inserted} italy_profiles_total={total}')
    return 0


if __name__ == '__main__':
    raise SystemExit(seed_profiles())
