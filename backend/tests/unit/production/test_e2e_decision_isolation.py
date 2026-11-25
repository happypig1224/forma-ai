"""End-to-end multi-tenant decision isolation and concurrency tests.

Validates security, audit integrity, and state machine boundaries for video decisions:
1. Cross-tenant IDOR attack defense: Bob cannot submit decisions for Alice's task (404).
2. Cross-tenant IDOR audit defense: Bob cannot read Alice's decision log (404).
3. Concurrent multi-tenant progression: Alice and Bob advance tasks independently;
   checkpoint snapshots are stored in isolated tenant spaces.
4. Concurrency & idempotency: replay of same idempotency key returns 200 OK;
   mismatched payload with same key returns 409 Conflict.
5. Optimistic concurrency defense: concurrent conflicting decisions fail with 409.
6. Checkpoint recovery & integrity: checkpoint snapshots are safely verified.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from formaai.config import Settings
from formaai.infrastructure.storage import MockStorageService
from formaai.modules.identity.dependencies import (
    get_db,
    get_session_manager,
    get_settings,
    get_storage_service,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.tokens import create_token_pair
from formaai.modules.library import CategoryDTO
from formaai.modules.library.models import Category
from formaai.modules.production.models import VideoDecision, VideoTask
from formaai.modules.production.router import get_category_verifier, task_router


class MockSessionManager:
    """In-memory session manager for testing token and session liveness."""

    def __init__(self) -> None:
        self.active_sessions: dict[tuple[str, int, int], bool] = {}

    def add_session(self, session_id: str, user_id: int, version: int) -> None:
        self.active_sessions[(session_id, user_id, version)] = True

    def revoke_session(self, session_id: str) -> None:
        for k in list(self.active_sessions.keys()):
            if k[0] == session_id:
                del self.active_sessions[k]

    def verify_session(self, session_id: str, user_id: int, version: int) -> bool:
        return self.active_sessions.get((session_id, user_id, version), False)


class MultiTenantDecisionDbSession:
    """In-memory DB session mimicking MySQL for VideoTask, VideoDecision."""

    def __init__(
        self,
        users: list[UserAccount] | None = None,
        categories: list[Category] | None = None,
        tasks: list[VideoTask] | None = None,
        decisions: list[VideoDecision] | None = None,
    ) -> None:
        self.users: list[UserAccount] = users if users is not None else []
        self.categories: list[Category] = categories if categories is not None else []
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.decisions: list[VideoDecision] = decisions if decisions is not None else []
        self._next_task_id = max([t.id for t in self.tasks], default=0) + 1
        self._next_dec_id = max([d.id for d in self.decisions], default=0) + 1
        self._pending: list[Any] = []

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )

        user_id = None
        target_id = None
        task_id = None
        idempotency_key = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "user_id":
                    user_id = val
                elif col == "id":
                    target_id = val
                elif col == "task_id":
                    task_id = val
                elif col == "idempotency_key":
                    idempotency_key = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is UserAccount:
            for u in self.users:
                if target_id is not None and u.id != target_id:
                    continue
                return u
            return None

        if entity is Category:
            for c in self.categories:
                if target_id is not None and c.id != target_id:
                    continue
                if user_id is not None and c.user_id != user_id:
                    continue
                return c
            return None

        if entity is VideoDecision:
            for d in self.decisions:
                if task_id is not None and d.task_id != task_id:
                    continue
                if idempotency_key is not None and d.idempotency_key != idempotency_key:
                    continue
                return d
            return None

        # Default entity is VideoTask
        for t in self.tasks:
            if target_id is not None and t.id != target_id:
                continue
            if user_id is not None and t.user_id != user_id:
                continue
            return t
        return None

    def scalars(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        clauses = (
            (whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause])
            if whereclause is not None
            else []
        )

        task_id = None
        user_id = None
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "task_id":
                    task_id = val
                elif col == "user_id":
                    user_id = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is VideoDecision:
            filtered_dec = [
                d for d in self.decisions if task_id is None or d.task_id == task_id
            ]
            filtered_dec.sort(key=lambda d: (d.created_at, d.id))

            class DecScalarsResult:
                def __init__(self, items: list[VideoDecision]) -> None:
                    self._items = items

                def all(self) -> list[VideoDecision]:
                    return self._items

            return DecScalarsResult(filtered_dec)

        filtered_tasks = [
            t for t in self.tasks if user_id is None or t.user_id == user_id
        ]
        filtered_tasks.sort(key=lambda t: (t.created_at, t.id), reverse=True)

        class TaskScalarsResult:
            def __init__(self, items: list[VideoTask]) -> None:
                self._items = items

            def all(self) -> list[VideoTask]:
                return self._items

        return TaskScalarsResult(filtered_tasks)

    def add(self, instance: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(instance, VideoDecision):
            if instance.id is None:
                instance.id = self._next_dec_id
                self._next_dec_id += 1
            if instance.created_at is None:
                instance.created_at = now
            self._pending.append(instance)
        elif isinstance(instance, VideoTask):
            if instance.id is None:
                instance.id = self._next_task_id
                self._next_task_id += 1
            if instance.version is None:
                instance.version = 0
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
            self._pending.append(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, VideoDecision) and item not in self.decisions:
                self.decisions.append(item)
            elif isinstance(item, VideoTask) and item not in self.tasks:
                self.tasks.append(item)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        mysql_database="formaai_e2e_decision",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("e2e-decision-test-access-sec-32b!"),
        jwt_refresh_secret=SecretStr("e2e-decision-test-refresh-sec-32b!"),
        jwt_issuer="https://auth.formaai.test",
        jwt_audience="https://api.formaai.test",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=86400,
    )


@pytest.fixture
def shared_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def session_manager() -> MockSessionManager:
    return MockSessionManager()


@pytest.fixture
def e2e_decision_env(
    test_settings: Settings,
    session_manager: MockSessionManager,
    shared_storage: MockStorageService,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    alice = UserAccount(
        id=101, email="alice@formaai.test", role="user", status="active", version=0
    )
    bob = UserAccount(
        id=102, email="bob@formaai.test", role="user", status="active", version=0
    )

    # Categories
    cat_alice = Category(
        id=1,
        user_id=101,
        content_type="brand",
        name="Alice Brand",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    cat_bob = Category(
        id=2,
        user_id=102,
        content_type="commerce",
        name="Bob Commerce",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )

    # Alice's Task 1 (in created stage, version 0)
    task_alice = VideoTask(
        id=1,
        user_id=101,
        category_id=1,
        name="Alice Brand Campaign",
        content_type="brand",
        stage="created",
        status="pending",
        input_key="library/101/input_v1.json",
        checkpoint_key=None,
        version=0,
        created_at=now,
        updated_at=now,
    )

    # Bob's Task 2 (in created stage, version 0)
    task_bob = VideoTask(
        id=2,
        user_id=102,
        category_id=2,
        name="Bob Product Reel",
        content_type="commerce",
        stage="created",
        status="pending",
        input_key="library/102/input_v1.json",
        checkpoint_key=None,
        version=0,
        created_at=now,
        updated_at=now,
    )

    db_session = MultiTenantDecisionDbSession(
        users=[alice, bob],
        categories=[cat_alice, cat_bob],
        tasks=[task_alice, task_bob],
    )

    def e2e_cat_verifier(db: Any, category_id: int, user_id: int) -> CategoryDTO | None:
        for c in db_session.categories:
            if c.id == category_id and c.user_id == user_id:
                return CategoryDTO(
                    id=c.id,
                    user_id=c.user_id,
                    content_type=c.content_type,
                    name=c.name,
                    sort_order=c.sort_order,
                )
        return None

    app = FastAPI()
    app.include_router(task_router)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage_service] = lambda: shared_storage
    app.dependency_overrides[get_category_verifier] = lambda: e2e_cat_verifier

    return {
        "app": app,
        "settings": test_settings,
        "session_manager": session_manager,
        "storage": shared_storage,
        "db": db_session,
        "users": {"alice": alice, "bob": bob},
    }


def issue_auth_header(
    user: UserAccount,
    settings: Settings,
    session_manager: MockSessionManager,
    sid: str = "e2e-session",
) -> dict[str, str]:
    pair = create_token_pair(
        user_id=user.id,
        version=user.version,
        settings=settings,
        session_id=sid,
    )
    session_manager.add_session(pair.session_id, user.id, user.version)
    return {"Authorization": f"Bearer {pair.access_token}"}


# ==============================================================================
# 1. Cross-Tenant IDOR Attack Defense
# ==============================================================================


def test_e2e_cross_tenant_decision_idor_rejected(
    e2e_decision_env: dict[str, Any],
) -> None:
    """Bob cannot submit decisions or view decision audit history for Alice's Task 1."""
    app = e2e_decision_env["app"]
    settings: Settings = e2e_decision_env["settings"]
    sm: MockSessionManager = e2e_decision_env["session_manager"]
    bob: UserAccount = e2e_decision_env["users"]["bob"]

    h_bob = issue_auth_header(bob, settings, sm, sid="bob-idor-s1")

    with TestClient(app) as client:
        # 1. Bob attempts to submit decision for Alice's Task 1 -> 404 Not Found
        res_submit = client.post(
            "/api/tasks/1/decisions",
            json={
                "idempotency_key": "bob_hack_token_123",
                "expected_version": 0,
                "action": "lock_spec",
                "target_key": "tasks/101/1/script.json",
                "details": {
                    "selected_script_index": 0,
                    "spec": {
                        "aspect_ratio": "16:9",
                        "resolution": "1080p",
                        "target_duration_sec": 30,
                    },
                },
            },
            headers=h_bob,
        )
        assert res_submit.status_code == status.HTTP_404_NOT_FOUND
        assert res_submit.json()["detail"] == "Task not found"

        # 2. Bob attempts to list decisions for Alice's Task 1 -> 404 Not Found
        res_list = client.get("/api/tasks/1/decisions", headers=h_bob)
        assert res_list.status_code == status.HTTP_404_NOT_FOUND
        assert res_list.json()["detail"] == "Task not found"


