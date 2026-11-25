"""Comprehensive End-to-End defense matrix acceptance test.

Simulates a full red-blue adversarial campaign across key defense vectors:
Vector 1: Mixed-case spoofing and duplicate registration collision defense.
Vector 2: RFC 9700 token replay detection and session family self-destruction.
Vector 3: Administrative state transition lockout.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis

from formaai.config import Settings
from formaai.modules.identity.dependencies import (
    get_db,
    get_session_manager,
    get_settings,
    require_can_create_tasks,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.router import auth_router
from formaai.modules.identity.sessions import SessionManager

from .redis_provider import provide_test_redis

DEFENSE_MATRIX_PORT = 6394


@pytest.fixture(scope="module")
def real_redis_server() -> Generator[Redis]:
    """Launch an isolated real Redis server or connect to available test Redis."""
    yield from provide_test_redis(DEFENSE_MATRIX_PORT, db=14)


class InMemoryDbSession:
    """In-memory database session mimicking MySQL behavior for UserAccount."""

    def __init__(self, users: dict[int, UserAccount] | None = None) -> None:
        self.users: dict[int, UserAccount] = users if users is not None else {}
        self._next_id = max(self.users.keys(), default=0) + 1
        self._pending: list[UserAccount] = []

    def scalar(self, stmt: Any) -> UserAccount | None:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col_name = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col_name == "email":
                    for u in self.users.values():
                        if u.email == val:
                            return u
                elif col_name == "id":
                    return self.users.get(val)
        return None

    def add(self, instance: UserAccount) -> None:
        if instance.id is None:
            instance.id = self._next_id
            self._next_id += 1
        if getattr(instance, "version", None) is None:
            instance.version = 0
        if getattr(instance, "status", None) is None:
            instance.status = "active"
        if getattr(instance, "role", None) is None:
            instance.role = "user"
        if getattr(instance, "created_at", None) is None:
            instance.created_at = datetime.now(UTC)
        self._pending.append(instance)

    def commit(self) -> None:
        for u in self._pending:
            self.users[u.id] = u
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: UserAccount) -> None:
        if instance.id in self.users:
            stored = self.users[instance.id]
            instance.version = getattr(stored, "version", 0)
            instance.status = getattr(stored, "status", "active")

    def execute(self, stmt: Any) -> None:
        if hasattr(stmt, "table") and stmt.table.name == "user_account":
            whereclause = getattr(stmt, "whereclause", None)
            if whereclause is not None:
                clauses = (
                    whereclause.clauses
                    if hasattr(whereclause, "clauses")
                    else [whereclause]
                )
                for expr in clauses:
                    if hasattr(expr, "left") and hasattr(expr, "right"):
                        if expr.left.name == "id":
                            uid = expr.right.value
                            if uid in self.users:
                                self.users[uid].version += 1

    def __enter__(self) -> InMemoryDbSession:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def matrix_env(real_redis_server: Redis) -> dict[str, Any]:
    """Setup FastAPI application for comprehensive defense matrix tests."""
    real_redis_server.flushdb()

    settings = Settings(
        app_env="test",
        cors_origins=["https://dashboard.formaai.test"],
        cors_allow_credentials=True,
        mysql_database="formaai_matrix",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr(f"redis://127.0.0.1:{DEFENSE_MATRIX_PORT}/0"),
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

    @app.post("/test/tasks")
    def create_task_endpoint(
        user: Annotated[UserAccount, Depends(require_can_create_tasks)],
    ):
        return {"task_created": True, "user_id": user.id}

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


def test_adversarial_defense_matrix_end_to_end(
    matrix_env: dict[str, Any],
) -> None:
    """Execute complete red-blue adversarial campaign against defense vectors."""
    app = matrix_env["app"]
    db: InMemoryDbSession = matrix_env["db"]

    target_email = "target.user@formaai.test"
    target_pwd = "SecureTargetPassword123!"
    csrf_headers = {
        "Origin": "https://dashboard.formaai.test",
        "X-CSRF-Protection": "1",
    }

    client = TestClient(app)

    # ==========================================================================
    # VECTOR 1: Casing Spoofing & Duplicate Registration Defense
    # ==========================================================================
    # 1a. Register with mixed-case email directly
    mixed_email = "Target.User@FormaAI.test"
    res_reg = client.post(
        "/api/auth/register",
        json={
            "email": mixed_email,
            "password": target_pwd,
        },
    )
    assert res_reg.status_code == status.HTTP_201_CREATED
    initial_refresh_token = res_reg.cookies["formaai_refresh"]

    # Database email must be strictly lowercase
    user_id = next(iter(db.users.keys()))
    assert db.users[user_id].email == target_email

    # 1b. Attacker attempts to register with lowercase variant
    res_dup_lower = client.post(
        "/api/auth/register",
        json={
            "email": target_email,
            "password": "AttackerPassword123!",
        },
    )
    assert res_dup_lower.status_code == status.HTTP_409_CONFLICT
    assert "already registered" in res_dup_lower.json()["detail"]

    # 1c. Attacker attempts to register with all-caps variant
    res_dup_upper = client.post(
        "/api/auth/register",
        json={
            "email": "TARGET.USER@FORMAAI.TEST",
            "password": "AttackerPassword123!",
        },
    )
    assert res_dup_upper.status_code == status.HTTP_409_CONFLICT
    assert "already registered" in res_dup_upper.json()["detail"]

    # ==========================================================================
    # VECTOR 2: RFC 9700 Replay Attack & Instant Family Destruction
    # ==========================================================================
    # 2a. Legitimate user rotates tokens
    client.cookies.set("formaai_refresh", initial_refresh_token)
    res_rot = client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_rot.status_code == status.HTTP_200_OK
    rotated_refresh_token = res_rot.cookies["formaai_refresh"]
    rotated_access_token = res_rot.json()["access_token"]
    assert rotated_refresh_token != initial_refresh_token

    # 2b. Attacker replays intercepted initial_refresh_token
    attacker_client = TestClient(app)
    attacker_client.cookies.set("formaai_refresh", initial_refresh_token)
    res_replay = attacker_client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_replay.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Refresh token replayed" in res_replay.json()["detail"]

    # 2c. Session family is dead: legitimate rotated refresh token fails
    client.cookies.set("formaai_refresh", rotated_refresh_token)
    res_dead_refresh = client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_dead_refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Session expired or invalid" in res_dead_refresh.json()["detail"]

    # 2d. Legitimate access token fails on /me
    res_dead_access = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {rotated_access_token}"},
    )
    assert res_dead_access.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Session has been revoked" in res_dead_access.json()["detail"]

    # ==========================================================================
    # VECTOR 3: Recovery Login & Multi-Stage Account Status Cutoff
    # ==========================================================================
    # 3a. User logs in with password to re-establish session
    res_login = client.post(
        "/api/auth/login",
        json={"email": target_email, "password": target_pwd},
    )
    assert res_login.status_code == status.HTTP_200_OK
    live_access_token = res_login.json()["access_token"]
    live_refresh_cookie = res_login.cookies["formaai_refresh"]
    auth_header = {"Authorization": f"Bearer {live_access_token}"}

    # Verify profile and task creation work normally
    assert client.get("/api/auth/me", headers=auth_header).status_code == 200
    assert client.post("/test/tasks", headers=auth_header).status_code == 200

    # 3b. Status transition: 'disabling' (cooling down period)
    db.users[user_id].status = "disabling"
    # Reading profile is allowed
    assert client.get("/api/auth/me", headers=auth_header).status_code == 200
    # Creating new tasks is prohibited (403 Forbidden)
    res_disabling_task = client.post("/test/tasks", headers=auth_header)
    assert res_disabling_task.status_code == status.HTTP_403_FORBIDDEN
    assert "disabling" in res_disabling_task.json()["detail"]

    # 3c. Status transition: 'disabled' (frozen by administrator)
    db.users[user_id].status = "disabled"
    # Immediate read access revoked (401)
    res_disabled_me = client.get("/api/auth/me", headers=auth_header)
    assert res_disabled_me.status_code == status.HTTP_401_UNAUTHORIZED
    assert "disabled" in res_disabled_me.json()["detail"]

    # Refresh attempt rejected (401)
    client.cookies.set("formaai_refresh", live_refresh_cookie)
    res_disabled_refresh = client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_disabled_refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Account status or version invalid" in res_disabled_refresh.json()["detail"]

    # Login attempt rejected (401)
    res_disabled_login = client.post(
        "/api/auth/login",
        json={"email": target_email, "password": target_pwd},
    )
    assert res_disabled_login.status_code == status.HTTP_401_UNAUTHORIZED
    assert "disabled" in res_disabled_login.json()["detail"]

    # 3d. Status transition: 'deleted' (soft deleted)
    db.users[user_id].status = "deleted"
    db.users[user_id].deleted_at = datetime.now(UTC)
    # Existing session already revoked or cut off
    res_deleted_me = client.get("/api/auth/me", headers=auth_header)
    assert res_deleted_me.status_code == status.HTTP_401_UNAUTHORIZED

    # Login rejected with deleted status
    res_deleted_login = client.post(
        "/api/auth/login",
        json={"email": target_email, "password": target_pwd},
    )
    assert res_deleted_login.status_code == status.HTTP_401_UNAUTHORIZED
    assert "deleted" in res_deleted_login.json()["detail"]
