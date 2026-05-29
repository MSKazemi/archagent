"""Password hashing with PBKDF2-HMAC-SHA256 (pure stdlib).

Stores per-user salt + iteration count alongside the derived hash so the work
factor can be raised over time without invalidating old hashes. Verification is
constant-time via ``hmac.compare_digest``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# Work factor. OWASP-recommended floor for PBKDF2-HMAC-SHA256 is well above this;
# 240k keeps login latency acceptable for a stdlib server while staying defensible.
DEFAULT_ITERATIONS = 240_000
_SALT_BYTES = 16


def gen_salt() -> str:
    """Return a fresh random salt as hex."""
    return secrets.token_hex(_SALT_BYTES)


def _derive(password: str, salt_hex: str, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), iterations)
    return dk.hex()


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> tuple[str, str, int]:
    """Hash ``password``; return ``(hash_hex, salt_hex, iterations)``."""
    if not password:
        raise ValueError('password must not be empty')
    salt = gen_salt()
    return _derive(password, salt, iterations), salt, iterations


def verify_password(password: str, hash_hex: str, salt_hex: str, iterations: int) -> bool:
    """Constant-time verify of ``password`` against a stored hash."""
    if not (password and hash_hex and salt_hex and iterations):
        return False
    try:
        candidate = _derive(password, salt_hex, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, hash_hex)
