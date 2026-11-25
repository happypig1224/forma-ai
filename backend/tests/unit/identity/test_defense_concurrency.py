"""Concurrency, race condition, and RFC 9700 replay attack defense tests.

Covers:
1. Concurrent duplicate registration race condition: exactly 1 wins, others 409.
2. RFC 9700 Refresh Token replay detection: session family instantly terminated.
3. Concurrent refresh race condition: atomic Lua rotation ensures zero token collision.
"""

import concurrent.futures
import shutil
import subprocess
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime
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
from .redis_provider import provide_test_redis

DEFENSE_CONCURRENCY_PORT = 6393


@pytest.fixture(scope="module")
def real_redis_server() -> Generator[Redis]:
    """Launch an isolated real Redis server or connect to available test Redis."""
    yield from provide_test_redis(DEFENSE_CONCURRENCY_PORT, db=13)


class SharedDatabase:
    """Thread-safe in-memory store simulating ACID database storage."""

    def __init__(self) -> None:
        self.users: dict[int, UserAccount] = {}
        self.lock = threading.Lock()
        self._next_id = 1


class ThreadSafeDbSession:
    """Individual database session operating on SharedDatabase with row locking."""

    def __init__(self, shared_db: SharedDatabase) -> None:
        self.shared_db = shared_db
        self._pending: list[UserAccount] = []

    def scalar(self, stmt: Any) -> UserAccount | None:
        with self.shared_db.lock:
            whereclause = getattr(stmt, "whereclause", None)
            if whereclause is None:
                return None
            clauses = (
                whereclause.clauses
                if hasattr(whereclause, "clauses")
                else [whereclause]
            )
            for expr in clauses:
                if hasattr(expr, "left") and hasattr(expr, "right"):
                    col_name = getattr(expr.left, "name", None)
                    val = getattr(expr.right, "value", None)
                    if col_name == "email":
                        for u in self.shared_db.users.values():
                            if u.email == val:
                                return u
                    elif col_name == "id":
                        return self.shared_db.users.get(val)
            return None

    def add(self, instance: UserAccount) -> None:
        with self.shared_db.lock:
            if instance.id is None:
                instance.id = self.shared_db._next_id
                self.shared_db._next_id += 1
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
        with self.shared_db.lock:
            for u in self._pending:
                for existing_id, existing_u in self.shared_db.users.items():
                    if existing_u.email == u.email and existing_id != u.id:
                        raise IntegrityError(
                            statement="INSERT INTO user_account...",
                            params={},
                            orig=Exception(
                                "Duplicate entry for key 'uq_user_account_email'"
                            ),
                        )
                self.shared_db.users[u.id] = u
            self._pending.clear()

    def rollback(self) -> None:
        with self.shared_db.lock:
            self._pending.clear()

    def refresh(self, instance: UserAccount) -> None:
        with self.shared_db.lock:
            if instance.id in self.shared_db.users:
                stored = self.shared_db.users[instance.id]
                instance.version = getattr(stored, "version", 0)
                instance.status = getattr(stored, "status", "active")

    def execute(self, stmt: Any) -> None:
        with self.shared_db.lock:
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
                                if uid in self.shared_db.users:
                                    self.shared_db.users[uid].version += 1

    def __enter__(self) -> ThreadSafeDbSession:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def concurrency_env(real_redis_server: Redis) -> dict[str, Any]:
    """Setup FastAPI application for concurrency and race condition tests."""
    real_redis_server.flushdb()

    settings = Settings(
        app_env="test",
        cors_origins=["https://dashboard.formaai.test"],
        cors_allow_credentials=True,
        mysql_database="formaai_concurrency",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr(f"redis://127.0.0.1:{DEFENSE_CONCURRENCY_PORT}/0"),
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

    shared_db = SharedDatabase()

    app = FastAPI()
    app.include_router(auth_router)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.dependency_overrides[get_db] = lambda: ThreadSafeDbSession(shared_db)

    return {
        "app": app,
        "settings": settings,
        "session_manager": session_manager,
        "shared_db": shared_db,
        "redis": real_redis_server,
    }


# ==============================================================================
# 1. Concurrent Duplicate Registration Race Condition Defense
# ==============================================================================


def test_concurrent_registration_race_defense(
    concurrency_env: dict[str, Any],
) -> None:
    """10 simultaneous register requests for same email: strictly 1 succeeds."""
    app = concurrency_env["app"]
    shared_db: SharedDatabase = concurrency_env["shared_db"]

    email = "concur.reg@formaai.test"
    password = "SuperPassword123!"

    results: list[int] = []

    def worker_register() -> int:
        c = TestClient(app)
        r = c.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futs = [executor.submit(worker_register) for _ in range(10)]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())

    # Exactly 1 success (201 Created), remaining 9 fail with 409
    count_201 = results.count(status.HTTP_201_CREATED)
    count_errors = len([c for c in results if c in (400, 409)])
    assert count_201 == 1
    assert count_errors == 9
    assert 500 not in results

    # Database contains strictly ONE user record for this email
    with shared_db.lock:
        users = [u for u in shared_db.users.values() if u.email == email]
        assert len(users) == 1


