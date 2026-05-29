"""Enterprise data plane: full backup/restore, data export, retention, DB stats.

Pure stdlib. Backups use the SQLite online backup API (``con.backup()``) in both
directions — hot snapshot for backup, and ``source.backup(live)`` for an in-place
restore that is safe against the server's short-lived connections. Every backup is
integrity-checked (``PRAGMA integrity_check``); every destructive action requires
``confirm: true``, takes a pre-restore safety backup, and is written to ``activities``.
Exports redact secret columns and never include auth-secret tables.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path

from archagent.api.auth import parse_int_param
from archagent.api.errors import ApiError
from archagent.core.audit import log_activity
from archagent.core.backup import sqlite_backup
from archagent.core.config import APP_DB, BACKUP_DIR, EXPORT_DIR, LEADS_DB
from archagent.core.db import app_conn, app_cursor, leads_conn, now, rows_to_dicts

# Tables safe to export (business data). Auth-secret tables (sessions, api_keys,
# login_attempts) and large blobs (pdf_extractions) are intentionally excluded.
_APP_EXPORT = (
    'prospects', 'proposals', 'contractors', 'followups', 'customer_profiles',
    'bid_profiles', 'lead_radar_exports', 'tender_dossiers', 'expert_workers',
    'worker_verifications', 'analysis_jobs', 'pilot_requests', 'activities',
    'error_log', 'backups', 'feature_flags', 'settings', 'users',
)
_LEADS_EXPORT = ('sources', 'project_leads')
# Secret columns stripped from any export.
_REDACT = {'users': {'password_hash', 'password_salt', 'pbkdf2_iterations'}}
# Tables carrying a soft-delete column (kept in sync with migrations._SOFT_DELETE_TABLES).
_SOFT_DELETE_TABLES = (
    'prospects', 'proposals', 'contractors', 'followups', 'customer_profiles',
    'bid_profiles', 'expert_workers', 'tender_dossiers',
)
_DEFAULT_RETENTION_DAYS = 365
_DEFAULT_KEEP = 30


def _actor_id(principal):
    return getattr(principal, 'user_id', None) if principal else None


def _stamp() -> str:
    # Microsecond + random suffix → unique filenames even for two backups in the same
    # second (otherwise a later backup silently overwrites an earlier same-second file).
    ts = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')
    return f'{ts}_{uuid.uuid4().hex[:6]}'


def _fsize(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _count(con, table: str):
    try:
        return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    except sqlite3.Error:
        return None


def _integrity(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return con.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        con.close()


# ─── Backups ────────────────────────────────────────────────────────────────────

def _record_backup(con, db_path: Path, dest: Path, ok, error, size, sha, verified, kind, note, principal) -> int:
    cur = con.execute(
        'INSERT INTO backups(created_at,path,size_bytes,sha256,trigger,actor_user_id,ok,error,db_name,kind,verified,note)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (now(), str(dest), size, sha, kind, _actor_id(principal), 1 if ok else 0, error,
         db_path.name, kind, 1 if verified else 0, note),
    )
    return cur.lastrowid


def _backup_db(con, db_path: Path, kind: str, principal, note=None) -> dict:
    """Snapshot one DB file, verify it, record a row. Returns a result dict."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f'{db_path.stem}_{_stamp()}.sqlite3'
    ok, error, size, sha, verified = True, None, 0, None, False
    try:
        sqlite_backup(db_path, dest)
        size = dest.stat().st_size
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        verified = _integrity(dest) == 'ok'
        if not verified:
            ok, error = False, 'integrity check failed'
    except Exception as exc:  # noqa: BLE001 — record any backup failure
        ok, error = False, str(exc)
    bid = _record_backup(con, db_path, dest, ok, error, size, sha, verified, kind, note, principal)
    return {'id': bid, 'db': db_path.name, 'path': str(dest), 'ok': ok,
            'verified': verified, 'size_bytes': size, 'sha256': sha, 'error': error}


