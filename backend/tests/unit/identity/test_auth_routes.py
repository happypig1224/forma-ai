"""Unit tests for Phase 3 FastAPI authentication dependencies and auth routes."""

from typing import Annotated
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from formaai.config import Settings
from formaai.modules.identity.dependencies import (
    get_current_user,
    get_db,
    get_session_manager,
    get_settings,
    require_admin,
    require_can_create_tasks,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.router import auth_router
from formaai.modules.identity.sessions import SessionManager
from formaai.modules.identity.tokens import (
    create_token_pair,
)


@pytest.fixture
def auth_settings() -> Settings:
    """Test settings with CORS origin and distinct JWT secrets."""
    return Settings(
        app_env="test",
        cors_origins=["https://app.formaai.test"],
        cors_allow_credentials=True,
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
    """Mock SessionManager for isolated routing and dependency tests."""
    sm = MagicMock(spec=SessionManager)
    sm.verify_session.return_value = True
    sm.rotate_session.return_value = 1  # RotationResult.SUCCESS
    sm.revoke_session.return_value = True
    return sm


@pytest.fixture
def test_app(auth_settings: Settings, mock_session_manager: MagicMock) -> FastAPI:
    """FastAPI test app mounting auth_router and dummy protected routes."""
    app = FastAPI()
    app.include_router(auth_router)

    # Attach dummy protected endpoints using modern Annotated syntax
    @app.get("/test/protected")
    def protected_route(
        user: Annotated[UserAccount, Depends(get_current_user)],
    ):
        return {"user_id": user.id, "email": user.email, "role": user.role}

    @app.get("/test/admin")
    def admin_route(
        user: Annotated[UserAccount, Depends(require_admin)],
    ):
        return {"admin": True, "user_id": user.id}

    @app.post("/test/tasks")
    def create_task_route(
        user: Annotated[UserAccount, Depends(require_can_create_tasks)],
    ):
        return {"task_created": True}

    # Dependency overrides
    app.dependency_overrides[get_settings] = lambda: auth_settings
    app.dependency_overrides[get_session_manager] = lambda: mock_session_manager
    app.dependency_overrides[get_db] = lambda: MagicMock()

    return app


def create_mock_user(
    user_id: int = 1,
    role: str = "user",
    status_val: str = "active",
    version: int = 0,
) -> UserAccount:
    """Construct mock UserAccount with required identity properties."""
    user = UserAccount()
    user.id = user_id
    user.email = f"user{user_id}@formaai.test"
    user.role = role
    user.status = status_val
    user.version = version
    return user


def test_get_current_user_success(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_session_manager: MagicMock,
):
    """AC-P3-1: Valid access token with active session returns current user."""
    mock_user = create_mock_user(user_id=10, role="user")
    mock_db = MagicMock()
    mock_db.scalar.return_value = mock_user
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair = create_token_pair(user_id=10, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        res = client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["user_id"] == 10
        assert data["role"] == "user"

    mock_session_manager.verify_session.assert_called_once_with(
        session_id=pair.session_id, user_id=10, version=0
    )


def test_get_current_user_redis_session_revoked(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_session_manager: MagicMock,
):
    """AC-P3-2: If Redis session was deleted, access token is rejected immediately."""
    mock_session_manager.verify_session.return_value = False
    mock_db = MagicMock()
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair = create_token_pair(user_id=11, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        res = client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Session has been revoked" in res.json()["detail"]


def test_get_current_user_account_status_and_version(
    test_app: FastAPI,
    auth_settings: Settings,
):
    """AC-P3-3: Disabled/deleted accounts and bumped version tokens are rejected."""
    # 1. Version mismatch (password changed / logout all)
    mock_user_bumped = create_mock_user(user_id=12, version=1)
    mock_db = MagicMock()
    mock_db.scalar.return_value = mock_user_bumped
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair_v0 = create_token_pair(user_id=12, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        res = client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {pair_v0.access_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Credentials have been revoked" in res.json()["detail"]

    # 2. Disabled account
    mock_user_disabled = create_mock_user(user_id=12, status_val="disabled", version=0)
    mock_db.scalar.return_value = mock_user_disabled
    with TestClient(test_app) as client:
        res = client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {pair_v0.access_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "disabled" in res.json()["detail"]


def test_disabling_user_can_access_but_cannot_create_tasks(
    test_app: FastAPI,
    auth_settings: Settings,
):
    """AC-P3-3: disabling user can access work but cannot create new tasks."""
    mock_user_disabling = create_mock_user(
        user_id=13, status_val="disabling", version=0
    )
    mock_db = MagicMock()
    mock_db.scalar.return_value = mock_user_disabling
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair = create_token_pair(user_id=13, version=0, settings=auth_settings)
    auth_header = {"Authorization": f"Bearer {pair.access_token}"}

    with TestClient(test_app) as client:
        # Permitted: reading existing work
        res_read = client.get("/test/protected", headers=auth_header)
        assert res_read.status_code == status.HTTP_200_OK

        # Prohibited: creating new tasks
        res_task = client.post("/test/tasks", headers=auth_header)
        assert res_task.status_code == status.HTTP_403_FORBIDDEN
        assert "disabling" in res_task.json()["detail"]


def test_require_admin_guard(
    test_app: FastAPI,
    auth_settings: Settings,
):
    """AC-P3-4: Administrator guard blocks regular user and allows admin."""
    mock_user = create_mock_user(user_id=14, role="user")
    mock_admin = create_mock_user(user_id=15, role="admin")

    mock_db = MagicMock()
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair_user = create_token_pair(user_id=14, version=0, settings=auth_settings)
    pair_admin = create_token_pair(user_id=15, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        # Regular user denied
        mock_db.scalar.return_value = mock_user
        res_user = client.get(
            "/test/admin",
            headers={"Authorization": f"Bearer {pair_user.access_token}"},
        )
        assert res_user.status_code == status.HTTP_403_FORBIDDEN

        # Admin allowed
        mock_db.scalar.return_value = mock_admin
        res_admin = client.get(
            "/test/admin",
            headers={"Authorization": f"Bearer {pair_admin.access_token}"},
        )
        assert res_admin.status_code == status.HTTP_200_OK
        assert res_admin.json()["admin"] is True


def test_refresh_token_endpoint_success(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_session_manager: MagicMock,
):
    """AC-P3-5: POST /api/auth/refresh returns new access token and updates cookie."""
    mock_user = create_mock_user(user_id=20, version=0)
    mock_db = MagicMock()
    mock_db.scalar.return_value = mock_user
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair = create_token_pair(user_id=20, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        # Set refresh token cookie and required CSRF headers
        client.cookies.set("formaai_refresh", pair.refresh_token)
        res = client.post(
            "/api/auth/refresh",
            headers={
                "Origin": "https://app.formaai.test",
                "X-CSRF-Protection": "1",
            },
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 300

        # Assert new refresh cookie is set
        assert "formaai_refresh" in res.cookies

    mock_session_manager.rotate_session.assert_called_once()


def test_refresh_token_csrf_and_origin_enforcement(
    test_app: FastAPI,
    auth_settings: Settings,
):
    """AC-P3-7: Missing X-CSRF-Protection or invalid Origin is rejected."""
    pair = create_token_pair(user_id=21, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        client.cookies.set("formaai_refresh", pair.refresh_token)

        # 1. Missing X-CSRF-Protection
        res1 = client.post(
            "/api/auth/refresh",
            headers={"Origin": "https://app.formaai.test"},
        )
        assert res1.status_code == status.HTTP_403_FORBIDDEN

        # 2. Untrusted Origin
        res2 = client.post(
            "/api/auth/refresh",
            headers={
                "Origin": "https://evil-attacker.test",
                "X-CSRF-Protection": "1",
            },
        )
        assert res2.status_code == status.HTTP_403_FORBIDDEN


def test_logout_endpoint_revokes_session_and_clears_cookie(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_session_manager: MagicMock,
):
    """AC-P3-7: POST /api/auth/logout revokes Redis session and deletes cookie."""
    pair = create_token_pair(user_id=30, version=0, settings=auth_settings)

    with TestClient(test_app) as client:
        client.cookies.set("formaai_refresh", pair.refresh_token)
        res = client.post(
            "/api/auth/logout",
            headers={
                "Origin": "https://app.formaai.test",
                "X-CSRF-Protection": "1",
            },
        )
        assert res.status_code == status.HTTP_204_NO_CONTENT
        # Cookie is cleared
        cookie_val = res.cookies.get("formaai_refresh")
        assert cookie_val is None or cookie_val == ""

    mock_session_manager.revoke_session.assert_called_once_with(
        pair.session_id, user_id=30
    )


def test_logout_all_endpoint_bumps_version(
    test_app: FastAPI,
    auth_settings: Settings,
    mock_session_manager: MagicMock,
):
    """AC-P3-7: POST /api/auth/logout-all increments DB version and revokes session."""
    mock_user = create_mock_user(user_id=40, version=2)
    mock_db = MagicMock()
    mock_db.scalar.return_value = mock_user
    test_app.dependency_overrides[get_db] = lambda: mock_db

    pair = create_token_pair(user_id=40, version=2, settings=auth_settings)

    with TestClient(test_app) as client:
        res = client.post(
            "/api/auth/logout-all",
            headers={
                "Authorization": f"Bearer {pair.access_token}",
                "Origin": "https://app.formaai.test",
                "X-CSRF-Protection": "1",
            },
        )
        assert res.status_code == status.HTTP_204_NO_CONTENT
        # DB version update was executed and committed
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()
