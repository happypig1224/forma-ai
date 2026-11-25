"""Unit tests for Argon2id password hashing and verification."""

from formaai.modules.identity.passwords import hash_password, verify_password


def test_hash_password_produces_argon2id_format() -> None:
    hashed = hash_password("SecureP@ssw0rd123!")
    assert hashed.startswith("$argon2id$")


def test_hash_password_salts_uniquely() -> None:
    hash1 = hash_password("same_password")
    hash2 = hash_password("same_password")
    assert hash1 != hash2


def test_verify_password_success() -> None:
    password = "CorrectHorseBatteryStaple!"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_verify_password_mismatch() -> None:
    password = "CorrectHorseBatteryStaple!"
    hashed = hash_password(password)
    assert verify_password("WrongPassword!", hashed) is False


def test_verify_password_invalid_hash_format() -> None:
    assert verify_password("secret", "not_a_valid_hash") is False
    assert verify_password("secret", "") is False
    assert verify_password("secret", "$argon2id$invalid$hash$structure") is False