# ==============================================================================
# 2. Concurrent Multi-Tenant Progression & Checkpoint Isolation
# ==============================================================================


def test_e2e_concurrent_multi_tenant_decision_progression(
    e2e_decision_env: dict[str, Any],
) -> None:
    """Alice and Bob advance tasks concurrently; checkpoints remain isolated."""
    app = e2e_decision_env["app"]
    settings: Settings = e2e_decision_env["settings"]
    sm: MockSessionManager = e2e_decision_env["session_manager"]
    storage: MockStorageService = e2e_decision_env["storage"]
    alice: UserAccount = e2e_decision_env["users"]["alice"]
    bob: UserAccount = e2e_decision_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-prog-s1")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-prog-s1")

    with TestClient(app) as client:
        # 1. Alice locks 16:9 spec on Task 1
        res_alice = client.post(
            "/api/tasks/1/decisions",
            json={
                "idempotency_key": "alice_idemp_lock_01",
                "expected_version": 0,
                "action": "lock_spec",
                "target_key": "tasks/101/1/script_v1.json",
                "details": {
                    "selected_script_index": 0,
                    "spec": {
                        "aspect_ratio": "16:9",
                        "resolution": "1080p",
                        "target_duration_sec": 45,
                    },
                },
            },
            headers=h_alice,
        )
        assert res_alice.status_code == status.HTTP_201_CREATED

        # 2. Bob locks 9:16 vertical spec on Task 2
        res_bob = client.post(
            "/api/tasks/2/decisions",
            json={
                "idempotency_key": "bob_idemp_lock_01",
                "expected_version": 0,
                "action": "lock_spec",
                "target_key": "tasks/102/2/script_v1.json",
                "details": {
                    "selected_script_index": 1,
                    "spec": {
                        "aspect_ratio": "9:16",
                        "resolution": "1080p",
                        "target_duration_sec": 15,
                    },
                },
            },
            headers=h_bob,
        )
        assert res_bob.status_code == status.HTTP_201_CREATED

        # 3. Verify task 1 & 2 details and checkpoint keys in storage
        task_1 = client.get("/api/tasks/1", headers=h_alice).json()
        assert task_1["stage"] == "script_confirmed"
        assert task_1["version"] == 1
        assert task_1["checkpoint_key"].startswith("library/101/")

        task_2 = client.get("/api/tasks/2", headers=h_bob).json()
        assert task_2["stage"] == "script_confirmed"
        assert task_2["version"] == 1
        assert task_2["checkpoint_key"].startswith("library/102/")

        # 4. Verify checkpoint file content integrity
        ckpt_a = json.loads(storage.get_file(task_1["checkpoint_key"]).decode("utf-8"))
        assert ckpt_a["details"]["spec"]["aspect_ratio"] == "16:9"

        ckpt_b = json.loads(storage.get_file(task_2["checkpoint_key"]).decode("utf-8"))
        assert ckpt_b["details"]["spec"]["aspect_ratio"] == "9:16"

        # 5. Verify isolated decision logs
        log_a = client.get("/api/tasks/1/decisions", headers=h_alice).json()
        assert log_a["total"] == 1
        assert log_a["items"][0]["action"] == "lock_spec"

        log_b = client.get("/api/tasks/2/decisions", headers=h_bob).json()
        assert log_b["total"] == 1
        assert log_b["items"][0]["action"] == "lock_spec"


