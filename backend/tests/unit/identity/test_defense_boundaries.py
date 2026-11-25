"""Specialized unit tests for security boundary defense and contract integrity.

Covers:
1. Email case-insensitivity and normalization defense.
2. Duplicate registration collision defense.
3. Token tampering, rogue secret forgery, expired token, and invalid audience.
4. Real-time account status mutation: active -> disabling -> disabled/deleted.
"""

import shutil
import subprocess
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
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

DEFENSE_REDIS_PORT = 6392


@pytest.fixture(scope="module")
def real_redis_server() -> Generator[Redis]:
    """Launch an isolated real Redis 7.4.0 server for boundary defense tests."""
    redis_exe = (
        shutil.which("redis-server") or r"D:\Redis-7.4.0-Windows\redis-server.exe"
    )
    proc = subprocess.Popen(
        [
            redis_exe,
            "--port",
            str(DEFENSE_REDIS_PORT),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client: Redis = Redis(
        host="127.0.0.1", port=DEFENSE_REDIS_PORT, decode_responses=True
    )
    connected = False
    for _ in range(30):
        try:
            if client.ping():
                connected = True
                break
        except Exception:
            time.sleep(0.1)

    if not connected:
        proc.kill()
        pytest.fail("Failed to connect to isolated test Redis server for defense tests")

    yield client

    client.close()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


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
def defense_env(real_redis_server: Redis) -> dict[str, Any]:
    """Setup FastAPI test app for security boundary validation."""
    real_redis_server.flushdb()

    settings = Settings(
        app_env="test",
        cors_origins=["https://dashboard.formaai.test"],
        cors_allow_credentials=True,
        mysql_database="formaai_defense",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr(f"redis://127.0.0.1:{DEFENSE_REDIS_PORT}/0"),
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

    # Protected endpoint guarding write actions
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


# ==============================================================================
# 1. Email Normalization & Case-Insensitive Anti-Spoofing Defense
# ==============================================================================


def test_email_case_insensitivity_normalization_defense(
    defense_env: dict[str, Any],
) -> None:
    app = defense_env["app"]
    db: InMemoryDbSession = defense_env["db"]

    mixed_email = "Alice.Dev@FormaAI.test"
    canonical_email = "alice.dev@formaai.test"
    password = "SuperPassword123!"

    with TestClient(app) as client:
        # 1. Register with mixed case directly
        res_reg = client.post(
            "/api/auth/register",
            json={
                "email": mixed_email,
                "password": password,
            },
        )
        assert res_reg.status_code == status.HTTP_201_CREATED

        # Stored in DB must be canonical lowercase
        created_user = next(iter(db.users.values()))
        assert created_user.email == canonical_email

        # 2. Login with uppercase variant
        res_login_upper = client.post(
            "/api/auth/login",
            json={
                "email": "ALICE.DEV@FORMAAI.TEST",
                "password": password,
            },
        )
        assert res_login_upper.status_code == status.HTTP_200_OK

        # 3. Duplicate registration attempt with different casing rejected
        res_dup = client.post(
            "/api/auth/register",
            json={
                "email": "aLiCe.DeV@formaai.test",
                "password": "AnotherPassword456!",
            },
        )
        assert res_dup.status_code == status.HTTP_409_CONFLICT
        assert "already registered" in res_dup.json()["detail"]


# ==============================================================================
# 2. Token Tampering, Expired, and Rogue Signature Defenses
# ==============================================================================


def test_token_forgery_and_tampering_defenses(
    defense_env: dict[str, Any],
) -> None:
    app = defense_env["app"]
    settings: Settings = defense_env["settings"]

    with TestClient(app) as client:
        # 2a. Malformed garbage token
        res_malformed = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
        )
        assert res_malformed.status_code == status.HTTP_401_UNAUTHORIZED

        # 2b. Rogue signature signed with attacker secret
        rogue_secret = "attacker-secret-strictly-32-bytes!"
        now = datetime.now(UTC)
        rogue_payload = {
            "sub": "1",
            "ver": 0,
            "sid": "attacker_sid",
            "jti": "jti_rogue_1",
            "typ": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        }
        rogue_jwt = jwt.encode(rogue_payload, rogue_secret, algorithm="HS256")
        res_rogue = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {rogue_jwt}"},
        )
        assert res_rogue.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Signature verification failed" in res_rogue.json()["detail"]

        # 2c. Expired token signed with genuine secret
        expired_payload = {
            "sub": "1",
            "ver": 0,
            "sid": "sid_123",
            "jti": "jti_expired_1",
            "typ": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int((now - timedelta(minutes=20)).timestamp()),
            "exp": int((now - timedelta(minutes=5)).timestamp()),
        }
        expired_jwt = jwt.encode(
            expired_payload,
            settings.jwt_access_secret.get_secret_value(),
            algorithm="HS256",
        )
        res_expired = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired_jwt}"},
        )
        assert res_expired.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Token has expired" in res_expired.json()["detail"]

        # 2d. Token with wrong audience
        wrong_aud_payload = {
            "sub": "1",
            "ver": 0,
            "sid": "sid_123",
            "jti": "jti_wrong_aud_1",
            "typ": "access",
            "iss": settings.jwt_issuer,
            "aud": "rogue-client-audience",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        }
        wrong_aud_jwt = jwt.encode(
            wrong_aud_payload,
            settings.jwt_access_secret.get_secret_value(),
            algorithm="HS256",
        )
        res_wrong_aud = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {wrong_aud_jwt}"},
        )
        assert res_wrong_aud.status_code == status.HTTP_401_UNAUTHORIZED


