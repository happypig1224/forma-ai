"""End-to-end integration test spanning Phase 1, Phase 2, and Phase 3.

Validates the entire business lifecycle on real Redis 7.4.0:
1. Issue initial TokenPair & register session in Redis via Lua.
2. Authenticate requests through FastAPI Bearer dependencies.
3. Verify role (admin) and status (disabling) guards.
4. Atomically rotate Refresh Token via Lua, updating cookie and access token.
5. Demonstrate RFC 9700 replay attack mitigation (instant session revocation).
6. Perform single-device logout (session destruction).
7. Perform multi-device logout-all via MySQL version increment.
"""

import shutil
import subprocess
import time
from collections.abc import Generator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis

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

E2E_REDIS_PORT = 6389


@pytest.fixture(scope="module")
def real_redis_server() -> Generator[Redis]:
    """Launch an isolated real Redis server on port 6389."""
    redis_exe = (
        shutil.which("redis-server") or r"D:\Redis-7.4.0-Windows\redis-server.exe"
    )
    proc = subprocess.Popen(
        [
            redis_exe,
            "--port",
            str(E2E_REDIS_PORT),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client: Redis = Redis(host="127.0.0.1", port=E2E_REDIS_PORT, decode_responses=True)
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
        pytest.fail("Failed to connect to isolated test Redis server")

    yield client

    client.close()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


class InMemoryDbSession:
    """In-memory database session mimicking MySQL behavior for UserAccount."""

    def __init__(self, users: dict[int, UserAccount]):
        self.users = users

    def scalar(self, stmt):
        for expr in (
            stmt.whereclause.clauses
            if hasattr(stmt.whereclause, "clauses")
            else [stmt.whereclause]
        ):
            if hasattr(expr, "right") and hasattr(expr.right, "value"):
                user_id = expr.right.value
                return self.users.get(user_id)
        return None

    def execute(self, stmt):
        if hasattr(stmt, "table") and stmt.table.name == "user_account":
            for expr in (
                stmt.whereclause.clauses
                if hasattr(stmt.whereclause, "clauses")
                else [stmt.whereclause]
            ):
                if hasattr(expr, "right") and hasattr(expr.right, "value"):
                    uid = expr.right.value
                    if uid in self.users:
                        self.users[uid].version += 1
        return None

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def e2e_env(real_redis_server: Redis):
    """Setup integrated application with real Redis and in-memory MySQL users."""
    real_redis_server.flushdb()

    settings = Settings(
        app_env="test",
        cors_origins=["https://dashboard.formaai.test"],
        cors_allow_credentials=True,
        mysql_database="formaai_e2e",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr(f"redis://127.0.0.1:{E2E_REDIS_PORT}/0"),
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

    # Initial users
    alice = UserAccount()
    alice.id = 1
    alice.email = "alice@formaai.test"
    alice.role = "user"
    alice.status = "active"
    alice.version = 0

    bob_admin = UserAccount()
    bob_admin.id = 2
    bob_admin.email = "bob@formaai.test"
    bob_admin.role = "admin"
    bob_admin.status = "active"
    bob_admin.version = 0

    charlie_disabling = UserAccount()
    charlie_disabling.id = 3
    charlie_disabling.email = "charlie@formaai.test"
    charlie_disabling.role = "user"
    charlie_disabling.status = "disabling"
    charlie_disabling.version = 0

    users_db = {
        alice.id: alice,
        bob_admin.id: bob_admin,
        charlie_disabling.id: charlie_disabling,
    }

    app = FastAPI(title="FormaAI-E2E")
    app.include_router(auth_router)

    # Protected sample business routes
    @app.get("/api/user/profile")
    def get_profile(user: Annotated[UserAccount, Depends(get_current_user)]):
        return {"id": user.id, "email": user.email, "role": user.role}

    @app.get("/api/admin/metrics")
    def get_metrics(admin: Annotated[UserAccount, Depends(require_admin)]):
        return {"system_health": "100%", "active_nodes": 4}

    @app.post("/api/tasks/create")
    def create_task(user: Annotated[UserAccount, Depends(require_can_create_tasks)]):
        return {"task_id": "task-xyz-999", "status": "created"}

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.dependency_overrides[get_db] = lambda: InMemoryDbSession(users_db)

    return {
        "app": app,
        "settings": settings,
        "session_manager": session_manager,
        "users": users_db,
        "redis": real_redis_server,
    }


def test_full_business_lifecycle_across_all_three_phases(e2e_env):
    """Full 8-stage end-to-end integration test of JWT + Redis authentication."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    session_manager: SessionManager = e2e_env["session_manager"]
    users = e2e_env["users"]
    alice: UserAccount = users[1]

    client = TestClient(app)

    # -------------------------------------------------------------
    # STAGE 1: 签发 (Phase 1 Token 签发 + Phase 2 Redis 会话落库)
    # -------------------------------------------------------------
    now = int(time.time())
    pair_v1 = create_token_pair(
        user_id=alice.id, version=alice.version, settings=settings
    )
    # Save session in real Redis 7.4.0 via Lua script
    saved = session_manager.create_session(
        session_id=pair_v1.session_id,
        user_id=alice.id,
        version=alice.version,
        refresh_digest=pair_v1.refresh_digest,
        now=now,
    )
    assert saved is True

    # Check key exists in real Redis
    redis_data = session_manager.get_session(pair_v1.session_id)
    assert redis_data is not None
    assert redis_data.refresh_digest == pair_v1.refresh_digest

    # -------------------------------------------------------------
    # STAGE 2: 访问受保护业务接口 (Phase 3 FastAPI Bearer 鉴权)
    # -------------------------------------------------------------
    res_profile = client.get(
        "/api/user/profile",
        headers={"Authorization": f"Bearer {pair_v1.access_token}"},
    )
    assert res_profile.status_code == status.HTTP_200_OK
    assert res_profile.json()["email"] == "alice@formaai.test"

    # -------------------------------------------------------------
    # STAGE 3: 行为与权限守卫拦截 (Role & Status Guards)
    # -------------------------------------------------------------
    # 1. Alice is 'user', attempting admin endpoint -> 403 Forbidden
    res_admin = client.get(
        "/api/admin/metrics",
        headers={"Authorization": f"Bearer {pair_v1.access_token}"},
    )
    assert res_admin.status_code == status.HTTP_403_FORBIDDEN

    # 2. Bob is 'admin', issues token and visits admin endpoint -> 200 OK
    bob = users[2]
    bob_pair = create_token_pair(user_id=bob.id, version=bob.version, settings=settings)
    session_manager.create_session(
        session_id=bob_pair.session_id,
        user_id=bob.id,
        version=bob.version,
        refresh_digest=bob_pair.refresh_digest,
    )
    res_bob = client.get(
        "/api/admin/metrics",
        headers={"Authorization": f"Bearer {bob_pair.access_token}"},
    )
    assert res_bob.status_code == status.HTTP_200_OK
    assert res_bob.json()["system_health"] == "100%"

    # 3. Charlie is 'disabling': can read profile (200), but cannot create tasks (403)
    charlie = users[3]
    charlie_pair = create_token_pair(
        user_id=charlie.id, version=charlie.version, settings=settings
    )
    session_manager.create_session(
        session_id=charlie_pair.session_id,
        user_id=charlie.id,
        version=charlie.version,
        refresh_digest=charlie_pair.refresh_digest,
    )
    charlie_auth = {"Authorization": f"Bearer {charlie_pair.access_token}"}
    res_charlie_read = client.get("/api/user/profile", headers=charlie_auth)
    assert res_charlie_read.status_code == status.HTTP_200_OK

    res_charlie_task = client.post("/api/tasks/create", headers=charlie_auth)
    assert res_charlie_task.status_code == status.HTTP_403_FORBIDDEN
    assert "disabling" in res_charlie_task.json()["detail"]

    # -------------------------------------------------------------
    # STAGE 4: Token 刷新轮换 (POST /api/auth/refresh)
    # -------------------------------------------------------------
    client.cookies.set("formaai_refresh", pair_v1.refresh_token)
    res_refresh = client.post(
        "/api/auth/refresh",
        headers={
            "Origin": "https://dashboard.formaai.test",
            "X-CSRF-Protection": "1",
        },
    )
    assert res_refresh.status_code == status.HTTP_200_OK
    refresh_json = res_refresh.json()
    new_access_token = refresh_json["access_token"]
    assert new_access_token != pair_v1.access_token

    # New refresh token cookie received
    new_refresh_token = res_refresh.cookies["formaai_refresh"]
    assert new_refresh_token != pair_v1.refresh_token

    # -------------------------------------------------------------
    # STAGE 5: 使用新 Access Token 访问正常业务
    # -------------------------------------------------------------
    res_new_access = client.get(
        "/api/user/profile",
        headers={"Authorization": f"Bearer {new_access_token}"},
    )
    assert res_new_access.status_code == status.HTTP_200_OK

    # -------------------------------------------------------------
    # STAGE 6: 重放攻击拦截与熔断 (RFC 9700 Replay Attack Mitigation)
    # -------------------------------------------------------------
    # Attacker steals or replays the OLD Refresh Token (pair_v1.refresh_token)
    client.cookies.clear()
    client.cookies.set("formaai_refresh", pair_v1.refresh_token)
    res_replay = client.post(
        "/api/auth/refresh",
        headers={
            "Origin": "https://dashboard.formaai.test",
            "X-CSRF-Protection": "1",
        },
    )
    assert res_replay.status_code == status.HTTP_401_UNAUTHORIZED
    assert "replayed" in res_replay.json()["detail"].lower()

    # Replay penalty: The session in Redis was COMPLETELY NUKED!
    assert session_manager.get_session(pair_v1.session_id) is None

    # Even the new access token obtained in Stage 4 is now dead!
    res_dead_access = client.get(
        "/api/user/profile",
        headers={"Authorization": f"Bearer {new_access_token}"},
    )
    assert res_dead_access.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in res_dead_access.json()["detail"].lower()

    # -------------------------------------------------------------
    # STAGE 7: 重新登录并测试单端退出 (POST /api/auth/logout)
    # -------------------------------------------------------------
    pair_v2 = create_token_pair(
        user_id=alice.id, version=alice.version, settings=settings
    )
    session_manager.create_session(
        session_id=pair_v2.session_id,
        user_id=alice.id,
        version=alice.version,
        refresh_digest=pair_v2.refresh_digest,
    )

    client.cookies.clear()
    client.cookies.set("formaai_refresh", pair_v2.refresh_token)
    res_logout = client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://dashboard.formaai.test",
            "X-CSRF-Protection": "1",
        },
    )
    assert res_logout.status_code == status.HTTP_204_NO_CONTENT
    assert session_manager.get_session(pair_v2.session_id) is None

    # Access Token fails immediately
    res_after_logout = client.get(
        "/api/user/profile",
        headers={"Authorization": f"Bearer {pair_v2.access_token}"},
    )
    assert res_after_logout.status_code == status.HTTP_401_UNAUTHORIZED

    # -------------------------------------------------------------
    # STAGE 8: 多端登录与全账号撤销 (POST /api/auth/logout-all)
    # -------------------------------------------------------------
    # Alice logs into Device 1 (PC) and Device 2 (Mobile)
    device_1 = create_token_pair(
        user_id=alice.id,
        version=alice.version,
        settings=settings,
        session_id="device-pc",
    )
    session_manager.create_session(
        session_id="device-pc",
        user_id=alice.id,
        version=alice.version,
        refresh_digest=device_1.refresh_digest,
    )

    device_2 = create_token_pair(
        user_id=alice.id,
        version=alice.version,
        settings=settings,
        session_id="device-mobile",
    )
    session_manager.create_session(
        session_id="device-mobile",
        user_id=alice.id,
        version=alice.version,
        refresh_digest=device_2.refresh_digest,
    )

    # Both devices can access profile
    assert (
        client.get(
            "/api/user/profile",
            headers={"Authorization": f"Bearer {device_1.access_token}"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/user/profile",
            headers={"Authorization": f"Bearer {device_2.access_token}"},
        ).status_code
        == 200
    )

    # Device 1 calls logout-all
    client.cookies.clear()
    client.cookies.set("formaai_refresh", device_1.refresh_token)
    res_logout_all = client.post(
        "/api/auth/logout-all",
        headers={
            "Authorization": f"Bearer {device_1.access_token}",
            "Origin": "https://dashboard.formaai.test",
            "X-CSRF-Protection": "1",
        },
    )
    assert res_logout_all.status_code == status.HTTP_204_NO_CONTENT

    # MySQL version was atomically incremented
    assert alice.version == 1

    # Device 2 (Mobile) is immediately invalidated across all endpoints!
    res_mobile_access = client.get(
        "/api/user/profile",
        headers={"Authorization": f"Bearer {device_2.access_token}"},
    )
    assert res_mobile_access.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in res_mobile_access.json()["detail"].lower()

    # Device 2 cannot even refresh
    client.cookies.set("formaai_refresh", device_2.refresh_token)
    res_mobile_refresh = client.post(
        "/api/auth/refresh",
        headers={
            "Origin": "https://dashboard.formaai.test",
            "X-CSRF-Protection": "1",
        },
    )
    assert res_mobile_refresh.status_code == status.HTTP_401_UNAUTHORIZED
