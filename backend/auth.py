"""
Authentication for the reporter Space.

A Hugging Face Space URL is public. Without validation on every call,
anyone can burn the CPU quota and render reports for laboratories they
have nothing to do with. So every endpoint validates the Supabase JWT
before doing any work at all.

Roles are read from app_metadata, never user_metadata: a signed-in user
can edit user_metadata through the normal client API, so an authorisation
claim living there would be self-issued.

This mirrors app.role() and app.lab_id() in
supabase/migrations/0002_functions.sql, so the Space and the database
agree on who someone is.
"""

import os
from dataclasses import dataclass
from typing import Optional, Sequence

import jwt

JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
AUDIENCE = "authenticated"


class AuthError(Exception):
    """Raised for a missing, malformed, expired or under-privileged token."""


@dataclass(frozen=True)
class Caller:
    user_id: str
    role: str
    lab_id: Optional[str]
    email: Optional[str]

    def is_(self, *roles: str) -> bool:
        return self.role in roles


def verify(token: str) -> Caller:
    """Decode and validate a Supabase access token."""
    if not JWT_SECRET:
        raise AuthError(
            "SUPABASE_JWT_SECRET is not set on this Space. "
            "Add it under Settings, Repository secrets."
        )
    if not token or not isinstance(token, str):
        raise AuthError("no access token supplied")

    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        claims = jwt.decode(
            token, JWT_SECRET, algorithms=["HS256"], audience=AUDIENCE
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("access token has expired; sign in again")
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid access token: {exc}")

    app_meta = claims.get("app_metadata") or {}
    role = app_meta.get("lab_role")
    if not role:
        raise AuthError(
            "token carries no lab_role in app_metadata. "
            "Run supabase/seed/create_users.py, or set the claim with the "
            "service-role key."
        )

    return Caller(
        user_id=claims.get("sub", ""),
        role=role,
        lab_id=app_meta.get("lab_id"),
        email=claims.get("email"),
    )


def require(token: str, *allowed: str) -> Caller:
    """Validate the token and assert the caller holds one of `allowed`."""
    caller = verify(token)
    if allowed and not caller.is_(*allowed):
        raise AuthError(
            f"role '{caller.role}' may not perform this action "
            f"(requires {', '.join(allowed)})"
        )
    return caller


def may_touch_lab(caller: Caller, lab_id: Optional[str]) -> bool:
    """Laboratory isolation, matching the RLS policies.

    Director, admin and auditor see across laboratories; everyone else is
    confined to their own.
    """
    if caller.is_("director", "admin", "auditor"):
        return True
    return bool(lab_id) and caller.lab_id == lab_id
