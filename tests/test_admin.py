#!/usr/bin/env python3
"""Unit + end-to-end tests for the identity/RBAC layer and the admin plane.

Self-contained: uses an isolated temp app database (ARCHAGENT_APP_DB) so it never
touches the real data, and spawns a live server for the end-to-end portion. Prints a
single PASS line on success.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# Isolate the database BEFORE importing archagent modules.
_TMP = Path(tempfile.mkdtemp())
os.environ['ARCHAGENT_APP_DB'] = str(_TMP / 'app.sqlite3')
os.environ['ARCHAGENT_LEADS_DB'] = str(BASE / 'archagent_actionable_projects.sqlite3')

from archagent.core import db, passwords, rbac, auth_db, sessions  # noqa: E402
from archagent.core.migrations import run_migrations  # noqa: E402
from archagent.api import validation  # noqa: E402
from archagent.api.errors import ApiError  # noqa: E402
from archagent.api.ratelimit import RateLimiter  # noqa: E402


def unit_tests():
    # passwords
    h, s, i = passwords.hash_password('correct horse')
    assert passwords.verify_password('correct horse', h, s, i)
    assert not passwords.verify_password('wrong', h, s, i)
    assert h != 'correct horse'

    # rbac
    assert rbac.has_permission('admin', 'anything:write')
    assert rbac.has_permission('viewer', 'leads:read')
    assert not rbac.has_permission('viewer', 'crm:write')
    assert rbac.has_permission('sales', 'crm:write')
    assert not rbac.has_permission('sales', 'users:manage')
    assert rbac.permission_for_route('GET', '/api/leads') == 'leads:read'
    assert rbac.permission_for_route('POST', '/api/prospects') == 'crm:write'
    assert rbac.permission_for_route('DELETE', '/api/admin/resources/prospects/5') == 'resources:write'
    assert rbac.permission_for_route('GET', '/api/v1/leads') == 'leads:read'

    # validation
    spec = {
        'name': validation.field(str, required=True, max_len=10),
        'count': validation.field(int, min_val=0, max_val=5),
        'status': validation.field(str, choices=['a', 'b'], default='a'),
    }
    cleaned = validation.validate({'name': 'ok', 'count': '3'}, spec)
    assert cleaned == {'name': 'ok', 'count': 3, 'status': 'a'}, cleaned
    for bad in ({}, {'name': 'x' * 20}, {'name': 'ok', 'count': 99}, {'name': 'ok', 'status': 'z'}):
        try:
            validation.validate(bad, spec)
            raise AssertionError(f'expected validation error for {bad}')
        except ApiError as e:
            assert e.code == 'validation_error'

    # rate limiter token bucket: a tiny bucket empties then refuses
    rl = RateLimiter()
    rl._tiers = {'t': (2, 0.0), 'default': (2, 0.0)}  # capacity 2, no refill
    assert rl.allow('k', 't')[0] and rl.allow('k', 't')[0]
    ok, retry = rl.allow('k', 't')
    assert not ok and retry >= 1

    # migrations idempotency
    db.init_app_db()
    con = db.app_conn()
    try:
        v1 = sorted(r[0] for r in con.execute('SELECT version FROM schema_migrations'))
        assert v1 == [2, 3, 4, 5, 6, 7, 8, 9], v1
        applied = run_migrations(con)  # re-run: nothing new
        assert applied == [], applied
        # users/api_keys/sessions round-trip
        uid = auth_db.create_user(con, 'unit@test.com', 'pw12345', 'analyst'); con.commit()
        assert auth_db.authenticate_user(con, 'unit@test.com', 'pw12345')['role'] == 'analyst'
        try:
            auth_db.create_user(con, 'unit@test.com', 'x', 'viewer')
            raise AssertionError('expected duplicate email rejection')
        except ValueError:
            pass
        # lockout after repeated failures
        for _ in range(5):
            try:
                auth_db.authenticate_user(con, 'unit@test.com', 'bad')
            except ValueError:
                pass
        try:
            auth_db.authenticate_user(con, 'unit@test.com', 'pw12345')
            raise AssertionError('expected lockout')
        except auth_db.LockedOut:
            pass
        tok = sessions.create_session(con, uid)
        assert sessions.resolve_session(con, tok)['email'] == 'unit@test.com'
        sessions.revoke_by_token(con, tok); con.commit()
        assert sessions.resolve_session(con, tok) is None
        key = auth_db.issue_api_key(con, 'k', 'viewer'); con.commit()
        assert auth_db.resolve_api_key(con, key['key'])['role'] == 'viewer'
        auth_db.revoke_api_key(con, key['id']); con.commit()
        assert auth_db.resolve_api_key(con, key['key']) is None
    finally:
        con.close()
    print('  unit: passwords, rbac, validation, ratelimit, migrations, users/sessions/keys OK')


PORT = 8097
URL = f'http://127.0.0.1:{PORT}'


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def _req(opener, method, path, body=None, headers=None, expect=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {'Content-Type': 'application/json'} if data else {}
    h.update(headers or {})
    req = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        resp = opener.open(req, timeout=10)
        code, payload = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read()
    if expect is not None:
        assert code == expect, f'{method} {path} → {code} (wanted {expect}): {payload[:200]}'
    try:
        return code, json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        return code, {}


def e2e_tests():
    env = os.environ.copy()
    env['ARCHAGENT_APP_DB'] = str(_TMP / 'e2e_app.sqlite3')  # fresh DB so bootstrap seeds the admin
    env['ARCHAGENT_TOKEN'] = 'e2e-token-which-is-long-enough-1234567890'
    env['ARCHAGENT_ADMIN_EMAIL'] = 'admin@test.com'
    env['ARCHAGENT_ADMIN_PASSWORD'] = 'adminpass123'
    proc = subprocess.Popen([sys.executable, 'archagent_server.py', '--port', str(PORT)],
                            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        for _ in range(40):
            try:
                if _req(_opener(), 'GET', '/api/health')[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError('server did not start: ' + (proc.stdout.read() if proc.stdout else ''))

        anon = _opener()
        # anonymous is rejected on a protected route
        _req(anon, 'GET', '/api/stats', expect=401)
        # legacy token still authorizes as admin
        _, stats = _req(anon, 'GET', '/api/stats', headers={'X-ArchAgent-Token': env['ARCHAGENT_TOKEN']}, expect=200)
        assert 'total_leads' in stats

        # admin session login
        admin = _opener()
        _, me0 = _req(admin, 'POST', '/api/auth/login', {'email': 'admin@test.com', 'password': 'adminpass123'}, expect=200)
        assert me0['user']['role'] == 'admin'
        _, me = _req(admin, 'GET', '/api/admin/me', expect=200)
        assert me['role'] == 'admin' and me['permissions'] == ['*']

        # create a prospect (admin), then list/patch/soft-delete/restore via admin resources
        _, p = _req(admin, 'POST', '/api/prospects', {'name': 'E2E Co', 'company': 'E2E', 'offer': 'Lead Radar'}, expect=201)
        pid = p['id']
        _, lst = _req(admin, 'GET', '/api/admin/resources/prospects?q=E2E', expect=200)
        assert lst['total'] >= 1 and any(r['id'] == pid for r in lst['items'])
        _, patched = _req(admin, 'PATCH', f'/api/admin/resources/prospects/{pid}', {'status': 'qualified'}, expect=200)
        assert patched['status'] == 'qualified'
        _req(admin, 'DELETE', f'/api/admin/resources/prospects/{pid}', expect=200)
        _, lst2 = _req(admin, 'GET', '/api/admin/resources/prospects', expect=200)
        assert not any(r['id'] == pid for r in lst2['items']), 'soft-deleted row should be hidden'
        _req(admin, 'POST', f'/api/admin/resources/prospects/{pid}/restore', expect=200)
        # change history reflects the edits
        _, hist = _req(admin, 'GET', f'/api/admin/audit/history?resource=prospects&id={pid}', expect=200)
        assert len(hist['items']) >= 2

        # RBAC: create a viewer, log in, confirm writes are forbidden but reads allowed
        _req(admin, 'POST', '/api/admin/security/users',
             {'email': 'viewer@test.com', 'password': 'viewerpass', 'role': 'viewer'}, expect=201)
        viewer = _opener()
        _req(viewer, 'POST', '/api/auth/login', {'email': 'viewer@test.com', 'password': 'viewerpass'}, expect=200)
        _req(viewer, 'GET', '/api/leads?limit=1', expect=200)
        _req(viewer, 'POST', '/api/prospects', {'name': 'nope'}, expect=403)
        _req(viewer, 'GET', '/api/admin/security/users', expect=403)  # admin-only

        # API key issue → use → revoke → rejected
        _, key = _req(admin, 'POST', '/api/admin/security/api-keys', {'name': 'ci', 'role': 'analyst'}, expect=201)
        assert '.' in key['key']
        _, kstats = _req(_opener(), 'GET', '/api/stats', headers={'Authorization': 'Bearer ' + key['key']}, expect=200)
        assert 'total_leads' in kstats
        _req(admin, 'DELETE', f'/api/admin/security/api-keys/{key["id"]}', expect=200)
        _req(_opener(), 'GET', '/api/stats', headers={'Authorization': 'Bearer ' + key['key']}, expect=401)

        # sessions: list and revoke the viewer's session
        _, sess = _req(admin, 'GET', '/api/admin/security/sessions', expect=200)
        vsessions = [s for s in sess['items'] if s['email'] == 'viewer@test.com' and not s['revoked_at']]
        assert vsessions
        _req(admin, 'DELETE', f'/api/admin/security/sessions/{vsessions[0]["id"]}', expect=200)
        _req(viewer, 'GET', '/api/leads', expect=401)  # session no longer valid

        # GDPR preview/export
        _req(admin, 'POST', '/api/prospects', {'name': 'Subject', 'email': 'subject@test.com'}, expect=201)
        _, prev = _req(admin, 'POST', '/api/admin/audit/gdpr/preview', {'email': 'subject@test.com'}, expect=200)
        assert prev['total'] >= 1
        _, exp = _req(admin, 'GET', '/api/admin/audit/gdpr/export?email=subject@test.com', expect=200)
        assert 'prospects' in exp['tables']

        # ops: metrics, health, backup
        _, m = _req(admin, 'GET', '/api/admin/ops/metrics', expect=200)
        assert m['total_requests'] > 0
        _, health = _req(admin, 'GET', '/api/admin/ops/health', expect=200)
        assert 'db_size_bytes' in health
        _, bk = _req(admin, 'POST', '/api/admin/security/backups', expect=201)
        assert bk['ok'] and bk['size_bytes'] > 0

        # size cap → 413
        big = _opener()
        big_body = json.dumps({'x': 'y' * (1024 * 1024 + 10)}).encode()
        req = urllib.request.Request(URL + '/api/prospects', data=big_body,
                                     headers={'Content-Type': 'application/json',
                                              'X-ArchAgent-Token': env['ARCHAGENT_TOKEN']}, method='POST')
        try:
            big.open(req, timeout=10)
            raise AssertionError('expected 413')
        except urllib.error.HTTPError as e:
            assert e.code == 413, e.code

        # rate limit → 429 on the expensive tier
        for _ in range(10):
            code, _ = _req(_opener(), 'POST', '/api/proposals/hermes', {'lead_notice_id': 'x'},
                           headers={'X-ArchAgent-Token': env['ARCHAGENT_TOKEN']})
            if code == 429:
                break
        assert code == 429, f'expected a 429 from the expensive tier, got {code}'

        print('  e2e: login/RBAC, admin CRUD, history, API keys, sessions, GDPR, ops, 413, 429 OK')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == '__main__':
    unit_tests()
    e2e_tests()
    print('PASS tests/test_admin.py: identity, RBAC, sessions, API keys, admin CRUD, audit/GDPR, ops, hardening')