# ==============================================================================
# 3. Idempotency Token Replay & Mismatch Defense
# ==============================================================================


def test_e2e_idempotency_token_replay_and_conflict(
    e2e_decision_env: dict[str, Any],
) -> None:
    """Repeated calls with same key return 200 OK; modified payload returns 409."""
    app = e2e_decision_env["app"]
    settings: Settings = e2e_decision_env["settings"]
    sm: MockSessionManager = e2e_decision_env["session_manager"]
    alice: UserAccount = e2e_decision_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-idemp-s1")

    payload = {
        "idempotency_key": "alice_idem_tok_9999",
        "expected_version": 0,
        "action": "lock_spec",
        "target_key": "tasks/101/1/script.json",
        "details": {
            "selected_script_index": 0,
            "spec": {
                "aspect_ratio": "16:9",
                "resolution": "1080p",
                "target_duration_sec": 30,
            },
        },
    }

    with TestClient(app) as client:
        # First call -> 201 Created
        res1 = client.post("/api/tasks/1/decisions", json=payload, headers=h_alice)
        assert res1.status_code == status.HTTP_201_CREATED
        first_id = res1.json()["id"]

        # Exact repeat -> 200 OK (idempotent replay)
        res2 = client.post("/api/tasks/1/decisions", json=payload, headers=h_alice)
        assert res2.status_code == status.HTTP_200_OK
        assert res2.json()["id"] == first_id

        # Modified payload with same idempotency token -> 409 Conflict
        conflicting_payload = {
            **payload,
            "expected_version": 1,
            "details": {
                "selected_script_index": 2,  # Different script!
                "spec": {
                    "aspect_ratio": "1:1",
                    "resolution": "720p",
                    "target_duration_sec": 10,
                },
            },
        }
        res3 = client.post(
            "/api/tasks/1/decisions", json=conflicting_payload, headers=h_alice
        )
        assert res3.status_code == status.HTTP_409_CONFLICT
        assert "already used with different decision payload" in res3.json()["detail"]


