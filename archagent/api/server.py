"""ArchAgent HTTP server — Handler class and main() entry point.

All ``/api/*`` requests flow through a single ``_dispatch`` funnel:
  parse → size cap → resolve principal → rate limit → permission → route →
  respond → record metrics (in finally).
Legacy ``/admin/*`` pages and static files bypass the API funnel. The single shared
``ARCHAGENT_TOKEN`` still authenticates (as an admin-equivalent principal) for
backward compatibility while ``ARCHAGENT_LEGACY_TOKEN_ENABLED`` is on.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from archagent.core import config
from archagent.core.config import BASE, LEADS_DB, PUBLIC_PATHS
from archagent.core.db import app_conn, app_cursor, init_app_db, init_leads_db, now, rows_to_dicts
from archagent.core.rbac import has_permission, permission_for_route
from archagent.api import auth
from archagent.api import errors
from archagent.api.errors import ApiError
from archagent.api.metrics import metrics, path_group
from archagent.api.ratelimit import limiter, principal_key, tier_for
from archagent.api.pagination import list_table
from archagent.api.auth import (
    _admin_credentials,
    _admin_session_ok,
    _create_admin_session,
    _destroy_admin_session,
    _normalize_request_path,
    auth_enabled,
    read_json,
    validate_token_config,
)
from archagent.core import auth_db, sessions
from archagent.api.handlers import (
    admin_data,
    admin_router,
    bid_profiles,
    crm,
    dossiers,
    italy,
    leads,
    pilot,
    procurement,
    proposals,
    radar,
    workers,
)

_WRITE_METHODS = {'POST', 'PATCH', 'PUT', 'DELETE'}


def _canonical(path: str) -> str:
    """Collapse the optional /api/v1 alias onto the canonical /api path."""
    if path.startswith('/api/v1/'):
        return '/api/' + path[len('/api/v1/'):]
    return path


class Handler(SimpleHTTPRequestHandler):
    principal = None

    # ─── infra ───────────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        entry = {
            'time': now(),
            'client': self.client_address[0] if self.client_address else '',
            'method': getattr(self, 'command', ''),
            'path': self.path,
            'request_id': getattr(self, '_request_id', ''),
            'status': getattr(self, '_status', ''),
            'message': fmt % args,
        }
        print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    def end_headers(self):
        if not auth_enabled():
            self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-ArchAgent-Token')
        if getattr(self, '_request_id', None):
            self.send_header('X-Request-Id', self._request_id)
        super().end_headers()

    def json(self, data, status=200, extra_headers=None):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _err(self, code, message, status=None, details=None):
        body = errors.error_body(code, message, getattr(self, '_request_id', ''), details)
        return self.json(body, status or errors.CODES.get(code, 400))

    def _send_download(self, path, download_name):
        """Stream a file as an attachment (used for backup + export downloads)."""
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ─── size & body ───────────────────────────────────────────────────────────
    def _content_length(self) -> int:
        raw = self.headers.get('Content-Length')
        if raw is None:
            return 0  # bodyless request (e.g. DELETE, empty POST)
        try:
            return int(raw)
        except ValueError:
            raise ApiError('bad_request', 'invalid Content-Length')

    def _check_size(self, cpath: str) -> int:
        length = self._content_length()
        cap = config.MAX_UPLOAD_BYTES if cpath.startswith('/api/dossier/analyze') else config.MAX_JSON_BYTES
        if length > cap:
            raise ApiError('payload_too_large', f'request body exceeds {cap} bytes')
        return length

    # ─── legacy admin/static (bypass the API funnel) ─────────────────────────────
    def _host(self) -> str:
        try:
            return self.server.server_address[0]
        except Exception:
            return '127.0.0.1'

    def static_path_allowed(self, parsed):
        path = _normalize_request_path(parsed.path)
        public_paths = {'/', '/index.html', '/app', '/app.html', '/admin.html', '/favicon.ico',
                        '/frontend/index.html', '/frontend/app.html', '/frontend/admin.html'}
        if path in public_paths:
            return True
        # Public landing-page assets (3D library, fonts/images): the landing page at '/'
        # is public, so its static dependencies must be reachable without a token too.
        if path.startswith('/frontend/libs/'):
            return True
        if path.startswith('/exports/') or path.startswith('/frontend/'):
            return (not auth_enabled()) or auth.token_ok(self.headers)
        return False

    def _serve_admin_data(self, path: str):
        if path == '/admin/data/summary':
            s = leads.stats()
            con = app_conn()
            pilot_count = con.execute('SELECT COUNT(*) FROM pilot_requests').fetchone()[0]
            recent_pilots = rows_to_dicts(con.execute('SELECT * FROM pilot_requests ORDER BY id DESC LIMIT 10').fetchall())
            recent_activities = rows_to_dicts(con.execute('SELECT * FROM activities ORDER BY id DESC LIMIT 20').fetchall())
            worker_count = con.execute('SELECT COUNT(*) FROM expert_workers').fetchone()[0]
            bid_profile_count = con.execute('SELECT COUNT(*) FROM bid_profiles').fetchone()[0]
            con.close()
            return self.json({
                'stats': s,
                'pilot_requests': {'count': pilot_count, 'recent': recent_pilots},
                'activities': recent_activities,
                'worker_count': worker_count,
                'bid_profile_count': bid_profile_count,
            })
        if path == '/admin/data/pilot-requests':
            return self.json(list_table('pilot_requests'))
        if path == '/admin/data/activities':
            return self.json(list_table('activities'))
        return self.json({'error': 'not found'}, 404)

    def _handle_admin_login_post(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8', errors='replace')
        params = parse_qs(body)
        username = (params.get('user') or [''])[0].strip()
        password = (params.get('pass') or [''])[0].strip()
        expected_user, expected_pass = _admin_credentials()
        if not expected_pass:
            return self.json({'error': 'Admin access not configured. Set ADMIN_PASS in .env.'}, 403)
        import secrets as _secrets
        if not (_secrets.compare_digest(username, expected_user) and _secrets.compare_digest(password, expected_pass)):
            return self.json({'error': 'Invalid credentials'}, 401)
        token = _create_admin_session()
        self.json({'ok': True}, 200, extra_headers={
            'Set-Cookie': f'archagent_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})

    def _legacy_get(self, parsed, params):
        path = parsed.path
        if path in ('/', '/index.html'):
            self.path = '/frontend/index.html'
            return super().do_GET()
        if path in ('/app', '/app.html'):
            self.path = '/frontend/app.html'
            return super().do_GET()
        if path in ('/admin', '/admin/', '/admin.html'):
            self.path = '/frontend/admin.html'
            return super().do_GET()
        if path == '/admin/logout':
            _destroy_admin_session(self.headers)
            self.send_response(302)
            self.send_header('Location', '/admin')
            self.send_header('Set-Cookie', 'archagent_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            self.end_headers()
            return
        if path.startswith('/admin/data/'):
            if not _admin_session_ok(self.headers):
                return self.json({'error': 'unauthorized'}, 401)
            return self._serve_admin_data(path)
        if not self.static_path_allowed(parsed):
            return self.json({'error': 'not found'}, 404)
        return super().do_GET()

    # ─── auth routes ────────────────────────────────────────────────────────────
    def _handle_auth_login(self, payload):
        email = (payload or {}).get('email', '')
        password = (payload or {}).get('password', '')
        con = app_conn()
        try:
            try:
                user = auth_db.authenticate_user(con, email, password, self.client_address[0])
            except auth_db.LockedOut as exc:
                return self._err('rate_limited', str(exc), 429)
            except ValueError:
                return self._err('unauthorized', 'invalid credentials', 401)
            tok = sessions.create_session(con, user['id'], self.client_address[0],
                                          self.headers.get('User-Agent', ''))
            email_out, role_out = user['email'], user['role']
        finally:
            con.close()
        cookie = auth.session_cookie_header(tok, self._host())
        return self.json({'user': {'email': email_out, 'role': role_out}}, 200,
                         extra_headers={'Set-Cookie': cookie})

    def _handle_auth_logout(self):
        tok = auth._get_cookie(self.headers, config.SESSION_COOKIE)
        if tok:
            with app_cursor() as con:
                sessions.revoke_by_token(con, tok)
        return self.json({'ok': True}, 200,
                         extra_headers={'Set-Cookie': auth.clear_session_cookie_header(self._host())})

    # ─── HTTP verbs ──────────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_PATCH(self):
        self._dispatch('PATCH')

    def do_DELETE(self):
        self._dispatch('DELETE')

    # ─── the funnel ────────────────────────────────────────────────────────────
    def _dispatch(self, method):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        cpath = _canonical(parsed.path)
        self._request_id = errors.new_request_id()
        self._status = 200
        t0 = time.monotonic()
        metrics.inflight += 1
        try:
            # Non-API GET (static pages, legacy admin) bypass the API funnel.
            if method == 'GET' and not cpath.startswith('/api/'):
                return self._legacy_get(parsed, params)
            # Legacy admin form login (form-encoded, not /api).
            if method == 'POST' and parsed.path == '/admin/login':
                return self._handle_admin_login_post()
            if not cpath.startswith('/api/'):
                return self._err('not_found', 'not found', 404)

            ip = self.client_address[0] if self.client_address else ''
            # Size cap before reading any body.
            length = self._check_size(cpath) if method in _WRITE_METHODS else 0

            # Resolve principal once (used for rate-limit key + permission + audit).
            self.principal = auth.resolve_principal(self.headers, ip)

            # Rate limit (health is exempt).
            if cpath != '/api/health':
                ok, retry = limiter.allow(principal_key(self.principal, ip), tier_for(method, cpath))
                if not ok:
                    return self.json(
                        errors.error_body('rate_limited', 'too many requests', self._request_id),
                        429, extra_headers={'Retry-After': str(retry)})

            # Permission gate (public paths skip).
            is_public = cpath in PUBLIC_PATHS or (method == 'GET' and cpath == '/api/health')
            if not is_public:
                perm = permission_for_route(method, cpath)
                if not self.principal.is_authenticated:
                    return self._err('unauthorized', 'authentication required', 401)
                if perm and not has_permission(self.principal.role, perm):
                    return self._err('forbidden', f'missing permission: {perm}', 403)

            # Route by method.
            if method == 'GET':
                return self._route_get(cpath, params)
            if method == 'POST':
                return self._route_post(cpath, params, length)
            if method in ('PATCH', 'DELETE'):
                return self._route_admin_write(method, cpath, params)
            return self._err('not_found', 'method not supported', 405)
        except ApiError as exc:
            return self.json(exc.body(self._request_id), exc.status)
        except ValueError as exc:
            return self._err('bad_request', str(exc), 400)
        except BrokenPipeError:
            return
        except Exception as exc:
            self._log_internal_error(method, parsed.path, exc)
            return self._err('internal_error', 'an unexpected error occurred', 500)
        finally:
            metrics.inflight -= 1
            metrics.record(path_group(cpath), method, getattr(self, '_status', 200),
                           (time.monotonic() - t0) * 1000.0)

    def _log_internal_error(self, method, path, exc):
        import traceback
        tb = traceback.format_exc()
        print(f'[ERROR] {method} {path} ({self._request_id}): {exc!r}\n{tb}', file=sys.stderr, flush=True)
        metrics.record_error(method, path, 500, repr(exc), self._request_id)
        try:
            with app_cursor() as con:
                con.execute(
                    'INSERT INTO error_log(created_at,request_id,method,path,status,message,traceback,actor_user_id)'
                    ' VALUES (?,?,?,?,?,?,?,?)',
                    (now(), self._request_id, method, path, 500, repr(exc), tb,
                     getattr(self.principal, 'user_id', None)),
                )
        except Exception:
            pass

    # ─── GET routing ─────────────────────────────────────────────────────────────
    def _route_get(self, cpath, params):
        if cpath == '/api/health':
            health = {'ok': True, 'auth_enabled': auth_enabled(), 'time': now()}
            try:
                health['leads_db_updated_at'] = datetime.utcfromtimestamp(LEADS_DB.stat().st_mtime).strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception:
                pass
            return self.json(health)
        if cpath == '/api/auth/logout':
            return self._handle_auth_logout()
        # File downloads stream binary, so they bypass the JSON admin router.
        if cpath == '/api/admin/export/download':
            path, name = admin_data.resolve_export_path((params.get('name') or [''])[0])
            return self._send_download(path, name)
        if cpath.startswith('/api/admin/security/backups/') and cpath.endswith('/download'):
            bid = cpath[len('/api/admin/security/backups/'):-len('/download')]
            try:
                backup_id = int(bid)
            except ValueError:
                return self._err('bad_request', f'invalid id: {bid!r}', 400)
            path, name = admin_data.resolve_backup_path(backup_id)
            return self._send_download(path, name)
        if cpath.startswith('/api/admin/'):
            data, status = admin_router.dispatch('GET', cpath, params, {}, self.principal)
            return self.json(data, status)
        if cpath == '/api/stats':
            return self.json(leads.stats())
        if cpath == '/api/italy/summary':
            return self.json(italy.italy_summary())
        if cpath == '/api/leads':
            return self.json(leads.query_leads(params))
        if cpath == '/api/lead':
            return self.json(leads.get_lead((params.get('id') or [''])[0]) or {})
        if cpath == '/api/prospects':
            return self.json(list_table('prospects'))
        if cpath == '/api/customer-profiles':
            return self.json(list_table('customer_profiles'))
        if cpath == '/api/lead-radar/export':
            return self.json(radar.export_lead_radar(params))
        if cpath == '/api/lead-radar/exports':
            return self.json(list_table('lead_radar_exports'))
        if cpath == '/api/tender-dossiers':
            return self.json(list_table('tender_dossiers'))
        if cpath == '/api/followups':
            return self.json(list_table('followups'))
        if cpath == '/api/proposals':
            return self.json(list_table('proposals'))
        if cpath == '/api/proposals/export':
            return self.json(proposals.export_proposal(params))
        if cpath == '/api/contractors':
            return self.json(list_table('contractors'))
        if cpath == '/api/workers':
            return self.json(workers.query_workers(params))
        if cpath == '/api/workers/export':
            return self.json(workers.export_workers(params))
        if cpath == '/api/worker-stats':
            return self.json(workers.worker_stats())
        if cpath == '/api/worker-verifications':
            return self.json(list_table('worker_verifications'))
        if cpath == '/api/activities':
            return self.json(list_table('activities'))
        if cpath == '/api/bid-profiles':
            return self.json(list_table('bid_profiles'))
        if cpath == '/api/soa-categories':
            return self.json(procurement.soa_categories())
        if cpath == '/api/pilot-requests':
            return self.json(list_table('pilot_requests'))
        if cpath == '/api/dossier/jobs':
            con = app_conn()
            rows = rows_to_dicts(con.execute(
                'SELECT id,status,notice_id,bid_profile_id,error,created_at,started_at,completed_at FROM analysis_jobs ORDER BY id DESC LIMIT 40'
            ).fetchall())
            con.close()
            return self.json({'items': rows})
        if cpath.startswith('/api/dossier/jobs/'):
            try:
                return self.json(dossiers.get_analysis_job(int(cpath.split('/')[-1])))
            except (ValueError, TypeError) as exc:
                return self._err('not_found', str(exc), 404)
        return self._err('not_found', 'not found', 404)

    # ─── POST routing ────────────────────────────────────────────────────────────
    def _route_post(self, cpath, params, length):
        principal = self.principal
        if cpath == '/api/auth/login':
            return self._handle_auth_login(read_json(self))
        if cpath == '/api/auth/logout':
            return self._handle_auth_logout()
        if cpath.startswith('/api/admin/'):
            data, status = admin_router.dispatch('POST', cpath, params, read_json(self), principal)
            return self.json(data, status)

        # Multipart endpoints — handled before read_json.
        if cpath in ('/api/dossier/analyze', '/api/dossier/analyze/async'):
            ct = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in ct:
                return self._err('bad_request', 'Content-Type must be multipart/form-data', 400)
            body = self.rfile.read(length)
            try:
                parts = dossiers._parse_multipart(ct, body)
            except ValueError as exc:
                return self._err('bad_request', str(exc), 400)
            pdf_entry = parts.get('pdf_file') or parts.get('file')
            if not pdf_entry or not pdf_entry.get('data'):
                return self._err('bad_request', 'pdf_file field is required', 400)
            notice_id = (parts.get('notice_id') or {}).get('data', b'').decode(errors='replace').strip()
            bid_profile_id = (parts.get('bid_profile_id') or {}).get('data', b'').decode(errors='replace').strip() or None
            if cpath.endswith('/async'):
                return self.json(dossiers.submit_analysis_job(pdf_entry['data'], notice_id, bid_profile_id), 202)
            return self.json(dossiers.analyze_dossier(pdf_entry['data'], notice_id, bid_profile_id), 201)

        payload = read_json(self)
        if cpath == '/api/prospects':
            return self.json(crm.create_prospect(payload, principal), 201)
        if cpath == '/api/customer-profiles':
            return self.json(crm.create_customer_profile(payload, principal), 201)
        if cpath == '/api/followups':
            return self.json(crm.create_followup(payload, principal), 201)
        if cpath == '/api/proposals':
            return self.json(proposals.create_proposal(payload, use_hermes=False, principal=principal), 201)
        if cpath == '/api/proposals/hermes':
            return self.json(proposals.create_proposal(payload, use_hermes=True, principal=principal), 201)
        if cpath == '/api/tender-dossiers':
            return self.json(dossiers.create_tender_dossier_payload(payload, principal), 201)
        if cpath == '/api/workers/verify':
            return self.json(workers.verify_worker(payload, principal), 201)
        if cpath == '/api/compliance':
            return self.json(proposals.generate_compliance_for_payload(payload), 201)
        if cpath == '/api/procurement/analyze-text':
            return self.json(procurement.analyze_text(payload), 201)
        if cpath == '/api/audit':
            return self.json({'body': proposals.generate_building_audit_handler(payload)})
        if cpath == '/api/match':
            return self.json({'items': dossiers.match_contractors(payload)})
        if cpath == '/api/outreach':
            return self.json(dossiers.create_outreach_pack(payload, principal), 201)
        if cpath == '/api/dossier':
            return self.json(dossiers.create_project_dossier(payload, principal), 201)
        if cpath == '/api/bid-profiles':
            return self.json(bid_profiles.create_bid_profile(payload, principal), 201)
        if cpath == '/api/pilot-request':
            return self.json(pilot.create_pilot_request(payload), 201)
        return self._err('not_found', 'not found', 404)

    def _route_admin_write(self, method, cpath, params):
        if not cpath.startswith('/api/admin/'):
            return self._err('not_found', 'not found', 404)
        payload = read_json(self)
        data, status = admin_router.dispatch(method, cpath, params, payload, self.principal)
        return self.json(data, status)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=os.getenv('ARCHAGENT_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('ARCHAGENT_PORT', '8091')))
    args = parser.parse_args()
    validate_token_config(args.host)
    init_app_db()
    init_leads_db()
    os.chdir(BASE)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    def _graceful(signum, frame):
        print('\n[shutdown] draining…', file=sys.stderr, flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _graceful)
    signal.signal(signal.SIGTERM, _graceful)

    print(f'ArchAgent server running: http://{args.host}:{args.port}/app')
    print(f'API stats: http://{args.host}:{args.port}/api/stats')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        print('[shutdown] complete', file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