# ==============================================================================
# 2. RFC 9700 Refresh Token Replay Mitigation (Family Termination)
# ==============================================================================


def test_rfc9700_refresh_replay_family_termination(
    concurrency_env: dict[str, Any],
) -> None:
    """Replaying an old refresh token terminates the entire session family."""
    app = concurrency_env["app"]
    csrf_headers = {
        "Origin": "https://dashboard.formaai.test",
        "X-CSRF-Protection": "1",
    }
    email = "replay.victim@formaai.test"
    password = "SuperPassword123!"

    client = TestClient(app)

    # 1. Register user directly
    res_reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert res_reg.status_code == status.HTTP_201_CREATED
    initial_refresh_token = res_reg.cookies["formaai_refresh"]

    # 2. Legitimate user rotates token: refresh_0 -> refresh_1
    client.cookies.set("formaai_refresh", initial_refresh_token)
    res_refresh1 = client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_refresh1.status_code == status.HTTP_200_OK
    new_refresh_token = res_refresh1.cookies["formaai_refresh"]
    legitimate_access_token = res_refresh1.json()["access_token"]
    assert new_refresh_token != initial_refresh_token

    # 3. Attacker uses stolen initial_refresh_token to refresh (REPLAY ATTACK)
    attacker_client = TestClient(app)
    attacker_client.cookies.set("formaai_refresh", initial_refresh_token)
    res_attacker = attacker_client.post("/api/auth/refresh", headers=csrf_headers)

    # Redis detects old digest mismatch -> deletes session -> returns 401
    assert res_attacker.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Refresh token replayed" in res_attacker.json()["detail"]

    # 4. Now legitimate user tries to use new_refresh_token -> FAIL (family killed)
    client.cookies.set("formaai_refresh", new_refresh_token)
    res_legit_refresh = client.post("/api/auth/refresh", headers=csrf_headers)
    assert res_legit_refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Session expired or invalid" in res_legit_refresh.json()["detail"]

    # 5. Legitimate access token is also rejected from /me (session dead in Redis)
    res_me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {legitimate_access_token}"},
    )
    assert res_me.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Session has been revoked or expired" in res_me.json()["detail"]


# ==============================================================================
# 3. Concurrent Refresh Race Condition Defense
# ==============================================================================


def test_concurrent_refresh_race_defense(
    concurrency_env: dict[str, Any],
) -> None:
    """Simultaneous refresh with same token: exactly 1 wins, other triggers replay."""
    app = concurrency_env["app"]
    csrf_headers = {
        "Origin": "https://dashboard.formaai.test",
        "X-CSRF-Protection": "1",
    }
    email = "race.refresh@formaai.test"
    password = "SuperPassword123!"

    client = TestClient(app)

    # 1. Register user directly
    res_reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert res_reg.status_code == status.HTTP_201_CREATED
    shared_refresh_token = res_reg.cookies["formaai_refresh"]

    # 2. Fire 2 simultaneous refresh calls with identical refresh token
    results: list[int] = []

    def worker_refresh() -> int:
        c = TestClient(app)
        c.cookies.set("formaai_refresh", shared_refresh_token)
        r = c.post("/api/auth/refresh", headers=csrf_headers)
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futs = [executor.submit(worker_refresh) for _ in range(2)]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())

    # One request succeeded (200), the other triggered replay detection (401)
    assert 200 in results
    assert 401 in results