# ==============================================================================
# 3. Real-time Account Status Mutation & Guard Isolation Defense
# ==============================================================================


def test_realtime_account_status_mutation_isolation(
    defense_env: dict[str, Any],
) -> None:
    app = defense_env["app"]
    db: InMemoryDbSession = defense_env["db"]
    email = "state.mutation@formaai.test"
    password = "SuperPassword123!"

    with TestClient(app) as client:
        # Register user directly
        res_reg = client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        assert res_reg.status_code == status.HTTP_201_CREATED
        token = res_reg.json()["access_token"]
        auth_header = {"Authorization": f"Bearer {token}"}
        user_id = next(iter(db.users.keys()))

        # Status 1: active -> /me and create_task both succeed
        res_me_active = client.get("/api/auth/me", headers=auth_header)
        assert res_me_active.status_code == status.HTTP_200_OK
        res_task_active = client.post("/test/tasks", headers=auth_header)
        assert res_task_active.status_code == status.HTTP_200_OK

        # Status 2: mutating to 'disabling' (cooling down)
        db.users[user_id].status = "disabling"
        # Reading data still permitted
        res_me_disabling = client.get("/api/auth/me", headers=auth_header)
        assert res_me_disabling.status_code == status.HTTP_200_OK
        assert res_me_disabling.json()["status"] == "disabling"
        # Creating tasks strictly prohibited
        res_task_disabling = client.post("/test/tasks", headers=auth_header)
        assert res_task_disabling.status_code == status.HTTP_403_FORBIDDEN
        assert "disabling" in res_task_disabling.json()["detail"]

        # Status 3: mutating to 'disabled' (frozen/banned)
        db.users[user_id].status = "disabled"
        # Even reading is immediately cut off
        res_me_disabled = client.get("/api/auth/me", headers=auth_header)
        assert res_me_disabled.status_code == status.HTTP_401_UNAUTHORIZED
        assert "disabled" in res_me_disabled.json()["detail"]

        # Status 4: mutating to 'deleted' (soft deleted)
        db.users[user_id].status = "deleted"
        res_me_deleted = client.get("/api/auth/me", headers=auth_header)
        assert res_me_deleted.status_code == status.HTTP_401_UNAUTHORIZED
        assert "deleted" in res_me_deleted.json()["detail"]