# ==============================================================================
# 4. Optimistic Concurrency Control (Stale Version Defense)
# ==============================================================================


def test_e2e_optimistic_concurrency_race_condition(
    e2e_decision_env: dict[str, Any],
) -> None:
    """Client A advances task to v1; Client B with v0 is rejected (409)."""
    app = e2e_decision_env["app"]
    settings: Settings = e2e_decision_env["settings"]
    sm: MockSessionManager = e2e_decision_env["session_manager"]
    alice: UserAccount = e2e_decision_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-race-s1")

    with TestClient(app) as client:
        # Client A locks spec with expected_version=0
        res_a = client.post(
            "/api/tasks/1/decisions",
            json={
                "idempotency_key": "client_a_token_111",
                "expected_version": 0,
                "action": "lock_spec",
                "target_key": "tasks/101/1/script.json",
                "details": {
                    "selected_script_index": 0,
                    "spec": {
                        "aspect_ratio": "16:9",
                        "resolution": "1080p",
                        "target_duration_sec": 30,
                    },
                },
            },
            headers=h_alice,
        )
        assert res_a.status_code == status.HTTP_201_CREATED

        # Client B (stale tab) tries to submit decision with expected_version=0
        res_b = client.post(
            "/api/tasks/1/decisions",
            json={
                "idempotency_key": "client_b_token_222",
                "expected_version": 0,
                "action": "confirm_storyboard",
                "target_key": "tasks/101/1/storyboard.json",
                "details": {},
            },
            headers=h_alice,
        )
        assert res_b.status_code == status.HTTP_409_CONFLICT
        assert "version conflict" in res_b.json()["detail"]
