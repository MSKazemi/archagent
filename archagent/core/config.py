"""Central configuration: paths, env loading, shared constants."""
from __future__ import annotations

import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
LEADS_DB = Path(os.getenv('ARCHAGENT_LEADS_DB') or (BASE / 'archagent_actionable_projects.sqlite3'))
APP_DB = Path(os.getenv('ARCHAGENT_APP_DB') or (BASE / 'archagent_app.sqlite3'))
EXPORT_DIR = BASE / 'exports'

PRIVATE_API_PREFIXES = ('/api/',)
PUBLIC_PATHS = {'/api/health', '/api/pilot-request', '/api/auth/login'}

BACKUP_DIR = BASE / 'backups'

# ─── Identity / session / security tunables (env-overridable) ───────────────────
SESSION_COOKIE = 'archagent_session'


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, '').strip() or default)
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def session_ttl_hours() -> int:
    return _env_int('ARCHAGENT_SESSION_TTL_HOURS', 8)


def legacy_token_enabled() -> bool:
    """Whether the single shared ARCHAGENT_TOKEN still authenticates (admin-equiv)."""
    return _env_flag('ARCHAGENT_LEGACY_TOKEN_ENABLED', True)


def secure_cookies(host: str = '127.0.0.1') -> bool:
    """Add the Secure cookie flag unless explicitly disabled or bound to localhost."""
    raw = os.getenv('ARCHAGENT_SECURE_COOKIES', '').strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return host not in {'127.0.0.1', 'localhost', '::1'}


def bootstrap_admin_credentials() -> tuple[str, str]:
    """Email + password for the first admin user, seeded on first run if set."""
    return (
        os.getenv('ARCHAGENT_ADMIN_EMAIL', '').strip(),
        os.getenv('ARCHAGENT_ADMIN_PASSWORD', '').strip(),
    )


# Login throttling: >= LOGIN_MAX_FAILURES failures within LOGIN_WINDOW_MINUTES → lock.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_MINUTES = 15

# Request size caps.
MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def load_env_file(path: Path = BASE / '.env') -> None:
    """Load KEY=VALUE lines from .env without overriding real environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
