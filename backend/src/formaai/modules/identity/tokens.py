"""JWT Token issuance, verification and security contracts for HS256."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Literal

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from formaai.config import Settings


class TokenError(Exception):
    """Base exception for all token operations."""


class TokenSignatureError(TokenError):
    """Token signature is invalid, tampered or uses an unauthorized algorithm."""


class TokenExpiredError(TokenError):
    """Token has expired."""


class TokenTypeMismatchError(TokenError):
    """Token type claim (typ) does not match expected usage (access vs refresh)."""


class TokenClaimsInvalidError(TokenError):
    """Token claims are missing, malformed or fail audience/issuer verification."""


@dataclass(frozen=True)
class TokenClaims:
    """Strongly-typed claims extracted from a verified JWT."""

    sub: str
    ver: int
    sid: str
    jti: str
    typ: Literal["access", "refresh"]
    iss: str
    aud: str
    iat: int
    exp: int


@dataclass(frozen=True)
class TokenPair:
    """Result of issuing an access and refresh token pair."""

    access_token: str
    refresh_token: str
    session_id: str
    refresh_digest: str
    access_expires_in: int
    refresh_expires_in: int


def compute_refresh_digest(refresh_token: str) -> str:
    """Compute standard SHA-256 hex digest of refresh token for Redis lookup."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def create_token_pair(
    user_id: int,
    version: int,
    settings: Settings,
    *,
    session_id: str | None = None,
) -> TokenPair:
    """Issue a paired Access Token and Refresh Token with distinct keys and HS256."""
    now = int(time.time())
    sid = session_id or secrets.token_hex(16)

    access_expires_in = settings.jwt_access_ttl_seconds
    refresh_expires_in = settings.jwt_refresh_ttl_seconds

    access_payload = {
        "sub": str(user_id),
        "ver": version,
        "sid": sid,
        "jti": secrets.token_hex(16),
        "typ": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + access_expires_in,
    }
    access_token = jwt.encode(
        access_payload,
        settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )

    refresh_payload = {
        "sub": str(user_id),
        "ver": version,
        "sid": sid,
        "jti": secrets.token_hex(16),
        "typ": "refresh",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + refresh_expires_in,
    }
    refresh_token = jwt.encode(
        refresh_payload,
        settings.jwt_refresh_secret.get_secret_value(),
        algorithm="HS256",
    )

    digest = compute_refresh_digest(refresh_token)

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        session_id=sid,
        refresh_digest=digest,
        access_expires_in=access_expires_in,
        refresh_expires_in=refresh_expires_in,
    )


def _decode_and_verify(
    token: str,
    secret: str,
    expected_typ: Literal["access", "refresh"],
    settings: Settings,
) -> TokenClaims:
    """Decode token with HS256 and verify claims, issuer and audience."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": [
                    "sub",
                    "ver",
                    "sid",
                    "jti",
                    "typ",
                    "iss",
                    "aud",
                    "iat",
                    "exp",
                ],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except InvalidTokenError as exc:
        msg = str(exc).lower()
        if "missing" in msg or "audience" in msg or "issuer" in msg or "claim" in msg:
            raise TokenClaimsInvalidError(str(exc)) from exc
        raise TokenSignatureError(str(exc)) from exc

    typ = payload.get("typ")
    if typ != expected_typ:
        raise TokenTypeMismatchError(
            f"Expected token type '{expected_typ}', got '{typ}'"
        )

    ver = payload.get("ver")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise TokenClaimsInvalidError("Claim 'ver' must be an integer")

    sub = str(payload.get("sub", ""))
    if not sub:
        raise TokenClaimsInvalidError("Claim 'sub' must be non-empty")

    sid = str(payload.get("sid", ""))
    if not sid:
        raise TokenClaimsInvalidError("Claim 'sid' must be non-empty")

    jti = str(payload.get("jti", ""))
    if not jti:
        raise TokenClaimsInvalidError("Claim 'jti' must be non-empty")

    return TokenClaims(
        sub=sub,
        ver=ver,
        sid=sid,
        jti=jti,
        typ=expected_typ,
        iss=str(payload["iss"]),
        aud=str(payload["aud"]),
        iat=int(payload["iat"]),
        exp=int(payload["exp"]),
    )


def verify_access_token(token: str, settings: Settings) -> TokenClaims:
    """Verify an access token using jwt_access_secret and typ='access'."""
    return _decode_and_verify(
        token,
        settings.jwt_access_secret.get_secret_value(),
        "access",
        settings,
    )


def verify_refresh_token(token: str, settings: Settings) -> TokenClaims:
    """Verify a refresh token using jwt_refresh_secret and typ='refresh'."""
    return _decode_and_verify(
        token,
        settings.jwt_refresh_secret.get_secret_value(),
        "refresh",
        settings,
    )
