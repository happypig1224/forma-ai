"""End-to-End integration test covering the complete registration and login lifecycle.

Validates the full business lifecycle on real Redis 7.4.0:
1. Register a new user account with unique email and Argon2id password (201 Created).
2. Access protected profile GET /api/auth/me with initial Bearer Access Token.
3. Verify duplicate registration prevention: same email is rejected with 409 Conflict.
4. Refresh tokens atomically: POST /api/auth/refresh updates cookie and token.
5. Verify session logout: POST /api/auth/logout invalidates session.
6. Authenticate with password: POST /api/auth/login (wrong password 401, correct 200).
7. Global session revocation: POST /api/auth/logout-all increments version in DB.
8. Account state restriction: disabled account rejected from auth and login.
"""

import shutil
import subprocess
import time
from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis
from sqlalchemy.exc import IntegrityError

from formaai.config import Settings
from formaai.modules.identity.dependencies import (
    get_db,
    get_session_manager,
    get_settings,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.router import auth_router
from formaai.modules.identity.sessions import SessionManager

E2E_AUTH_REDIS_PORT = 6391


class InMemoryDbSession:
    """Mock thread-safe database session mimicking SQLAlchemy Session."""

    def __init__(self) -> None:
        self.users: dict[int, UserAccount] = {}
        self.next_id = 1
        self._pending_user: UserAccount | None = None

    def scalar(self, statement: Any) -> Any:
        whereclause = getattr(statement, "whereclause", None)
        if whereclause is not None:
            clauses = (
                whereclause.clauses
                if hasattr(whereclause, "clauses")
                else [whereclause]
            )
            for expr in clauses:
                if hasattr(expr, "left") and hasattr(expr, "right"):
                    col = getattr(expr.left, "name", None)
                    val = getattr(expr.right, "value", None)
                    if col == "id":
                        return self.users.get(val)
                    if col == "email":
                        for u in self.users.values():
                            if u.email.lower() == str(val).lower():
                                return u
                        return None
        return None

    def add(self, instance: UserAccount) -> None:
        self._pending_user = instance

    def commit(self) -> None:
        if self._pending_user is not None:
            user = self._pending_user
            for existing in self.users.values():
                if existing.email.lower() == user.email.lower():
                    self._pending_user = None
                    raise IntegrityError(
                        statement="INSERT INTO user_account",
                        params={"email": user.email},
                        orig=Exception("Duplicate email violation"),
                    )
            if user.id is None:
                user.id = self.next_id
                self.next_id += 1
            self.users[user.id] = user
            self._pending_user = None

    def rollback(self) -> None:
        self._pending_user = None

    def refresh(self, instance: UserAccount) -> None:
        pass

    def execute(self, statement: Any) -> Any:
        table = getattr(statement, "table", None)
        values = getattr(statement, "_values", {})
        whereclause = getattr(statement, "whereclause", None)

        if table is not None and getattr(table, "name", "") == "user_account":
            target_id = None
            if whereclause is not None:
                clauses = (
                    whereclause.clauses
                    if hasattr(whereclause, "clauses")
                    else [whereclause]
                )
                for expr in clauses:
                    if hasattr(expr, "left") and hasattr(expr, "right"):
                        col = getattr(expr.left, "name", None)
                        val = getattr(expr.right, "value", None)
                        if col == "id":
                            target_id = val

            if target_id and target_id in self.users:
                user = self.users[target_id]
                for k, v in values.items():
                    col_name = getattr(k, "name", str(k))
                    if col_name == "version":
                        user.version += 1
                    elif hasattr(user, col_name):
                        setattr(user, col_name, v)
        return None


@pytest.fixture(scope="module")
def real_redis_server() -> Generator[Redis]:
    """Start an isolated background Redis server process on dedicated port."""
    redis_bin = shutil.which("redis-server")
    if not redis_bin:
        redis_bin = r"D:\Redis-7.4.0-Windows\redis-server.exe"

    proc = subprocess.Popen(
        [
            redis_bin,
            "--port",
            str(E2E_AUTH_REDIS_PORT),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    client = Redis(host="127.0.0.1", port=E2E_AUTH_REDIS_PORT, db=0)
    connected = False
    for _ in range(30):
        try:
            if client.ping():
                connected = True
                break
        except Exception:
            time.sleep(0.1)

    if not connected:
        proc.terminate()
        pytest.fail(f"Could not connect to Redis on port {E2E_AUTH_REDIS_PORT}")

    yield client

    client.close()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def e2e_auth_env(real_redis_server: Redis) -> dict[str, Any]:
    """Setup FastAPI test app with real Redis and in-memory MySQL session."""
    real_redis_server.flushdb()

    settings = Settings(
        app_env="test",
        cors_origins=["https://dashboard.formaai.test"],
        cors_allow_credentials=True,
        mysql_database="formaai_e2e_auth",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr(f"redis://127.0.0.1:{E2E_AUTH_REDIS_PORT}/0"),
        jwt_access_secret=SecretStr("access-secret-strictly-32-bytes-long!"),
        jwt_refresh_secret=SecretStr("refresh-secret-strictly-32-bytes-long!"),
        jwt_issuer="formaai",
        jwt_audience="formaai-client",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=604800,
    )

    session_manager = SessionManager(
        redis_client=real_redis_server,
        app_env=settings.app_env,
        idle_ttl_seconds=settings.jwt_refresh_ttl_seconds,
    )

    db_session = InMemoryDbSession()

    app = FastAPI()
    app.include_router(auth_router)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.dependency_overrides[get_db] = lambda: db_session

    return {
        "app": app,
        "settings": settings,
        "session_manager": session_manager,
        "db": db_session,
        "redis": real_redis_server,
    }


def test_full_auth_lifecycle_end_to_end(e2e_auth_env: dict[str, Any]) -> None:
    """Execute end-to-end integration flow across register, login, me, and logout."""
    app = e2e_auth_env["app"]
    db: InMemoryDbSession = e2e_auth_env["db"]

    user_email = "alice@formaai.test"
    user_password = "SuperSecretPassword123!"
    csrf_headers = {
        "Origin": "https://dashboard.formaai.test",
        "X-CSRF-Protection": "1",
    }

    with TestClient(app) as client:
        # ======================================================================
        # 1. Register user account directly with email and password
        # ======================================================================
        res_reg = client.post(
            "/api/auth/register",
            json={
                "email": user_email,
                "password": user_password,
            },
        )
        assert res_reg.status_code == status.HTTP_201_CREATED
        reg_data = res_reg.json()
        assert "access_token" in reg_data
        assert reg_data["token_type"] == "bearer"
        assert reg_data["expires_in"] == 300
        initial_access_token = reg_data["access_token"]

        # Ensure refresh cookie was set
        assert "formaai_refresh" in res_reg.cookies
        initial_refresh_cookie = res_reg.cookies["formaai_refresh"]

        # Check DB state
        assert len(db.users) == 1
        created_user = next(iter(db.users.values()))
        assert created_user.email == user_email
        assert created_user.role == "user"
        assert created_user.status == "active"
        assert created_user.version == 0

        # ======================================================================
        # 2. Access GET /api/auth/me with initial access token
        # ======================================================================
        res_me = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {initial_access_token}"},
        )
        assert res_me.status_code == status.HTTP_200_OK
        me_data = res_me.json()
        assert me_data["id"] == created_user.id
        assert me_data["email"] == user_email
        assert me_data["role"] == "user"
        assert me_data["status"] == "active"
        assert "created_at" in me_data

        # ======================================================================
        # 3. Duplicate registration prevention (409 Conflict)
        # ======================================================================
        res_dup_reg = client.post(
            "/api/auth/register",
            json={
                "email": user_email,
                "password": "AnotherPassword456!",
            },
        )
        assert res_dup_reg.status_code == status.HTTP_409_CONFLICT
        assert "already registered" in res_dup_reg.json()["detail"]

        # ======================================================================
        # 4. Refresh tokens: rotate session and acquire new access token
        # ======================================================================
        client.cookies.set("formaai_refresh", initial_refresh_cookie)
        res_refresh = client.post(
            "/api/auth/refresh",
            headers=csrf_headers,
        )
        assert res_refresh.status_code == status.HTTP_200_OK
        refreshed_data = res_refresh.json()
        new_access_token = refreshed_data["access_token"]
        assert new_access_token != initial_access_token
        new_refresh_cookie = res_refresh.cookies.get("formaai_refresh")
        assert new_refresh_cookie is not None
        assert new_refresh_cookie != initial_refresh_cookie

        # Access /me with new token
        res_me_new = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_access_token}"},
        )
        assert res_me_new.status_code == status.HTTP_200_OK
        assert res_me_new.json()["email"] == user_email

        # ======================================================================
        # 5. Single-device logout: POST /api/auth/logout
        # ======================================================================
        client.cookies.set("formaai_refresh", new_refresh_cookie)
        res_logout = client.post(
            "/api/auth/logout",
            headers=csrf_headers,
        )
        assert res_logout.status_code == status.HTTP_204_NO_CONTENT

        # Former access token should now be rejected (Redis session destroyed)
        res_me_after_logout = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_access_token}"},
        )
        assert res_me_after_logout.status_code == status.HTTP_401_UNAUTHORIZED
        assert (
            "Session has been revoked or expired"
            in res_me_after_logout.json()["detail"]
        )

        # ======================================================================
        # 6. Password authentication: POST /api/auth/login
        # ======================================================================
        # 6a. Wrong password fails
        res_bad_login = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": "WrongPassword999!"},
        )
        assert res_bad_login.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid email or password" in res_bad_login.json()["detail"]

        # 6b. Correct password succeeds
        res_good_login = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": user_password},
        )
        assert res_good_login.status_code == status.HTTP_200_OK
        login_data = res_good_login.json()
        login_access_token = login_data["access_token"]
        login_refresh_cookie = res_good_login.cookies.get("formaai_refresh")
        assert login_refresh_cookie is not None

        # Profile accessible with logged-in token
        res_me_login = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {login_access_token}"},
        )
        assert res_me_login.status_code == status.HTTP_200_OK
        assert res_me_login.json()["id"] == created_user.id

        # ======================================================================
        # 7. Global session revocation: POST /api/auth/logout-all
        # ======================================================================
        client.cookies.set("formaai_refresh", login_refresh_cookie)
        res_logout_all = client.post(
            "/api/auth/logout-all",
            headers={
                "Authorization": f"Bearer {login_access_token}",
                **csrf_headers,
            },
        )
        assert res_logout_all.status_code == status.HTTP_204_NO_CONTENT
        assert db.users[created_user.id].version == 1

        # Token invalidated due to session revocation / version bump
        res_me_bumped = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {login_access_token}"},
        )
        assert res_me_bumped.status_code == status.HTTP_401_UNAUTHORIZED

        # Refresh also rejected after logout-all
        res_refresh_bumped = client.post(
            "/api/auth/refresh",
            headers=csrf_headers,
        )
        assert res_refresh_bumped.status_code == status.HTTP_401_UNAUTHORIZED

        # ======================================================================
        # 8. Account status restriction: disabled account cannot login or /me
        # ======================================================================
        # 8a. Login again with bumped version succeeds and creates fresh session
        res_login_v1 = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": user_password},
        )
        assert res_login_v1.status_code == status.HTTP_200_OK
        v1_access_token = res_login_v1.json()["access_token"]

        # 8b. Disable the account in database
        db.users[created_user.id].status = "disabled"

        # Calling /me is immediately rejected
        res_me_disabled = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {v1_access_token}"},
        )
        assert res_me_disabled.status_code == status.HTTP_401_UNAUTHORIZED
        assert "disabled" in res_me_disabled.json()["detail"]

        # Login attempt with disabled account is rejected
        res_login_disabled = client.post(
            "/api/auth/login",
            json={"email": user_email, "password": user_password},
        )
        assert res_login_disabled.status_code == status.HTTP_401_UNAUTHORIZED
        assert "disabled" in res_login_disabled.json()["detail"]
