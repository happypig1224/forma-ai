"""Unit tests for register, login, and get_me authentication routes."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from formaai.config import Settings
from formaai.modules.identity.dependencies import (
    get_db,
    get_session_manager,
    get_settings,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.passwords import hash_password
from formaai.modules.identity.router import auth_router
from formaai.modules.identity.sessions import SessionManager
from formaai.modules.identity.tokens import create_token_pair


@pytest.fixture
def auth_settings() -> Settings:
    """Test configuration settings."""
    return Settings(
        app_env="test",
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


@pytest.fixture
def mock_session_manager() -> MagicMock:
    """Mock SessionManager."""
    sm = MagicMock(spec=SessionManager)
    sm.create_session.return_value = True
    sm.verify_session.return_value = True
    return sm


@pytest.fixture
def mock_db() -> MagicMock:
    """Mock database Session."""
    session = MagicMock()
    session.scalar.return_value = None
    return session


@pytest.fixture
def test_app(
    auth_settings: Settings,
    mock_session_manager: MagicMock,
    mock_db: MagicMock,
) -> FastAPI:
    """FastAPI test app with mocked dependencies."""
    app = FastAPI()
    app.include_router(auth_router)

    app.dependency_overrides[get_settings] = lambda: auth_settings
    app.dependency_overrides[get_session_manager] = lambda: mock_session_manager
    app.dependency_overrides[get_db] = lambda: mock_db
    return app


def create_user_model(
    user_id: int = 1,
    email: str = "test@formaai.test",
    password: str = "CorrectPassword123!",
    role: str = "user",
    status_val: str = "active",
    version: int = 0,
) -> UserAccount:
    """Construct mock UserAccount instance."""
    user = UserAccount()
    user.id = user_id
    user.email = email
    user.password_hash = hash_password(password)
    user.role = role
    user.status = status_val
    user.version = version
    user.created_at = datetime.now(UTC)
    return user


# --- Register Tests ---


def test_register_success(
    test_app: FastAPI,
    mock_db: MagicMock,
    mock_session_manager: MagicMock,
) -> None:
    def fake_refresh(user: UserAccount) -> None:
        user.id = 101
        user.version = 0

    mock_db.refresh.side_effect = fake_refresh

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/register",
            json={
                "email": "newuser@formaai.test",
                "password": "StrongPassword123!",
            },
        )
        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 300
        assert "formaai_refresh" in res.cookies

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_session_manager.create_session.assert_called_once()


def test_register_duplicate_email_conflict(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    mock_db.scalar.return_value = create_user_model(email="existing@formaai.test")

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/register",
            json={
                "email": "existing@formaai.test",
                "password": "StrongPassword123!",
            },
        )
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "already registered" in res.json()["detail"]

    mock_db.add.assert_not_called()


def test_register_db_integrity_race_conflict(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    mock_db.commit.side_effect = IntegrityError(
        statement="INSERT",
        params={},
        orig=Exception("Duplicate entry"),
    )

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/register",
            json={
                "email": "race@formaai.test",
                "password": "StrongPassword123!",
            },
        )
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "already registered" in res.json()["detail"]

    mock_db.rollback.assert_called_once()


# --- Login Tests ---


def test_login_success(
    test_app: FastAPI,
    mock_db: MagicMock,
    mock_session_manager: MagicMock,
) -> None:
    user = create_user_model(
        user_id=42,
        email="login@formaai.test",
        password="ValidPassword123!",
    )
    mock_db.scalar.return_value = user

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": "login@formaai.test",
                "password": "ValidPassword123!",
            },
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "formaai_refresh" in res.cookies

    mock_session_manager.create_session.assert_called_once()


def test_login_user_not_found(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    mock_db.scalar.return_value = None

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": "nonexistent@formaai.test",
                "password": "AnyPassword123!",
            },
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid email or password" in res.json()["detail"]


def test_login_wrong_password(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    user = create_user_model(password="CorrectPassword123!")
    mock_db.scalar.return_value = user

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": user.email,
                "password": "WrongPassword456!",
            },
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid email or password" in res.json()["detail"]


def test_login_account_disabled(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    user = create_user_model(
        password="ValidPassword123!",
        status_val="disabled",
    )
    mock_db.scalar.return_value = user

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": user.email,
                "password": "ValidPassword123!",
            },
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "disabled" in res.json()["detail"]


def test_login_account_deleted(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    user = create_user_model(
        password="ValidPassword123!",
        status_val="deleted",
    )
    mock_db.scalar.return_value = user

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": user.email,
                "password": "ValidPassword123!",
            },
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "deleted" in res.json()["detail"]


def test_login_account_disabling_allowed(
    test_app: FastAPI,
    mock_db: MagicMock,
) -> None:
    user = create_user_model(
        password="ValidPassword123!",
        status_val="disabling",
    )
    mock_db.scalar.return_value = user

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/login",
            json={
                "email": user.email,
                "password": "ValidPassword123!",
            },
        )
        assert res.status_code == status.HTTP_200_OK
        assert "access_token" in res.json()


# --- Me Tests ---


def test_get_me_success(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_db: MagicMock,
) -> None:
    user = create_user_model(user_id=77, email="me@formaai.test", role="admin")
    mock_db.scalar.return_value = user

    pair = create_token_pair(user_id=77, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        res = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["id"] == 77
        assert data["email"] == "me@formaai.test"
        assert data["role"] == "admin"
        assert data["status"] == "active"
        assert "created_at" in data


def test_get_me_unauthorized_without_token(
    test_app: FastAPI,
) -> None:
    with TestClient(test_app) as client:
        res = client.get("/api/auth/me")
        assert res.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
