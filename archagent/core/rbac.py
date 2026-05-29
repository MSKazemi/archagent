"""Role-based access control: roles, permission matrix, and route→permission mapping.

Permissions are coarse ``resource:verb`` strings. ``admin`` is a wildcard — it is
granted every permission implicitly so new permissions never require editing its
row. Roles map to an explicit set of permission strings.
"""
from __future__ import annotations

import re

ROLES = ('admin', 'analyst', 'sales', 'viewer')

# Read permissions every signed-in role holds.
_READS = {
    'leads:read', 'proposals:read', 'dossiers:read', 'crm:read',
    'workers:read', 'radar:read', 'bid_profiles:read',
}
# Writes available to the "sales" persona.
_SALES_WRITES = {'crm:write', 'proposals:write', 'radar:write'}
# Writes available to the "analyst" persona (a superset of sales-ish writes).
_ANALYST_WRITES = _SALES_WRITES | {
    'dossiers:write', 'workers:write', 'bid_profiles:read', 'bid_profiles:write',
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    'admin': set(),  # wildcard handled in has_permission
    'analyst': _READS | _ANALYST_WRITES,
    'sales': _READS | _SALES_WRITES,
    'viewer': set(_READS),
}

# Admin-only capabilities (only the wildcard admin role holds these).
ADMIN_ONLY = {
    'users:manage', 'apikeys:manage', 'admin:ops',
    'resources:read', 'resources:write',
    'ops:read', 'ops:write',
    'audit:read', 'compliance:export', 'compliance:erase',
    'security:read', 'security:write',
}


def has_permission(role: str | None, permission: str | None) -> bool:
    """True if ``role`` grants ``permission``. ``admin`` grants everything."""
    if role == 'admin':
        return True
    if not role or not permission:
        return False
    return permission in ROLE_PERMISSIONS.get(role, set())


# ─── Route → required permission ────────────────────────────────────────────────
# Resolved against the normalized request path. Most routes follow a simple
# "GET => *:read, POST => *:write" convention keyed by an /api/<group> prefix.
_GROUP_FOR_PREFIX = (
    ('/api/leads', 'leads'),
    ('/api/lead', 'leads'),
    ('/api/stats', 'leads'),
    ('/api/italy', 'leads'),
    ('/api/proposals', 'proposals'),
    ('/api/compliance', 'proposals'),
    ('/api/audit', 'proposals'),
    ('/api/dossier', 'dossiers'),
    ('/api/tender-dossiers', 'dossiers'),
    ('/api/match', 'dossiers'),
    ('/api/outreach', 'dossiers'),
    ('/api/prospects', 'crm'),
    ('/api/customer-profiles', 'crm'),
    ('/api/followups', 'crm'),
    ('/api/workers', 'workers'),
    ('/api/worker-stats', 'workers'),
    ('/api/worker-verifications', 'workers'),
    ('/api/lead-radar', 'radar'),
    ('/api/bid-profiles', 'bid_profiles'),
    ('/api/activities', 'leads'),  # read-only audit feed for the workspace
    ('/api/contractors', 'workers'),
    ('/api/pilot-requests', 'crm'),
)

# Admin-plane prefixes → explicit permission (verb decided per HTTP method below).
_ADMIN_PERMS = (
    ('/api/admin/resources', ('resources:read', 'resources:write')),
    ('/api/admin/export', ('ops:read', 'ops:write')),
    ('/api/admin/ops', ('ops:read', 'ops:write')),
    ('/api/admin/audit/gdpr/export', ('compliance:export', 'compliance:export')),
    ('/api/admin/audit/gdpr/preview', ('compliance:export', 'compliance:export')),
    ('/api/admin/audit/gdpr/erase', ('compliance:erase', 'compliance:erase')),
    ('/api/admin/audit', ('audit:read', 'audit:read')),
    ('/api/admin/security', ('security:read', 'security:write')),
    ('/api/admin/me', ('leads:read', 'leads:read')),
)

_WRITE_METHODS = {'POST', 'PATCH', 'PUT', 'DELETE'}


def _strip_version(path: str) -> str:
    """Map an optional ``/api/v1/...`` alias back to the canonical ``/api/...``."""
    return re.sub(r'^/api/v1/', '/api/', path)


def permission_for_route(method: str, path: str) -> str | None:
    """Return the permission string a route requires, or None if unmapped/public."""
    path = _strip_version(path)
    is_write = method.upper() in _WRITE_METHODS
    for prefix, (read_perm, write_perm) in _ADMIN_PERMS:
        if path == prefix or path.startswith(prefix + '/'):
            return write_perm if is_write else read_perm
    for prefix, group in _GROUP_FOR_PREFIX:
        if path == prefix or path.startswith(prefix + '/'):
            return f'{group}:write' if is_write else f'{group}:read'
    # Unknown /api route: default to a read gate so it still requires a principal.
    if path.startswith('/api/'):
        return 'leads:write' if is_write else 'leads:read'
    return None
