"""Signup, login, and token refresh.

Authentication sits awkwardly against Row-Level Security, and both flows here
resolve that deliberately rather than by weakening the policies:

* **Signup** writes the first rows of a tenant that does not exist yet. An
  unscoped session cannot insert them — the WITH CHECK predicate compares
  ``tenant_id`` against an unset GUC and fails. So the service generates the
  tenant's UUID itself, sets ``app.tenant_id`` to it, and then inserts. Both
  policies pass by the normal path; no exception, and no window in which an
  unscoped session may write.

* **Login** is inherently cross-tenant: the tenant is unknown until the user is
  identified. It goes through ``auth_lookup_user``, a SECURITY DEFINER function
  that returns exactly the six columns authentication needs and nothing else.
  The alternative — granting the app BYPASSRLS — would void every policy in the
  system to solve one query.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models.enums import UserRole
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.session import privileged_session, tenant_session
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.tokens import decode_token, issue_access_token, issue_refresh_token

logger = logging.getLogger(__name__)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


class InvalidCredentialsError(AppError):
    """Wrong email, wrong password, disabled account, unknown tenant.

    One error for all of them, deliberately. Distinguishing "no such user" from
    "wrong password" turns the login form into an account-enumeration oracle.
    """

    slug = "invalid_credentials"
    status_code = 401
    user_message = "Email or password is incorrect."


class EmailAlreadyRegisteredError(AppError):
    slug = "email_taken"
    status_code = 409
    user_message = "An account with this email already exists in this organisation."


class TenantSlugTakenError(AppError):
    slug = "slug_taken"
    status_code = 409
    user_message = "That organisation name is already taken."


@dataclass(slots=True)
class AuthResult:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: UserRole


def _slugify(name: str) -> str:
    base = _SLUG_UNSAFE.sub("-", name.strip().lower()).strip("-")[:40] or "org"
    # A short random suffix rather than a counter: a counter leaks how many
    # organisations share a name, and a probe loop can enumerate them.
    return f"{base}-{uuid.uuid4().hex[:6]}"


class AuthService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # ----------------------------------------------------------------- signup
    async def register_organisation(
        self, *, organisation_name: str, email: str, full_name: str, password: str
    ) -> AuthResult:
        """Create a tenant and its first user, who becomes the owner.

        Both rows are written in one transaction. A tenant without an owner is
        unreachable — nobody could ever sign in to it — so a partial commit
        would silently orphan a paying customer's organisation.

        Note there is no "email already registered" check across tenants, and
        that is correct rather than an omission: the unique constraint is on
        ``(tenant_id, email)``. The same person legitimately holds accounts in
        several organisations — a consultancy serving competing bidders — and
        a global check would block exactly that case.
        """
        email = email.strip().lower()
        slug = _slugify(organisation_name)
        # Generated here, not by the database, so the GUC can be set to it
        # before the INSERT that the policy will check against.
        tenant_id = uuid.uuid4()

        async with tenant_session(tenant_id, self._settings) as session:
            tenant = Tenant(id=tenant_id, name=organisation_name.strip(), slug=slug)
            session.add(tenant)
            await session.flush()

            user = User(
                tenant_id=tenant_id,
                email=email,
                full_name=full_name.strip(),
                hashed_password=hash_password(password),
                # The creator is the owner: someone must be able to invite the
                # team and manage billing from the first minute.
                role=UserRole.OWNER,
            )
            session.add(user)
            await session.flush()

            logger.info("registered organisation %s (tenant %s)", slug, tenant_id)
            return self._issue(user)

    # ------------------------------------------------------------------ login
    async def authenticate(
        self, *, email: str, password: str, tenant_slug: str | None = None
    ) -> AuthResult:
        """Verify credentials and issue a token pair."""
        email = email.strip().lower()

        async with privileged_session(self._settings) as session:
            # SECURITY DEFINER function — the only cross-tenant read in the
            # application, returning six columns and nothing more.
            rows = (
                (await session.execute(text("SELECT * FROM auth_lookup_user(:e)"), {"e": email}))
                .mappings()
                .all()
            )

            candidate = None
            if tenant_slug:
                candidate = next((r for r in rows if r["tenant_slug"] == tenant_slug), None)
            elif len(rows) == 1:
                candidate = rows[0]
            elif len(rows) > 1:
                # The same person may hold accounts in several organisations —
                # a consultancy serving multiple bidders. Without a slug we
                # cannot know which, and guessing would sign them into the
                # wrong customer's data.
                raise InvalidCredentialsError(
                    "this email exists in more than one organisation; specify which one"
                )

            # Always run the hash comparison, even with no candidate. Skipping
            # it makes "unknown email" measurably faster than "wrong password",
            # which enumerates accounts by response time.
            stored = candidate["hashed_password"] if candidate else None
            if not verify_password(password, stored):
                raise InvalidCredentialsError()

            assert candidate is not None  # noqa: S101 - narrowed by the check above
            if not candidate["user_active"] or not candidate["tenant_active"]:
                raise InvalidCredentialsError()

            if needs_rehash(stored or ""):
                # Transparent upgrade as cost parameters are raised over time.
                # These writes are tenant-scoped, so they need the GUC the
                # lookup did not require.
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(candidate["tenant_id"])},
                )
                await session.execute(
                    text("UPDATE users SET hashed_password = :h WHERE id = :i"),
                    {"h": hash_password(password), "i": candidate["user_id"]},
                )

            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(candidate["tenant_id"])},
            )
            await session.execute(
                text("UPDATE users SET last_login_at = now() WHERE id = :i"),
                {"i": candidate["user_id"]},
            )

            return AuthResult(
                *self._tokens(
                    user_id=candidate["user_id"],
                    tenant_id=candidate["tenant_id"],
                    email=candidate["email"],
                    role=candidate["role"],
                ),
                user_id=candidate["user_id"],
                tenant_id=candidate["tenant_id"],
                email=candidate["email"],
                role=UserRole(candidate["role"]),
            )

    # ---------------------------------------------------------------- refresh
    async def refresh(self, refresh_token: str) -> AuthResult:
        """Exchange a refresh token for a fresh pair.

        Email and role are re-read from the database rather than copied from
        the token. A role demoted last week must not survive in a token minted
        a month ago.
        """
        claims = decode_token(refresh_token, expected_type="refresh", settings=self._settings)

        async with tenant_session(claims.tenant_id, self._settings) as session:
            user = (
                await session.execute(
                    select(User).where(User.id == claims.user_id, User.deleted_at.is_(None))
                )
            ).scalar_one_or_none()

            if user is None or not user.is_active:
                raise InvalidCredentialsError("account is no longer active")

            return self._issue(user)

    # -------------------------------------------------------------- internals
    def _tokens(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str, role: str
    ) -> tuple[str, str, int]:
        access, ttl = issue_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            email=email,
            role=role,
            settings=self._settings,
        )
        refresh = issue_refresh_token(user_id=user_id, tenant_id=tenant_id, settings=self._settings)
        return access, refresh, ttl

    def _issue(self, user: User) -> AuthResult:
        access, refresh, ttl = self._tokens(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            role=user.role.value,
        )
        return AuthResult(
            access_token=access,
            refresh_token=refresh,
            expires_in=ttl,
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            role=user.role,
        )
