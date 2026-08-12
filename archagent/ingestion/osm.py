#!/usr/bin/env python3
"""Import real public expert/worker/company listings for BuildingOS.

Source: OpenStreetMap via Overpass API. These are public business listings,
not verified partners. Do not invent missing contact details.
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
# Allow running as a script: add project root to sys.path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import argparse
import csv
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from archagent.core.config import BASE
from archagent.core.config import APP_DB
JSON_OUT = BASE / 'expert_workers_osm.json'
CSV_OUT = BASE / 'expert_workers_osm.csv'
MD_OUT = BASE / 'EXPERT_WORKERS_REPORT.md'
OVERPASS = 'https://overpass-api.de/api/interpreter'

AREAS = [
    ('Germany', 'Berlin', 52.33, 13.05, 52.68, 13.77),
    ('Germany', 'Munich', 48.02, 11.36, 48.25, 11.75),
    ('Germany', 'Hamburg', 53.39, 9.73, 53.75, 10.33),
    ('France', 'Paris', 48.75, 2.18, 48.95, 2.55),
    ('France', 'Lyon', 45.67, 4.73, 45.84, 4.98),
    ('Italy', 'Rome', 41.78, 12.34, 42.02, 12.73),
    ('Italy', 'Milan', 45.38, 9.04, 45.55, 9.32),
    ('Italy', 'Turin', 45.00, 7.55, 45.14, 7.78),
    ('Italy', 'Naples', 40.76, 14.13, 40.92, 14.37),
    ('Italy', 'Bologna', 44.43, 11.25, 44.56, 11.43),
    ('Netherlands', 'Amsterdam', 52.25, 4.72, 52.43, 5.08),
    ('Belgium', 'Brussels', 50.75, 4.23, 50.93, 4.50),
    ('Switzerland', 'Zurich', 47.30, 8.42, 47.45, 8.65),
    ('Austria', 'Vienna', 48.11, 16.18, 48.33, 16.58),
    ('Ireland', 'Dublin', 53.25, -6.45, 53.43, -6.05),
]

CRAFT_TYPES = {
    'painter': ('worker', 'painting, finishing, renovation'),
    'electrician': ('worker', 'electrical, energy, building services'),
    'plumber': ('worker', 'plumbing, water, sanitary, building services'),
    'roofer': ('worker', 'roofing, waterproofing, envelope'),
    'carpenter': ('worker', 'carpentry, interiors, renovation'),
    'hvac': ('expert', 'HVAC, ventilation, energy'),
    'heating_engineer': ('expert', 'heating, HVAC, energy'),
    'window_construction': ('worker', 'windows, facade, envelope'),
    'tiler': ('worker', 'tiling, interiors, finishing'),
    'builder': ('contractor', 'construction, renovation, general works'),
    'insulation': ('worker', 'insulation, facade, energy retrofit'),
}

OFFICE_TYPES = {
    'architect': ('expert', 'architecture, design, permits, planning'),
    'engineer': ('expert', 'engineering, structural, MEP, technical design'),
    'surveyor': ('expert', 'surveying, inspection, measurement'),
}


def now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript('''
    CREATE TABLE IF NOT EXISTS expert_workers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL,
      source_id TEXT NOT NULL,
      name TEXT NOT NULL,
      worker_type TEXT NOT NULL,
      trades TEXT NOT NULL,
      country TEXT,
      city TEXT,
      address TEXT,
      lat REAL,
      lon REAL,
      phone TEXT,
      email TEXT,
      website TEXT,
      opening_hours TEXT,
      languages TEXT,
      verification_status TEXT NOT NULL DEFAULT 'public_listing_unverified',
      source_url TEXT,
      raw_json TEXT NOT NULL,
      imported_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(source, source_id)
    );
    ''')


def q(area):
    country, city, s, w, n, e = area
    bbox = f'{s},{w},{n},{e}'
    craft_re = '|'.join(CRAFT_TYPES)
    office_re = '|'.join(OFFICE_TYPES)
    return f'''
[out:json][timeout:45];
(
  node["name"]["craft"~"^({craft_re})$"]({bbox});
  way["name"]["craft"~"^({craft_re})$"]({bbox});
  relation["name"]["craft"~"^({craft_re})$"]({bbox});
  node["name"]["office"~"^({office_re})$"]({bbox});
  way["name"]["office"~"^({office_re})$"]({bbox});
  relation["name"]["office"~"^({office_re})$"]({bbox});
  node["name"]["shop"="paint"]({bbox});
  way["name"]["shop"="paint"]({bbox});
);
out center tags 90;
'''


def fetch(area):
    data = urllib.parse.urlencode({'data': q(area)}).encode()
    req = urllib.request.Request(OVERPASS, data=data, headers={'User-Agent': 'ArchAgent-BuildingOS/0.1 public data research'})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)


def address(tags):
    parts = []
    for k in ('addr:housenumber', 'addr:street', 'addr:postcode', 'addr:city'):
        if tags.get(k): parts.append(tags[k])
    return ', '.join(parts)


def classify(tags):
    craft = tags.get('craft')
    office = tags.get('office')
    shop = tags.get('shop')
    if craft in CRAFT_TYPES:
        return CRAFT_TYPES[craft] + (craft,)
    if office in OFFICE_TYPES:
        return OFFICE_TYPES[office] + (office,)
    if shop == 'paint':
        return ('supplier', 'paint, coatings, finishing materials', 'paint supplier')
    return ('expert', 'building services', 'unknown')


def osm_url(el):
    return f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}"


def normalize(area, el):
    country, city, *_ = area
    tags = el.get('tags') or {}
    name = tags.get('name') or tags.get('brand') or ''
    if not name.strip(): return None
    lat = el.get('lat') or (el.get('center') or {}).get('lat')
    lon = el.get('lon') or (el.get('center') or {}).get('lon')
    worker_type, trades, source_trade = classify(tags)
    website = tags.get('website') or tags.get('contact:website') or tags.get('url') or ''
    phone = tags.get('phone') or tags.get('contact:phone') or ''
    email = tags.get('email') or tags.get('contact:email') or ''
    return {
        'source': 'openstreetmap_overpass',
        'source_id': f"{el.get('type')}/{el.get('id')}",
        'name': name.strip(),
        'worker_type': worker_type,
        'trades': trades,
        'source_trade': source_trade,
        'country': country,
        'city': tags.get('addr:city') or city,
        'address': address(tags),
        'lat': lat,
        'lon': lon,
        'phone': phone,
        'email': email,
        'website': website,
        'opening_hours': tags.get('opening_hours') or '',
        'languages': '',
        'verification_status': 'public_listing_unverified',
        'source_url': osm_url(el),
        'raw_json': json.dumps(el, ensure_ascii=False),
        'imported_at': now(),
        'updated_at': now(),
    }


def upsert(con, row):
    cols = ['source','source_id','name','worker_type','trades','country','city','address','lat','lon','phone','email','website','opening_hours','languages','verification_status','source_url','raw_json','imported_at','updated_at']
    vals = [row.get(c) for c in cols]
    placeholders = ','.join('?' for _ in cols)
    update = ','.join(f'{c}=excluded.{c}' for c in cols if c not in ('source','source_id','imported_at'))
    con.execute(f'INSERT INTO expert_workers({",".join(cols)}) VALUES ({placeholders}) ON CONFLICT(source,source_id) DO UPDATE SET {update}', vals)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Import public expert/worker/company listings from OpenStreetMap Overpass into BuildingOS.')
    parser.add_argument('--dry-run', action='store_true', help='Fetch and report rows without writing SQLite/export files')
    parser.add_argument('--areas', default='', help='Comma-separated city names to import, e.g. Berlin,Paris. Default: all configured areas')
    parser.add_argument('--list-areas', action='store_true', help='List configured import areas and exit')
    parser.add_argument('--sleep', type=float, default=2.0, help='Seconds to wait between Overpass requests')
    args = parser.parse_args(argv)

    if args.list_areas:
        for country, city, *_ in AREAS:
            print(f'{city}, {country}')
        return 0

    selected = AREAS
    if args.areas:
        wanted = {x.strip().lower() for x in args.areas.split(',') if x.strip()}
        selected = [a for a in AREAS if a[1].lower() in wanted]
        missing = sorted(wanted - {a[1].lower() for a in selected})
        if missing:
            raise SystemExit(f'Unknown area(s): {", ".join(missing)}. Use --list-areas.')

    con = sqlite3.connect(APP_DB)
    ensure_schema(con)
    all_rows = []
    errors = []
    for area in selected:
        try:
            data = fetch(area)
            rows = []
            for el in data.get('elements', []):
                row = normalize(area, el)
                if row:
                    rows.append(row)
                    if not args.dry_run:
                        upsert(con, row)
            if not args.dry_run:
                con.commit()
            all_rows.extend(rows)
            print(f'{area[1]}: {len(rows)} listings')
            time.sleep(max(0, args.sleep))
        except Exception as exc:
            errors.append((area[1], str(exc)))
            print(f'{area[1]}: ERROR {exc}')
            time.sleep(max(0, args.sleep))
    if args.dry_run:
        print(f'DRY RUN total fetched rows: {len(all_rows)}; database not modified')
        if errors:
            print('Warnings:')
            for city, err in errors:
                print(f'- {city}: {err}')
        con.close()
        return 0
    # Export all DB records, not just current-run successes.
    con.row_factory = sqlite3.Row
    records = [dict(r) for r in con.execute('SELECT * FROM expert_workers ORDER BY country, city, worker_type, name').fetchall()]
    JSON_OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    with CSV_OUT.open('w', newline='', encoding='utf-8') as f:
        fields = ['id','name','worker_type','trades','country','city','address','phone','email','website','source_url','verification_status']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records: w.writerow({k: r.get(k, '') for k in fields})
    by_type = {}
    by_country = {}
    for r in records:
        by_type[r['worker_type']] = by_type.get(r['worker_type'], 0) + 1
        by_country[r['country']] = by_country.get(r['country'], 0) + 1
    sample = records[:40]
    md = ['# Expert / Worker Database Report', '', f'Imported/available records: {len(records)}', '', 'Source: OpenStreetMap Overpass public listings. These are public/unverified business records, not vetted partners.', '', '## By type']
    md += [f'- {k}: {v}' for k, v in sorted(by_type.items())]
    md += ['', '## By country']
    md += [f'- {k}: {v}' for k, v in sorted(by_country.items())]
    md += ['', '## Sample records', '', '| Name | Type | Trades | City | Country | Website | Source |', '|---|---|---|---|---|---|---|']
    for r in sample:
        md.append(f"| {r['name'].replace('|','/')} | {r['worker_type']} | {r['trades'].replace('|','/')} | {r.get('city') or ''} | {r.get('country') or ''} | {r.get('website') or ''} | {r.get('source_url') or ''} |")
    if errors:
        md += ['', '## Import warnings'] + [f'- {city}: {err}' for city, err in errors]
    MD_OUT.write_text('\n'.join(md), encoding='utf-8')
    con.close()
    print(f'TOTAL DB records: {len(records)}')
    print(f'Wrote {JSON_OUT.name}, {CSV_OUT.name}, {MD_OUT.name}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
