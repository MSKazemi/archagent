"""Authentication, token validation, session management, and request parsing helpers."""
from __future__ import annotations

import json
import os
import posixpath
import secrets
import time
from dataclasses import dataclass
from urllib.parse import unquote

from archagent.core import config
from archagent.core.config import PRIVATE_API_PREFIXES, PUBLIC_PATHS

_LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}

# ─── Admin sessions ────────────────────────────────────────────────────────────
_ADMIN_SESSIONS: dict = {}  # token -> expiry_timestamp


def configured_token() -> str:
    return os.getenv('ARCHAGENT_TOKEN', '').strip()


def token_is_placeholder(token: str) -> bool:
    return token in {'change-me-use-openssl-rand-hex-32', 'replace-with-long-random-token', 'changeme'}


# Credentials that appear in the docs, in examples, or at the top of every wordlist.
# Seeding an admin account with one of these is the single most common way a
# self-hosted deployment gets taken over.
_WEAK_PASSWORDS = {
    'admin', 'password', 'changeme', 'change-me', 'secret', '123456', '12345678',
    'letmein', 'archagent', 'choose-a-strong-password', 'your-password', 'test',
}


def validate_admin_credentials(host: str = '127.0.0.1') -> None:
    """Reject weak bootstrap-admin passwords; warn about them even on localhost.

    Only applies to the *bootstrap* password read from the environment on first
    run — passwords set later through the admin console are not affected.
    """
    import sys
    password = os.getenv('ARCHAGENT_ADMIN_PASSWORD', '')
    if not password:
        return
    is_local = host in _LOCAL_HOSTS
    weak = password.strip().lower() in _WEAK_PASSWORDS
    short = len(password) < 12
    if not (weak or short):
        return
    reason = 'a well-known default' if weak else f'shorter than 12 characters (length: {len(password)})'
    if not is_local:
        raise SystemExit(
            f'Refusing to bind {host!r} with an ARCHAGENT_ADMIN_PASSWORD that is {reason}. '
            'Choose a strong password, e.g. '
            "python3 -c 'import secrets; print(secrets.token_urlsafe(24))'"
        )
    print(f'[WARNING] ARCHAGENT_ADMIN_PASSWORD is {reason}. This is only tolerated on '
          'localhost — change it before exposing this deployment to any network.',
          file=sys.stderr, flush=True)


def validate_token_config(host: str = '127.0.0.1') -> None:
    import sys
    token = configured_token()
    is_local = host in _LOCAL_HOSTS
    if token_is_placeholder(token):
        raise SystemExit('Refusing to start with placeholder ARCHAGENT_TOKEN. Generate a real token or unset ARCHAGENT_TOKEN for local unauthenticated development.')
    if not is_local:
        if not token:
            if os.getenv('ARCHAGENT_ALLOW_UNAUTHENTICATED_PUBLIC') != '1':
                raise SystemExit(
                    f'Refusing to bind {host!r} without ARCHAGENT_TOKEN. '
                    'Set a strong token (openssl rand -hex 32) or set '
                    'ARCHAGENT_ALLOW_UNAUTHENTICATED_PUBLIC=1 to override (not recommended).'
                )
            print('[WARNING] Binding to public interface without authentication. '
                  'Set ARCHAGENT_TOKEN for any non-local deployment.', file=sys.stderr, flush=True)
        elif len(token) < 32:
            raise SystemExit(
                f'Refusing to start: ARCHAGENT_TOKEN must be at least 32 characters for '
                f'non-local deployments (current length: {len(token)}). '
                'Generate one with: openssl rand -hex 32'
            )
    else:
        if not token:
            print('[WARNING] Running unauthenticated on localhost. '
                  'Set ARCHAGENT_TOKEN before exposing to any network.', file=sys.stderr, flush=True)


def auth_enabled() -> bool:
    return bool(configured_token())


def token_ok(headers) -> bool:
    token = configured_token()
    if not token:
        return True
    supplied = headers.get('X-ArchAgent-Token') or ''
    auth = headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        supplied = auth.split(' ', 1)[1]
    return secrets.compare_digest(supplied, token)


