"""Who is calling, and what they may do.

Sign-in itself is Neon Auth (Better Auth, email + password): the browser signs in there
and sends its short-lived JWT (15 min) to this server, as "Authorization: Bearer <jwt>"
on HTTP requests and in the first message on the interview WebSocket. The token is checked
against Neon Auth's public keys (EdDSA, NEON_AUTH_URL/.well-known/jwks.json).

Only verified email addresses get in (Neon Auth emails a 6-digit code; the token's
emailVerified claim must be true).

Roles are the app's own (Neon Auth has no custom roles): memberships in db.py give a user
roles per clinic; PLATFORM_ADMIN_EMAILS (comma-separated) are platform admins. Someone who
signs up on their own becomes a patient of the default clinic, unless an invitation for
their email is waiting (staff: see the invitation endpoints in api.py); staff roles come
from an invitation or are granted by a clinic admin or a platform admin.
"""
import asyncio
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import jwt
from fastapi import Header, HTTPException

import db


@dataclass
class User:
    id: str
    email: str
    name: str
    platform_admin: bool = False
    memberships: list = field(default_factory=list)  # [{"org_id", "org_name", "role"}]

    def orgs_with(self, *roles):
        """Clinics in which this user has any of these roles."""
        return {m["org_id"] for m in self.memberships if m["role"] in roles}

    def has_role(self, *roles):
        return bool(self.orgs_with(*roles))


_jwks = None


def _neon_auth_url():
    url = os.getenv("NEON_AUTH_URL")
    if not url:
        raise RuntimeError("NEON_AUTH_URL is not set (the Neon Auth URL, in .env)")
    return url.rstrip("/")


def _signing_key(token):
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{_neon_auth_url()}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks.get_signing_key_from_jwt(token)


async def verify_token(token):
    """The token's claims, if Neon Auth issued it and it has not expired; raises otherwise."""
    key = await asyncio.to_thread(_signing_key, token)  # fetches the public keys only when not cached
    parts = urlsplit(_neon_auth_url())
    origin = f"{parts.scheme}://{parts.netloc}"
    return jwt.decode(token, key.key, algorithms=["EdDSA"], issuer=origin, audience=origin,
                      options={"require": ["exp", "sub"]})


def _platform_admin(email):
    admins = {e.strip().lower() for e in os.getenv("PLATFORM_ADMIN_EMAILS", "").split(",") if e.strip()}
    return email.lower() in admins


# a user's clinic roles, kept briefly: every request needs them, and each database round trip
# is slow when the database is far away. Role changes show within ROLE_CACHE_SECONDS (at once
# for the user's own changes: forget()).
ROLE_CACHE_SECONDS = 30
_roles = {}  # user id -> (time read, memberships)


def forget(user_id):
    _roles.pop(str(user_id), None)


async def load_user(claims):
    """The app user for verified token claims, with their clinic roles. A new user who is not
    a platform admin becomes a patient of the default clinic."""
    if claims.get("banned"):
        raise HTTPException(403, "This account is blocked")
    if claims.get("emailVerified") is not True:
        raise HTTPException(403, {"code": "email_not_verified",
                                  "message": "Please verify your email address to continue."})
    user = User(id=claims["sub"], email=claims.get("email", ""), name=claims.get("name") or "",
                platform_admin=_platform_admin(claims.get("email", "")))
    cached = _roles.get(user.id)
    if cached and time.monotonic() - cached[0] < ROLE_CACHE_SECONDS:
        user.memberships = cached[1]
        return user
    database = await db.pool()
    # the roles, and (in the same round trip) whether an invitation is waiting for this email
    rows = await database.fetch(
        """
        SELECT m.org_id, o.name AS org_name, m.role,
               EXISTS (SELECT 1 FROM invitations WHERE lower(email) = lower($2) AND accepted_at IS NULL
                       AND expires_at > now()) AS invited
        FROM (SELECT 1) AS one
        LEFT JOIN memberships m ON m.user_id = $1
        LEFT JOIN organizations o ON o.org_id = m.org_id
        ORDER BY o.name, m.role
        """,
        user.id, user.email,
    )
    invited = rows[0]["invited"]
    rows = [r for r in rows if r["org_id"] is not None]
    if not rows and not user.platform_admin and not invited:
        # join the default clinic and read the new role back in the same statement
        rows = await database.fetch(
            """
            WITH joined AS (
                INSERT INTO memberships (user_id, org_id, role)
                SELECT $1, org_id, 'patient' FROM organizations WHERE is_default
                ON CONFLICT DO NOTHING
                RETURNING org_id, role
            )
            SELECT j.org_id, o.name AS org_name, j.role FROM joined j JOIN organizations o USING (org_id)
            """,
            user.id,
        )
        db.audit_later(user.id, "self_registered_as_patient")
    user.memberships = [{"org_id": str(r["org_id"]), "org_name": r["org_name"], "role": r["role"]} for r in rows]
    if user.memberships:  # none yet (an invitation to accept): ask again next time
        _roles[user.id] = (time.monotonic(), user.memberships)
    return user


async def user_from_token(token):
    try:
        claims = await verify_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Your sign-in has expired or is not valid. Please sign in again.")
    return await load_user(claims)


async def current_user(authorization: str | None = Header(default=None)) -> User:
    """FastAPI dependency: the signed-in user (401 if there is no valid Bearer token)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in.")
    return await user_from_token(authorization.split(" ", 1)[1].strip())
