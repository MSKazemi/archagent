"""Dispatcher for the ``/api/admin/*`` plane.

Returns ``(data, status)``; the server funnel handles principal resolution,
permission gating, and JSON serialization. Path params (``{name}``, ``{id}``) are
parsed here. Raises ``ApiError`` for unknown routes / bad ids.
"""
from __future__ import annotations

from archagent.api.errors import ApiError
from archagent.api.handlers import admin_audit, admin_data, admin_ops, admin_resources, admin_security
from archagent.core.rbac import ROLE_PERMISSIONS, has_permission


def _int(value: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ApiError('bad_request', f'invalid id: {value!r}')


def _me(principal) -> dict:
    role = getattr(principal, 'role', None)
    if role == 'admin':
        perms = ['*']
    else:
        perms = sorted(ROLE_PERMISSIONS.get(role, set()))
    return {
        'kind': getattr(principal, 'kind', 'anonymous'),
        'role': role,
        'email': getattr(principal, 'email', None),
        'permissions': perms,
    }


def dispatch(method: str, path: str, params: dict, payload: dict, principal) -> tuple:
    # Normalize: strip /api/v1 alias and the /api/admin prefix.
    if path.startswith('/api/v1/'):
        path = '/api/' + path[len('/api/v1/'):]
    tail = path[len('/api/admin'):].strip('/')  # e.g. 'resources/prospects/5'
    seg = tail.split('/') if tail else []
    m = method.upper()

    if seg == ['me']:
        return _me(principal), 200

    # ─── resources ───────────────────────────────────────────────────────────
    if seg and seg[0] == 'resources':
        if len(seg) == 1:
            return admin_resources.list_resources(), 200
        name = seg[1]
        if len(seg) == 2:
            if m == 'GET':
                return admin_resources.list_rows(name, params), 200
            if m == 'POST':  # /resources/{name}/bulk arrives as len 3; this is unused
                raise ApiError('not_found', 'unknown admin route')
        if len(seg) == 3 and seg[2] == 'bulk' and m == 'POST':
            return admin_resources.bulk(name, payload, principal), 200
        if len(seg) == 3:
            rid = _int(seg[2])
            if m == 'GET':
                return admin_resources.get_row(name, rid), 200
            if m == 'PATCH':
                return admin_resources.update_row(name, rid, payload, principal), 200
            if m == 'DELETE':
                return admin_resources.delete_row(name, rid, principal), 200
        if len(seg) == 4 and seg[3] == 'restore' and m == 'POST':
            return admin_resources.restore_row(name, _int(seg[2]), principal), 200
        raise ApiError('not_found', 'unknown admin resource route')

    # ─── ops ─────────────────────────────────────────────────────────────────
    if seg and seg[0] == 'ops':
        if seg[1:] == ['metrics'] and m == 'GET':
            return admin_ops.get_metrics(), 200
        if seg[1:] == ['errors'] and m == 'GET':
            return admin_ops.get_errors(params), 200
        if seg[1:] == ['health'] and m == 'GET':
            return admin_ops.get_health(), 200
        if seg[1:] == ['jobs'] and m == 'GET':
            return admin_ops.list_jobs(params), 200
        if len(seg) == 4 and seg[1] == 'jobs' and m == 'POST':
            jid = _int(seg[2])
            if seg[3] == 'retry':
                return admin_ops.retry_job(jid), 200
            if seg[3] == 'cancel':
                return admin_ops.cancel_job(jid), 200
        if seg[1:] == ['db-stats'] and m == 'GET':
            return admin_data.db_stats(), 200
        if seg[1:] == ['retention'] and m == 'GET':
            return admin_data.retention_preview(params), 200
        if seg[1:] == ['retention', 'purge'] and m == 'POST':
            return admin_data.retention_purge(payload, principal), 200
        raise ApiError('not_found', 'unknown admin ops route')

    # ─── export ──────────────────────────────────────────────────────────────
    if seg and seg[0] == 'export':
        # NB: /export/download is intercepted in the server (streams a file), not here.
        if len(seg) == 1 and m == 'GET':
            return admin_data.list_exportable(), 200
        if seg[1:] == ['all'] and m == 'POST':
            return admin_data.export_all(principal), 200
        if len(seg) == 3 and m == 'GET':
            return admin_data.export_table(seg[1], seg[2], params, principal), 200
        raise ApiError('not_found', 'unknown admin export route')

    # ─── audit ───────────────────────────────────────────────────────────────
    if seg and seg[0] == 'audit':
        rest = seg[1:]
        if rest == ['activities'] and m == 'GET':
            return admin_audit.list_activities(params), 200
        if rest == ['logins'] and m == 'GET':
            return admin_audit.list_logins(params), 200
        if rest == ['history'] and m == 'GET':
            return admin_audit.change_history(params), 200
        if rest == ['gdpr', 'export'] and m == 'GET':
            return admin_audit.gdpr_export(params), 200
        if rest == ['gdpr', 'preview'] and m == 'POST':
            return admin_audit.gdpr_preview(payload), 200
        if rest == ['gdpr', 'erase'] and m == 'POST':
            return admin_audit.gdpr_erase(payload, principal), 200
        raise ApiError('not_found', 'unknown admin audit route')

    # ─── security ──────────────────────────────────────────────────────────────
    if seg and seg[0] == 'security':
        rest = seg[1:]
        if rest == ['api-keys']:
            if m == 'GET':
                return admin_security.list_keys(), 200
            if m == 'POST':
                return admin_security.create_key(payload, principal), 201
        if len(rest) == 3 and rest[0] == 'api-keys' and rest[2] == 'rotate' and m == 'POST':
            return admin_security.rotate_key(_int(rest[1]), principal), 200
        if len(rest) == 2 and rest[0] == 'api-keys' and m == 'DELETE':
            return admin_security.revoke_key(_int(rest[1]), principal), 200
        if rest == ['users']:
            if m == 'GET':
                return admin_security.list_users(), 200
            if m == 'POST':
                return admin_security.create_user(payload, principal), 201
        if len(rest) == 2 and rest[0] == 'users' and m == 'PATCH':
            return admin_security.update_user(_int(rest[1]), payload, principal), 200
        if rest == ['sessions'] and m == 'GET':
            return admin_security.list_sessions_handler(), 200
        if len(rest) == 2 and rest[0] == 'sessions' and m == 'DELETE':
            return admin_security.revoke_session(_int(rest[1]), principal), 200
        if rest == ['rate-limit']:
            if m == 'GET':
                return admin_security.get_rate_limit(), 200
            if m == 'PATCH':
                return admin_security.update_rate_limit(payload, principal), 200
        if rest == ['feature-flags'] and m == 'GET':
            return admin_security.list_flags(), 200
        if len(rest) == 2 and rest[0] == 'feature-flags' and m == 'PATCH':
            return admin_security.set_flag(rest[1], payload, principal), 200
        if rest == ['config-status'] and m == 'GET':
            return admin_security.config_status(), 200
        if rest == ['backups']:
            if m == 'GET':
                return admin_security.list_backups(), 200
            if m == 'POST':
                return admin_security.create_backup(principal), 201
        if rest == ['backups', 'rescan'] and m == 'POST':
            return admin_data.rescan_backups(principal), 200
        if len(rest) == 3 and rest[0] == 'backups' and rest[2] == 'verify' and m == 'POST':
            return admin_data.verify_backup(_int(rest[1]), principal), 200
        if len(rest) == 3 and rest[0] == 'backups' and rest[2] == 'restore' and m == 'POST':
            return admin_data.restore_backup(_int(rest[1]), payload, principal), 200
        raise ApiError('not_found', 'unknown admin security route')

    raise ApiError('not_found', 'unknown admin route')
