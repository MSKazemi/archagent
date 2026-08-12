#!/usr/bin/env python3
"""Unit tests for the enterprise data plane: backup/restore, export, retention, stats.

Self-contained: isolates BOTH databases (ARCHAGENT_APP_DB / ARCHAGENT_LEADS_DB) and
the backup/export directories into a temp dir, so it never touches real data. Prints a
single PASS line on success.
"""
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

_TMP = Path(tempfile.mkdtemp())
os.environ['ARCHAGENT_APP_DB'] = str(_TMP / 'app.sqlite3')
os.environ['ARCHAGENT_LEADS_DB'] = str(_TMP / 'leads.sqlite3')

from archagent.core import db  # noqa: E402
from archagent.api.errors import ApiError  # noqa: E402
from archagent.api.handlers import admin_data  # noqa: E402


def _seed_leads_db():
    con = sqlite3.connect(os.environ['ARCHAGENT_LEADS_DB'])
    con.execute('CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, name TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS project_leads (id INTEGER PRIMARY KEY, title TEXT, buyer_country TEXT)')
    con.execute("INSERT INTO sources(name) VALUES ('TED')")
    con.execute("INSERT INTO project_leads(title,buyer_country) VALUES ('Retrofit school','IT')")
    con.commit()
    con.close()


def _insert_prospect(name):
    with db.app_cursor() as con:
        con.execute(
            "INSERT INTO prospects(name,email,status,created_at,updated_at) VALUES (?,?,?,?,?)",
            (name, f'{name}@x.com', 'new', db.now(), db.now()),
        )


def main():
    # Redirect backup/export dirs into the temp sandbox.
    admin_data.BACKUP_DIR = _TMP / 'backups'
    admin_data.EXPORT_DIR = _TMP / 'exports'

    db.init_app_db()          # creates app schema + runs migrations (incl. m9)
    _seed_leads_db()
    _insert_prospect('alpha')

    # ── backup: both DBs, integrity-verified, recorded ──────────────────────────
    res = admin_data.create_backup(kind='manual')
    assert res['ok'] is True, res
    dbs = {b['db'] for b in res['backups']}
    assert dbs == {'app.sqlite3', 'leads.sqlite3'}, dbs
    assert all(b['verified'] and b['ok'] for b in res['backups']), res
    listed = admin_data.list_backups()['items']
    assert len(listed) == 2 and all(r['db_name'] for r in listed), listed
    app_backup_id = next(b['id'] for b in res['backups'] if b['db'] == 'app.sqlite3')

    # verify endpoint re-checks integrity
    v = admin_data.verify_backup(app_backup_id)
    assert v['ok'] and v['integrity'] == 'ok', v

    # ── db stats ────────────────────────────────────────────────────────────────
    stats = admin_data.db_stats()
    names = {d['name'] for d in stats['databases']}
    assert names == {'app.sqlite3', 'leads.sqlite3'}, names
    app_stat = next(d for d in stats['databases'] if d['name'] == 'app.sqlite3')
    assert app_stat['tables']['prospects']['rows'] >= 1, app_stat

    # ── export single table (csv + json) ────────────────────────────────────────
    csv_out = admin_data.export_table('app', 'prospects', {'format': ['csv']})
    csv_path = admin_data.EXPORT_DIR / csv_out['file']
    text = csv_path.read_text()
    assert text.startswith('id,') and 'alpha@x.com' in text, text[:120]

    json_out = admin_data.export_table('app', 'prospects', {'format': ['json']})
    assert json_out['rows'] >= 1 and json_out['file'].endswith('.json')

    # secret columns are redacted from a users export
    with db.app_cursor() as con:
        from archagent.core import auth_db
        auth_db.create_user(con, 'sec@x.com', 'pw123456', 'admin')
    users_csv = admin_data.export_table('app', 'users', {'format': ['csv']})
    utext = (admin_data.EXPORT_DIR / users_csv['file']).read_text()
    assert 'password_hash' not in utext and 'password_salt' not in utext, 'secrets leaked!'
    assert 'sec@x.com' in utext

    # disallowed table / db
    for bad in (('app', 'sessions'), ('leads', 'prospects'), ('nope', 'x')):
        try:
            admin_data.export_table(bad[0], bad[1], {})
            raise AssertionError(f'expected error for {bad}')
        except ApiError:
            pass

    # ── export everything (zip + manifest) ──────────────────────────────────────
    allout = admin_data.export_all()
    zpath = admin_data.EXPORT_DIR / allout['file']
    with zipfile.ZipFile(zpath) as z:
        members = set(z.namelist())
        assert 'manifest.json' in members
        assert 'app/prospects.csv' in members
        assert 'leads/project_leads.csv' in members
        assert 'app/users.csv' in members
        assert 'password_hash' not in z.read('app/users.csv').decode()

    # download path resolver rejects traversal
    for bad in ('../app.sqlite3', '/etc/passwd', 'nope.csv'):
        try:
            admin_data.resolve_export_path(bad)
            raise AssertionError(f'expected error for {bad}')
        except ApiError:
            pass
    ok_path, ok_name = admin_data.resolve_export_path(allout['file'])
    assert ok_path.exists() and ok_name == allout['file']

    # ── retention preview + purge guard ─────────────────────────────────────────
    prev = admin_data.retention_preview({'days': ['365']})
    assert prev['days'] == 365 and 'soft_deleted' in prev, prev
    try:
        admin_data.retention_purge({})  # no confirm
        raise AssertionError('purge without confirm should fail')
    except ApiError:
        pass
    # purge with confirm + days=0-ish large window removes nothing recent (just runs)
    purged = admin_data.retention_purge({'confirm': True, 'days': 3650})
    assert purged['ok'] and 'purged' in purged, purged

    # ── restore round-trip ──────────────────────────────────────────────────────
    _insert_prospect('beta')
    res2 = admin_data.create_backup(kind='manual')
    bid = next(b['id'] for b in res2['backups'] if b['db'] == 'app.sqlite3')
    # destroy data
    with db.app_cursor() as con:
        con.execute("DELETE FROM prospects WHERE name='beta'")
    con = db.app_conn()
    assert con.execute("SELECT COUNT(*) FROM prospects WHERE name='beta'").fetchone()[0] == 0
    con.close()
    # restore requires confirm
    try:
        admin_data.restore_backup(bid, {})
        raise AssertionError('restore without confirm should fail')
    except ApiError:
        pass
    rr = admin_data.restore_backup(bid, {'confirm': True})
    assert rr['ok'] and rr['restored'] == 'app.sqlite3' and rr['safety_backup_id'], rr
    con = db.app_conn()
    assert con.execute("SELECT COUNT(*) FROM prospects WHERE name='beta'").fetchone()[0] == 1, 'restore failed'
    con.close()

    print('PASS tests/test_admin_data.py: backup(both DBs)/verify/restore, export csv+json+zip+redaction, retention, stats')


if __name__ == '__main__':
    main()
