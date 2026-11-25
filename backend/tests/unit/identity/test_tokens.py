"""Unit tests for Phase 1 JWT Token issuance and security contracts."""

import hashlib
import time

import jwt
import pytest
from pydantic import SecretStr

from formaai.config import Settings
from formaai.modules.identity.tokens import (
    TokenClaims,
    TokenClaimsInvalidError,
    TokenExpiredError,
    TokenPair,
    TokenSignatureError,
    TokenTypeMismatchError,
    compute_refresh_digest,
    create_token_pair,
    verify_access_token,
    verify_refresh_token,
)


@pytest.fixture
def jwt_settings() -> Settings:
    """Provide realistic cryptographic settings with distinct 256-bit keys."""
    return Settings(
        mysql_database="test_db",
        mysql_user="test_user",
        mysql_password=SecretStr("test_pass"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_access_secret=SecretStr("access-secret-strictly-32-bytes-long!"),
        jwt_refresh_secret=SecretStr("refresh-secret-strictly-32-bytes-long!"),
        jwt_issuer="formaai",
        jwt_audience="formaai-client",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=604800,
    )


def test_create_token_pair_structure(jwt_settings: Settings):
    """AC-2: create_token_pair returns strongly-typed TokenPair."""
    pair = create_token_pair(user_id=101, version=3, settings=jwt_settings)

    assert isinstance(pair, TokenPair)
    assert isinstance(pair.access_token, str) and len(pair.access_token) > 20
    assert isinstance(pair.refresh_token, str) and len(pair.refresh_token) > 20
    assert isinstance(pair.session_id, str) and len(pair.session_id) >= 16
    assert isinstance(pair.refresh_digest, str) and len(pair.refresh_digest) == 64
    assert pair.access_expires_in == 300
    assert pair.refresh_expires_in == 604800

    # Digest must match SHA-256 of the issued refresh token
    expected_digest = hashlib.sha256(pair.refresh_token.encode("utf-8")).hexdigest()
    assert pair.refresh_digest == expected_digest
    assert compute_refresh_digest(pair.refresh_token) == expected_digest


def test_verify_access_token_success(jwt_settings: Settings):
    """AC-2 & AC-3: Valid access token decodes to strongly-typed TokenClaims."""
    pair = create_token_pair(user_id=102, version=5, settings=jwt_settings)
    claims = verify_access_token(pair.access_token, settings=jwt_settings)

    assert isinstance(claims, TokenClaims)
    assert claims.sub == "102"
    assert claims.ver == 5
    assert claims.sid == pair.session_id
    assert claims.typ == "access"
    assert claims.iss == "formaai"
    assert claims.aud == "formaai-client"
    assert claims.exp > claims.iat


def test_verify_refresh_token_success(jwt_settings: Settings):
    """AC-2 & AC-3: Valid refresh token decodes to strongly-typed TokenClaims."""
    pair = create_token_pair(user_id=103, version=1, settings=jwt_settings)
    claims = verify_refresh_token(pair.refresh_token, settings=jwt_settings)

    assert isinstance(claims, TokenClaims)
    assert claims.sub == "103"
    assert claims.ver == 1
    assert claims.sid == pair.session_id
    assert claims.typ == "refresh"
    assert claims.iss == "formaai"
    assert claims.aud == "formaai-client"
    assert claims.exp > claims.iat


def test_custom_session_id_preservation(jwt_settings: Settings):
    """AC-2: Explicit session_id is preserved across both tokens."""
    custom_sid = "custom-session-uuid-12345"
    pair = create_token_pair(
        user_id=200, version=0, settings=jwt_settings, session_id=custom_sid
    )

    assert pair.session_id == custom_sid
    access_claims = verify_access_token(pair.access_token, settings=jwt_settings)
    refresh_claims = verify_refresh_token(pair.refresh_token, settings=jwt_settings)
    assert access_claims.sid == custom_sid
    assert refresh_claims.sid == custom_sid


def test_type_confusion_attack_rejected(jwt_settings: Settings):
    """AC-3: Cross-using tokens (e.g. Refresh at Access endpoint) is rejected."""
    # 1. Refresh Token presented to verify_access_token
    with pytest.raises(TokenTypeMismatchError):
        now = int(time.time())
        fake_payload = {
            "sub": "300",
            "ver": 0,
            "sid": "sid",
            "jti": "jti-confusion-1",
            "typ": "refresh",
            "iss": "formaai",
            "aud": "formaai-client",
            "iat": now,
            "exp": now + 300,
        }
        fake_token = jwt.encode(
            fake_payload,
            jwt_settings.jwt_access_secret.get_secret_value(),
            algorithm="HS256",
        )
        verify_access_token(fake_token, settings=jwt_settings)

    # 2. Access Token presented to verify_refresh_token
    with pytest.raises(TokenTypeMismatchError):
        fake_payload["typ"] = "access"
        fake_token = jwt.encode(
            fake_payload,
            jwt_settings.jwt_refresh_secret.get_secret_value(),
            algorithm="HS256",
        )
        verify_refresh_token(fake_token, settings=jwt_settings)


def test_key_isolation_rejected(jwt_settings: Settings):
    """AC-3: Access token cannot be verified with refresh key and vice-versa."""
    pair = create_token_pair(user_id=400, version=1, settings=jwt_settings)

    # The actual refresh token signed with refresh key, tested against access secret
    with pytest.raises(TokenSignatureError):
        verify_access_token(pair.refresh_token, settings=jwt_settings)

    # The actual access token signed with access key, tested against refresh secret
    with pytest.raises(TokenSignatureError):
        verify_refresh_token(pair.access_token, settings=jwt_settings)


def test_algorithm_tampering_rejected(jwt_settings: Settings):
    """AC-3: Algorithm substitution attacks (none, asymmetric) must be rejected."""
    now = int(time.time())
    payload = {
        "sub": "500",
        "ver": 0,
        "sid": "some-sid",
        "jti": "jti-alg-1",
        "typ": "access",
        "iss": "formaai",
        "aud": "formaai-client",
        "iat": now,
        "exp": now + 300,
    }

    # Attack: alg="none"
    none_token = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(TokenSignatureError):
        verify_access_token(none_token, settings=jwt_settings)


def test_expired_token_rejected(jwt_settings: Settings):
    """AC-3: Expired tokens raise TokenExpiredError."""
    past = int(time.time()) - 100
    expired_payload = {
        "sub": "600",
        "ver": 0,
        "sid": "some-sid",
        "jti": "jti-exp-1",
        "typ": "access",
        "iss": "formaai",
        "aud": "formaai-client",
        "iat": past - 300,
        "exp": past,
    }
    expired_token = jwt.encode(
        expired_payload,
        jwt_settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(TokenExpiredError):
        verify_access_token(expired_token, settings=jwt_settings)


def test_tampered_signature_or_payload_rejected(jwt_settings: Settings):
    """AC-3: Tampered signature or payload raises TokenSignatureError."""
    pair = create_token_pair(user_id=700, version=0, settings=jwt_settings)
    parts = pair.access_token.split(".")
    # Tamper the signature part
    tampered_sig = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

    with pytest.raises(TokenSignatureError):
        verify_access_token(tampered_token, settings=jwt_settings)


def test_missing_claims_rejected(jwt_settings: Settings):
    """AC-3: Token missing mandatory claims raises TokenClaimsInvalidError."""
    now = int(time.time())
    # Missing 'ver'
    bad_payload = {
        "sub": "800",
        "sid": "sid-123",
        "jti": "jti-missing-ver",
        "typ": "access",
        "iss": "formaai",
        "aud": "formaai-client",
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode(
        bad_payload,
        jwt_settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(TokenClaimsInvalidError):
        verify_access_token(token, settings=jwt_settings)


def test_audience_and_issuer_mismatch_rejected(jwt_settings: Settings):
    """AC-3: Mismatched aud or iss raises TokenClaimsInvalidError."""
    now = int(time.time())
    payload = {
        "sub": "900",
        "ver": 0,
        "sid": "sid-123",
        "jti": "jti-aud-iss",
        "typ": "access",
        "iss": "wrong-issuer",
        "aud": "formaai-client",
        "iat": now,
        "exp": now + 300,
    }
    wrong_iss_token = jwt.encode(
        payload,
        jwt_settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(TokenClaimsInvalidError):
        verify_access_token(wrong_iss_token, settings=jwt_settings)

    payload["iss"] = "formaai"
    payload["aud"] = "wrong-audience"
    wrong_aud_token = jwt.encode(
        payload,
        jwt_settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(TokenClaimsInvalidError):
        verify_access_token(wrong_aud_token, settings=jwt_settings)
