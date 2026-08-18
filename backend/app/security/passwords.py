"""Password hashing.

Argon2id, which is the current recommendation for password storage: it is
memory-hard, so a GPU or ASIC attacker gains far less over a defender than with
bcrypt or PBKDF2.
"""

from __future__ import annotations

import logging

from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# Parameters are deliberately explicit rather than left to passlib's defaults,
# so a library upgrade cannot silently weaken every hash the product writes.
# ~64 MB and 3 passes is the OWASP baseline; raise memory_cost first if the
# threat model tightens, since memory is what actually costs an attacker.
_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,  # 64 MiB
    argon2__time_cost=3,
    argon2__parallelism=4,
)


def hash_password(password: str) -> str:
    return _context.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    """Check a password against a stored hash.

    Returns False for a missing hash rather than raising. An SSO-provisioned
    user has no local password, and a login attempt against one must fail like
    any other wrong password — not with a distinguishable error that reveals
    the account uses SSO.
    """
    if not hashed:
        # Burn comparable time so "no password set" is not measurably faster
        # than "wrong password". Without this, response timing enumerates which
        # accounts are SSO-backed.
        _context.dummy_verify()
        return False
    try:
        return _context.verify(password, hashed)
    except ValueError:
        logger.warning("stored password hash is malformed")
        return False


def needs_rehash(hashed: str) -> bool:
    """True when a hash was made with weaker parameters than current policy.

    Called on successful login so hashes migrate transparently as the cost
    parameters above are raised over time.
    """
    return _context.needs_update(hashed)
