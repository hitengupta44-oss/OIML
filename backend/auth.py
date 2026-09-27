"""
Authentication for the reporter service.

A Hugging Face Space URL is public. Without validation on every call,
anyone can burn the CPU quota and render reports for laboratories they
have nothing to do with. So every endpoint validates the Supabase JWT
before doing any work at all.

Two signing schemes, because Supabase supports both
---------------------------------------------------
Older projects sign access tokens with HS256 and a shared secret, the
one on Settings -> API -> JWT Secret.

Newer projects sign with ES256 (or RS256) using an asymmetric key pair
and publish the public half as a JWKS document. There is no shared
secret to configure, and a token signed that way fails HS256 validation
with "The specified alg value is not allowed".

This module reads the token's own header to decide which path to take,
so the same code works on either kind of project and keeps working when
a project is migrated from one to the other.

Roles are read from app_metadata, never user_metadata: a signed-in user
can edit user_metadata through the normal client API, so an
authorisation claim living there would be self-issued.

This mirrors app.role() and app.lab_id() in
supabase/migrations/0002_functions.sql, so the service and the database
agree on who someone is.
"""

import os
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient

JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
AUDIENCE = "authenticated"

# Asymmetric algorithms Supabase may use. The public keys come from the
# project's JWKS endpoint, which is cached by PyJWKClient.
ASYMMETRIC = ("ES256", "RS256", "EdDSA")

_jwks_client: Optional[PyJWKClient] = None


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


def _jwks() -> PyJWKClient:
    """Lazily built, and cached: fetching the key set on every request
    would add a network round trip to every call."""
    global _jwks_client
    if _jwks_client is None:
        if not SUPABASE_URL:
            raise AuthError(
                "This token is signed with an asymmetric key, so the public "
                "key must be fetched from the project's JWKS endpoint — but "
                "SUPABASE_URL is not set on this service."
            )
        _jwks_client = PyJWKClient(
            f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            cache_keys=True,
            lifespan=3600,
        )
    return _jwks_client


def _decode(token: str) -> dict:
    """Validate the signature and standard claims, whichever scheme is used."""
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"malformed access token: {exc}")

    if alg in ASYMMETRIC:
        try:
            key = _jwks().get_signing_key_from_jwt(token).key
        except AuthError:
            raise
        except Exception as exc:                              # noqa: BLE001
            raise AuthError(f"could not fetch the project's signing key: {exc}")
        return jwt.decode(token, key, algorithms=list(ASYMMETRIC),
                          audience=AUDIENCE)

    if alg == "HS256":
        if not JWT_SECRET:
            raise AuthError(
                "This token is signed with HS256, which needs the shared "
                "secret. Set SUPABASE_JWT_SECRET (Settings → API → JWT "
                "Secret) as a secret on this service."
            )
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                          audience=AUDIENCE)

    raise AuthError(f"unsupported token algorithm '{alg}'")


def verify(token: str) -> Caller:
    """Decode and validate a Supabase access token."""
    if not token or not isinstance(token, str):
        raise AuthError("no access token supplied")

    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise AuthError("access token has expired; sign in again")
    except jwt.InvalidAudienceError:
        raise AuthError("access token is for a different audience")
    except AuthError:
        raise
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


def configured() -> str:
    """What /health reports: which schemes this service can actually verify."""
    modes = []
    if JWT_SECRET:
        modes.append("HS256")
    if SUPABASE_URL:
        modes.append("ES256/RS256 via JWKS")
    return " + ".join(modes) if modes else "none"