def _prune_backups(keep: int) -> None:
    if not BACKUP_DIR.exists():
        return
    for prefix in {APP_DB.stem, LEADS_DB.stem}:
        files = sorted(BACKUP_DIR.glob(f'{prefix}_*.sqlite3'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass


def create_backup(principal=None, kind: str = 'manual', keep: int = _DEFAULT_KEEP) -> dict:
    """Back up BOTH databases (app + leads), integrity-verify, record, and prune."""
    results = []
    con = app_conn()
    try:
        for db_path in (APP_DB, LEADS_DB):
            if not db_path.exists():
                continue
            results.append(_backup_db(con, db_path, kind, principal))
        log_activity(con, 'backup', f'Backup ({kind}) of {len(results)} database(s)',
                     {'results': results}, principal)
        con.commit()
    finally:
        con.close()
    _prune_backups(keep)
    if results and not any(r['ok'] for r in results):
        raise ApiError('internal_error', 'all database backups failed')
    summary = {'ok': all(r['ok'] for r in results) if results else False, 'backups': results}
    # Keep the legacy single-backup shape the existing UI reads (size of app DB).
    app_result = next((r for r in results if r['db'] == APP_DB.name), None)
    if app_result:
        summary.update({'id': app_result['id'], 'size_bytes': app_result['size_bytes'],
                        'sha256': app_result['sha256']})
    return summary


def list_backups() -> dict:
    con = app_conn()
    try:
        return {'items': rows_to_dicts(con.execute(
            'SELECT * FROM backups ORDER BY id DESC LIMIT 200').fetchall())}
    finally:
        con.close()


def _backup_row(backup_id: int):
    con = app_conn()
    try:
        row = con.execute('SELECT * FROM backups WHERE id=?', (backup_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise ApiError('not_found', f'backup #{backup_id} not found')
    return row


def resolve_backup_path(backup_id: int) -> tuple[Path, str]:
    """Return (path, download_name) for a backup's file, or raise."""
    row = _backup_row(backup_id)
    path = Path(row['path'])
    if not path.exists():
        raise ApiError('not_found', 'backup file is missing on disk (pruned?)')
    return path, path.name


def verify_backup(backup_id: int, principal=None) -> dict:
    row = _backup_row(backup_id)
    path = Path(row['path'])
    if not path.exists():
        raise ApiError('not_found', 'backup file is missing on disk')
    result = _integrity(path)
    ok = result == 'ok'
    with app_cursor() as con:
        con.execute('UPDATE backups SET verified=? WHERE id=?', (1 if ok else 0, backup_id))
        log_activity(con, 'backup_verify', f'Verified backup #{backup_id}: {result}',
                     {'id': backup_id, 'result': result}, principal)
    return {'ok': ok, 'id': backup_id, 'integrity': result}


def _target_db_for(row) -> Path:
    name = (row['db_name'] or '') if 'db_name' in row.keys() else ''
    blob = (name + ' ' + (row['path'] or '')).lower()
    if 'actionable_projects' in blob or 'leads' in blob:
        return LEADS_DB
    return APP_DB


def restore_backup(backup_id: int, payload: dict, principal=None) -> dict:
    """Restore a verified backup into its live DB, after a pre-restore safety backup."""
    if not (payload or {}).get('confirm'):
        raise ApiError('bad_request', 'confirmation required: send {"confirm": true}')
    row = _backup_row(backup_id)
    if not row['ok']:
        raise ApiError('bad_request', 'cannot restore from a failed backup')
    src = Path(row['path'])
    if not src.exists():
        raise ApiError('not_found', 'backup file is missing on disk')
    integ = _integrity(src)
    if integ != 'ok':
        raise ApiError('bad_request', f'backup integrity check failed: {integ}')
    target = _target_db_for(row)

    # Safety backup of the live target before we overwrite it.
    con = app_conn()
    try:
        safety = _backup_db(con, target, 'pre-restore', principal,
                            note=f'auto safety backup before restoring #{backup_id}')
        con.commit()
    finally:
        con.close()

    # Restore by atomically replacing the live DB file, then dropping its WAL/SHM
    # sidecars so no stale frames mask the restored content. os.replace is atomic, so
    # in-flight readers keep their open handle until they close. (More deterministic
    # than the backup API into a live WAL database.)
    tmp = Path(str(target) + '.restoring')
    shutil.copyfile(src, tmp)
    os.replace(tmp, target)
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(target) + suffix)
        try:
            sidecar.unlink()
        except OSError:
            pass

    # Restoring the app DB reverts the backups index to the snapshot's state, so
    # reconcile it with the files actually on disk (incl. this restore's safety backup).
    rescanned = rescan_backups(principal).get('added', 0) if target == APP_DB else 0

    with app_cursor() as c:
        log_activity(c, 'restore', f'Restored {target.name} from backup #{backup_id}',
                     {'from_backup_id': backup_id, 'target': target.name,
                      'safety_backup_id': safety['id'], 'rescanned': rescanned}, principal)
    return {'ok': True, 'restored': target.name, 'from_backup_id': backup_id,
            'safety_backup_id': safety['id'], 'rescanned': rescanned}


def rescan_backups(principal=None) -> dict:
    """Reconcile the backups table with files on disk — add a row for any backup file in
    BACKUP_DIR not already indexed (e.g. after an app-DB restore reverted the table)."""
    if not BACKUP_DIR.exists():
        return {'ok': True, 'added': 0}
    added = 0
    con = app_conn()
    try:
        known = {r[0] for r in con.execute('SELECT path FROM backups')}
        for f in sorted(BACKUP_DIR.glob('*.sqlite3')):
            if str(f) in known:
                continue
            db_name = (APP_DB.name if f.name.startswith(APP_DB.stem)
                       else LEADS_DB.name if f.name.startswith(LEADS_DB.stem) else f.name)
            created = dt.datetime.utcfromtimestamp(f.stat().st_mtime).replace(microsecond=0).isoformat() + 'Z'
            verified = _integrity(f) == 'ok'
            con.execute(
                'INSERT INTO backups(created_at,path,size_bytes,sha256,trigger,actor_user_id,ok,error,db_name,kind,verified,note)'
                " VALUES (?,?,?,?,'rescan',?,1,NULL,?,'rescan',?,?)",
                (created, str(f), f.stat().st_size, hashlib.sha256(f.read_bytes()).hexdigest(),
                 _actor_id(principal), db_name, 1 if verified else 0, 'recovered by rescan'),
            )
            added += 1
        if added:
            log_activity(con, 'backup_rescan', f'Reconciled {added} backup file(s) into the index',
                         {'added': added}, principal)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'added': added}


# ─── Export ───────────────────────────────────────────────────────────────────────

def _conn_for(db: str):
    if db == 'app':
        return app_conn()
    if db == 'leads':
        return leads_conn()
    raise ApiError('bad_request', f'unknown database: {db!r} (use app|leads)')


def _check_table(db: str, table: str) -> None:
    allowed = _APP_EXPORT if db == 'app' else _LEADS_EXPORT
    if table not in allowed:
        raise ApiError('not_found', f'table not exportable: {table!r}')


def list_exportable() -> dict:
    items = []
    con = app_conn()
    try:
        for t in _APP_EXPORT:
            items.append({'db': 'app', 'table': t, 'rows': _count(con, t)})
    finally:
        con.close()
    lc = leads_conn()
    try:
        for t in _LEADS_EXPORT:
            items.append({'db': 'leads', 'table': t, 'rows': _count(lc, t)})
    finally:
        lc.close()
    return {'items': items}


def _keep_cols(cursor, table: str) -> list[str]:
    cols = [d[0] for d in cursor.description]
    redact = _REDACT.get(table, set())
    return [c for c in cols if c not in redact]


def export_table(db: str, table: str, params: dict, principal=None) -> dict:
    _check_table(db, table)
    fmt = (params.get('format') or ['csv'])[0].lower()
    if fmt not in ('csv', 'json'):
        fmt = 'csv'
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f'export_{db}_{table}_{_stamp()}.{fmt}'
    dest = EXPORT_DIR / fname
    con = _conn_for(db)
    try:
        cur = con.execute(f'SELECT * FROM {table}')
        keep = _keep_cols(cur, table)
        if fmt == 'json':
            rows = [{c: r[c] for c in keep} for r in cur]
            dest.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
            n = len(rows)
        else:
            n = 0
            with open(dest, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(keep)
                for r in cur:
                    w.writerow([r[c] for c in keep])
                    n += 1
    finally:
        con.close()
    with app_cursor() as c:
        log_activity(c, 'data_export', f'Exported {db}.{table} ({n} rows, {fmt})',
                     {'db': db, 'table': table, 'rows': n, 'file': fname}, principal)
    return {'ok': True, 'db': db, 'table': table, 'format': fmt, 'rows': n,
            'file': fname, 'download_url': f'/api/admin/export/download?name={fname}'}


def export_all(principal=None) -> dict:
    """Zip every allow-listed table (both DBs) as CSV + a manifest.json."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f'archagent_full_export_{_stamp()}.zip'
    dest = EXPORT_DIR / fname
    manifest = {'generated_at': now(), 'app_db': APP_DB.name, 'leads_db': LEADS_DB.name, 'tables': []}
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        for db, tables, factory in (('app', _APP_EXPORT, app_conn), ('leads', _LEADS_EXPORT, leads_conn)):
            con = factory()
            try:
                for t in tables:
                    try:
                        cur = con.execute(f'SELECT * FROM {t}')
                    except sqlite3.Error:
                        continue
                    keep = _keep_cols(cur, t)
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(keep)
                    n = 0
                    for r in cur:
                        w.writerow([r[c] for c in keep])
                        n += 1
                    z.writestr(f'{db}/{t}.csv', buf.getvalue())
                    manifest['tables'].append({'db': db, 'table': t, 'rows': n})
            finally:
                con.close()
        z.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))
    with app_cursor() as c:
        log_activity(c, 'data_export', f'Full export ({len(manifest["tables"])} tables)',
                     {'file': fname, 'tables': len(manifest['tables'])}, principal)
    return {'ok': True, 'file': fname, 'download_url': f'/api/admin/export/download?name={fname}',
            'tables': manifest['tables'], 'size_bytes': _fsize(dest)}


def resolve_export_path(name: str) -> tuple[Path, str]:
    safe = os.path.basename(name or '')
    if not safe or safe != name:
        raise ApiError('bad_request', 'invalid export name')
    path = EXPORT_DIR / safe
    if not path.exists() or not path.is_file():
        raise ApiError('not_found', 'export file not found')
    return path, safe


# ─── Retention ──────────────────────────────────────────────────────────────────

def _cutoff(days: int) -> str:
    return (dt.datetime.utcnow() - dt.timedelta(days=days)).replace(microsecond=0).isoformat() + 'Z'


def retention_preview(params: dict | None = None) -> dict:
    days = parse_int_param(params or {}, 'days', _DEFAULT_RETENTION_DAYS, 1, 3650)
    cutoff = _cutoff(days)
    con = app_conn()
    try:
        soft = {}
        for t in _SOFT_DELETE_TABLES:
            soft[t] = con.execute(
                f'SELECT COUNT(*) FROM {t} WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,)
            ).fetchone()[0]
        activities = con.execute('SELECT COUNT(*) FROM activities WHERE created_at < ?', (cutoff,)).fetchone()[0]
        errors = con.execute('SELECT COUNT(*) FROM error_log WHERE created_at < ?', (cutoff,)).fetchone()[0]
        logins = con.execute('SELECT COUNT(*) FROM login_attempts WHERE attempted_at < ?', (cutoff,)).fetchone()[0]
    finally:
        con.close()
    return {'days': days, 'cutoff': cutoff, 'soft_deleted': soft,
            'activities': activities, 'error_log': errors, 'login_attempts': logins}


def retention_purge(payload: dict, principal=None) -> dict:
    p = payload or {}
    if not p.get('confirm'):
        raise ApiError('bad_request', 'confirmation required: send {"confirm": true}')
    try:
        days = max(1, int(p.get('days', _DEFAULT_RETENTION_DAYS)))
    except (TypeError, ValueError):
        raise ApiError('bad_request', 'days must be an integer')
    cutoff = _cutoff(days)
    purged = {}
    with app_cursor() as con:
        for t in _SOFT_DELETE_TABLES:
            cur = con.execute(
                f'DELETE FROM {t} WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,))
            purged[t] = cur.rowcount
        purged['activities'] = con.execute('DELETE FROM activities WHERE created_at < ?', (cutoff,)).rowcount
        purged['error_log'] = con.execute('DELETE FROM error_log WHERE created_at < ?', (cutoff,)).rowcount
        purged['login_attempts'] = con.execute('DELETE FROM login_attempts WHERE attempted_at < ?', (cutoff,)).rowcount
        log_activity(con, 'retention_purge', f'Purged records older than {days} days',
                     {'cutoff': cutoff, 'purged': purged}, principal)
    return {'ok': True, 'days': days, 'cutoff': cutoff, 'purged': purged}


# ─── DB stats ───────────────────────────────────────────────────────────────────

def db_stats() -> dict:
    app_tables = {}
    con = app_conn()
    try:
        for t in sorted(_APP_EXPORT):
            entry = {'rows': _count(con, t)}
            if t in _SOFT_DELETE_TABLES:
                entry['deleted'] = con.execute(
                    f'SELECT COUNT(*) FROM {t} WHERE deleted_at IS NOT NULL').fetchone()[0]
            app_tables[t] = entry
    finally:
        con.close()
    leads_tables = {}
    lc = leads_conn()
    try:
        for t in _LEADS_EXPORT:
            leads_tables[t] = {'rows': _count(lc, t)}
    finally:
        lc.close()
    return {'databases': [
        {'name': APP_DB.name, 'size_bytes': _fsize(APP_DB),
         'wal_bytes': _fsize(Path(str(APP_DB) + '-wal')), 'tables': app_tables},
        {'name': LEADS_DB.name, 'size_bytes': _fsize(LEADS_DB),
         'wal_bytes': _fsize(Path(str(LEADS_DB) + '-wal')), 'tables': leads_tables},
    ]}
