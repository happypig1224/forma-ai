"""Unit tests for identity module Pydantic schemas contract validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.identity.schemas import (
    LoginRequest,
    RegisterRequest,
    UserResponse,
)


class DummyUserAccount:
    """Simple class to simulate ORM model attributes."""

    def __init__(
        self,
        id: int,
        email: str,
        role: str,
        status: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.email = email
        self.role = role
        self.status = status
        self.created_at = created_at


def test_register_request_valid() -> None:
    req = RegisterRequest(
        email="developer@formaai.test",
        password="ValidPassword123",
    )
    assert req.email == "developer@formaai.test"
    assert req.password == "ValidPassword123"


@pytest.mark.parametrize(
    "invalid_password",
    [
        "short",  # < 8 chars
        "1234567",  # 7 chars
        "a" * 129,  # > 128 chars
    ],
)
def test_register_request_invalid_password(invalid_password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="developer@formaai.test",
            password=invalid_password,
        )


def test_register_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="developer@formaai.test",
            password="ValidPassword123",
            extra_field="malicious",  # type: ignore[call-arg]
        )


def test_login_request_valid() -> None:
    req = LoginRequest(
        email="developer@formaai.test",
        password="ValidPassword123",
    )
    assert req.email == "developer@formaai.test"
    assert req.password == "ValidPassword123"


def test_login_request_empty_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(
            email="developer@formaai.test",
            password="",
        )


def test_login_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(
            email="developer@formaai.test",
            password="ValidPassword123",
            role="admin",  # type: ignore[call-arg]
        )


def test_user_response_from_attributes() -> None:
    now = datetime.now(UTC)
    user_orm = DummyUserAccount(
        id=42,
        email="alice@formaai.test",
        role="user",
        status="active",
        created_at=now,
    )
    res = UserResponse.model_validate(user_orm)
    assert res.id == 42
    assert res.email == "alice@formaai.test"
    assert res.role == "user"
    assert res.status == "active"
    assert res.created_at == now


def test_user_response_dict_export() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    res = UserResponse(
        id=10,
        email="bob@formaai.test",
        role="admin",
        status="active",
        created_at=now,
    )
    data = res.model_dump()
    assert data == {
        "id": 10,
        "email": "bob@formaai.test",
        "role": "admin",
        "status": "active",
        "created_at": now,
    }
