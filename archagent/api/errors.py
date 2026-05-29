"""Structured error taxonomy and helpers.

Error codes are stable machine strings decoupled from HTTP status. The serialized
body keeps the legacy ``error`` field (existing frontend reads it) and adds
``message``, ``request_id`` and optional ``details``.
"""
from __future__ import annotations

import uuid

# code → default HTTP status
CODES = {
    'validation_error': 400,
    'bad_request': 400,
    'unauthorized': 401,
    'forbidden': 403,
    'not_found': 404,
    'conflict': 409,
    'length_required': 411,
    'payload_too_large': 413,
    'rate_limited': 429,
    'internal_error': 500,
}


class ApiError(Exception):
    """An error carrying a stable code, HTTP status, message, and optional details."""

    def __init__(self, code: str, message: str = '', status: int | None = None, details=None):
        self.code = code
        self.status = status if status is not None else CODES.get(code, 400)
        self.message = message or code.replace('_', ' ')
        self.details = details
        super().__init__(self.message)

    def body(self, request_id: str | None = None) -> dict:
        return error_body(self.code, self.message, request_id, self.details)


def error_body(code: str, message: str, request_id: str | None = None, details=None) -> dict:
    out = {'error': code, 'message': message}
    if request_id:
        out['request_id'] = request_id
    if details is not None:
        out['details'] = details
    return out


def new_request_id() -> str:
    return uuid.uuid4().hex