def parse_int_param(params: dict, key: str, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    raw = (params.get(key) or [str(default)])[0]
    try:
        val = int(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid value for '{key}': expected integer")
    if min_val is not None and val < min_val:
        raise ValueError(f"Parameter '{key}' must be >= {min_val}")
    if max_val is not None and val > max_val:
        val = max_val
    return val


def parse_float_param(params: dict, key: str, default: float | None = None) -> float | None:
    raw = (params.get(key) or [''])[0]
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid value for '{key}': expected number")


def parse_bool_param(params: dict, key: str, default: bool = False) -> bool:
    raw = (params.get(key) or [''])[0].strip().lower()
    if not raw:
        return default
    if raw in ('1', 'true', 'yes'):
        return True
    if raw in ('0', 'false', 'no'):
        return False
    raise ValueError(f"Invalid value for '{key}': expected 0 or 1")


def _normalize_request_path(raw_path: str) -> str:
    """URL-decode and posix-normalize a request path to catch encoded traversal."""
    return posixpath.normpath(unquote(raw_path))


def _admin_credentials() -> tuple:
    return (
        os.environ.get('ADMIN_USER', 'admin').strip(),
        os.environ.get('ADMIN_PASS', '').strip(),
    )


def _get_cookie(headers, name: str) -> str:
    cookie_str = headers.get('Cookie') or ''
    for part in cookie_str.split(';'):
        kv = part.strip().split('=', 1)
        if len(kv) == 2 and kv[0].strip() == name:
            return kv[1].strip()
    return ''


def _admin_session_ok(headers) -> bool:
    token = _get_cookie(headers, 'archagent_admin')
    if not token:
        return False
    exp = _ADMIN_SESSIONS.get(token)
    if exp is None or time.time() > exp:
        _ADMIN_SESSIONS.pop(token, None)
        return False
    return True


def _create_admin_session() -> str:
    token = secrets.token_hex(32)
    _ADMIN_SESSIONS[token] = time.time() + 8 * 3600  # 8 h TTL
    # Prune expired sessions
    expired = [t for t, exp in list(_ADMIN_SESSIONS.items()) if time.time() > exp]
    for t in expired:
        _ADMIN_SESSIONS.pop(t, None)
    return token


def _destroy_admin_session(headers) -> None:
    token = _get_cookie(headers, 'archagent_admin')
    if token:
        _ADMIN_SESSIONS.pop(token, None)


def read_json(handler) -> dict:
    length = int(handler.headers.get('Content-Length') or 0)
    return json.loads(handler.rfile.read(length).decode('utf-8')) if length else {}


# ─── Request principal & RBAC resolution ─────────────────────────────────────────

@dataclass(frozen=True)
class Principal:
    kind: str            # 'user' | 'api_key' | 'legacy_token' | 'anonymous'
    role: str | None     # 'admin'|'analyst'|'sales'|'viewer' or None for anonymous
    user_id: int | None = None
    api_key_id: int | None = None
    email: str | None = None
    ip: str = ''

    @property
    def actor_type(self) -> str:
        return {'user': 'user', 'api_key': 'api_key', 'legacy_token': 'legacy_token'}.get(
            self.kind, 'anonymous')

    @property
    def is_authenticated(self) -> bool:
        return self.kind != 'anonymous'


ANONYMOUS = Principal(kind='anonymous', role=None)


def _bearer_or_token(headers) -> str:
    supplied = (headers.get('X-ArchAgent-Token') or '').strip()
    auth = headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        supplied = auth.split(' ', 1)[1].strip()
    return supplied


def _dev_mode_admin(con) -> bool:
    """Local, no token configured, and no real users → implicit admin (dev convenience)."""
    if configured_token():
        return False
    if con.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
        return False
    return True


def resolve_principal(headers, ip: str = '') -> Principal:
    """Resolve the request principal from session cookie, API key, or legacy token."""
    from archagent.core.db import app_conn
    from archagent.core import sessions, auth_db

    con = app_conn()
    try:
        # 1. Session cookie.
        token = _get_cookie(headers, config.SESSION_COOKIE)
        if token:
            row = sessions.resolve_session(con, token)
            if row is not None:
                return Principal(kind='user', role=row['role'], user_id=row['user_id'],
                                 email=row['email'], ip=ip)
        # 2. Scoped API key (prefix.secret).
        presented = _bearer_or_token(headers)
        if presented and '.' in presented:
            key = auth_db.resolve_api_key(con, presented)
            if key is not None:
                return Principal(kind='api_key', role=key['role'], api_key_id=key['id'], ip=ip)
        # 3. Legacy single shared token → admin-equivalent.
        legacy = configured_token()
        if legacy and config.legacy_token_enabled() and presented and secrets.compare_digest(presented, legacy):
            return Principal(kind='legacy_token', role='admin', ip=ip)
        # 4. Dev-mode implicit admin (local, unauthenticated, no users yet).
        if presented == '' and token == '' and _dev_mode_admin(con):
            return Principal(kind='legacy_token', role='admin', ip=ip)
    finally:
        con.close()
    return Principal(kind='anonymous', role=None, ip=ip)


def session_cookie_header(token: str, host: str = '127.0.0.1', max_age: int | None = None) -> str:
    """Build a Set-Cookie header value for the session token."""
    if max_age is None:
        max_age = config.session_ttl_hours() * 3600
    parts = [f'{config.SESSION_COOKIE}={token}', 'HttpOnly', 'SameSite=Strict', 'Path=/', f'Max-Age={max_age}']
    if config.secure_cookies(host):
        parts.append('Secure')
    return '; '.join(parts)


def clear_session_cookie_header(host: str = '127.0.0.1') -> str:
    return session_cookie_header('', host, max_age=0)
