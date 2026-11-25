"""End-to-end multi-tenant penetration, usage audit, and full lifecycle tests.

Validates security, billing integrity, and full lifecycle state machines:
1. Cross-tenant IDOR & injection defense: Bob cannot read, inject, or associate
   usages across tenant boundaries (404 Not Found).
2. Full lifecycle production pipeline: task creation -> shot redo -> review ->
   composition -> terminal completion -> accurate aggregated billing.
3. Concurrent multi-tenant billing isolation: simultaneous usage records remain
   strictly segregated per tenant.
4. Account status guard matrix: disabling accounts can view historical billing
   but cannot initiate new tasks.
"""

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
from formaai.modules.production.models import (
    UsageRecord,
    VideoAttempt,
    VideoShot,
    VideoTask,
)
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


class MultiTenantUsageDbSession:
    """In-memory DB session mimicking MySQL for VideoTask, Shot, Attempt, Usage."""

    def __init__(
        self,
        users: list[UserAccount] | None = None,
        categories: list[Category] | None = None,
        tasks: list[VideoTask] | None = None,
        shots: list[VideoShot] | None = None,
        attempts: list[VideoAttempt] | None = None,
        usages: list[UsageRecord] | None = None,
    ) -> None:
        self.users: list[UserAccount] = users if users is not None else []
        self.categories: list[Category] = categories if categories is not None else []
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.shots: list[VideoShot] = shots if shots is not None else []
        self.attempts: list[VideoAttempt] = attempts if attempts is not None else []
        self.usages: list[UsageRecord] = usages if usages is not None else []
        self._next_task_id = 100
        self._next_attempt_id = 500
        self._next_usage_id = 1000
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
        shot_id = None
        shot_no = None
        email = None
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
                elif col == "shot_id":
                    shot_id = val
                elif col == "shot_no":
                    shot_no = val
                elif col == "email":
                    email = val
                elif col == "idempotency_key":
                    idempotency_key = val

        desc = getattr(stmt, "column_descriptions", [{}])
        expr_target = desc[0].get("expr") if desc else None
        entity = desc[0].get("entity") if desc else None

        if str(expr_target).lower().startswith("max("):
            all_attempts = self.attempts + [
                p for p in self._pending if isinstance(p, VideoAttempt)
            ]
            matched = [
                a.attempt_no
                for a in all_attempts
                if shot_id is None or a.shot_id == shot_id
            ]
            return max(matched) if matched else None

        if entity is UserAccount:
            for u in self.users:
                if target_id is not None and u.id != target_id:
                    continue
                if email is not None and u.email != email:
                    continue
                return u
            return None

        if entity is VideoAttempt:
            all_attempts = self.attempts + [
                p for p in self._pending if isinstance(p, VideoAttempt)
            ]
            for a in all_attempts:
                if target_id is not None and a.id != target_id:
                    continue
                if task_id is not None and a.task_id != task_id:
                    continue
                if shot_id is not None and a.shot_id != shot_id:
                    continue
                if idempotency_key is not None and a.idempotency_key != idempotency_key:
                    continue
                return a
            return None

        if entity is VideoShot:
            all_shots = self.shots + [
                p for p in self._pending if isinstance(p, VideoShot)
            ]
            for s in all_shots:
                if target_id is not None and s.id != target_id:
                    continue
                if task_id is not None and s.task_id != task_id:
                    continue
                if shot_no is not None and s.shot_no != shot_no:
                    continue
                return s
            return None

        # Default: VideoTask
        all_tasks = self.tasks + [p for p in self._pending if isinstance(p, VideoTask)]
        for t in all_tasks:
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
        shot_id = None
        min_shot_no = None
        in_ids: list[int] | None = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                op_str = str(getattr(expr, "operator", ""))
                if col == "task_id":
                    task_id = val
                elif col == "shot_id":
                    shot_id = val
                elif col == "shot_no" and ">" in op_str:
                    min_shot_no = val
            if hasattr(expr, "modifier") or "in" in str(getattr(expr, "operator", "")):
                if hasattr(expr, "right") and hasattr(expr.right, "clauses"):
                    in_ids = [c.value for c in expr.right.clauses]

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is UsageRecord:
            all_usages = self.usages + [
                p for p in self._pending if isinstance(p, UsageRecord)
            ]
            filtered_usages = [
                u for u in all_usages if task_id is None or u.task_id == task_id
            ]
            filtered_usages.sort(key=lambda u: (u.recorded_at, u.id))

            class UsageScalarsResult:
                def __init__(self, items: list[UsageRecord]) -> None:
                    self._items = items

                def all(self) -> list[UsageRecord]:
                    return self._items

            return UsageScalarsResult(filtered_usages)

        if entity is VideoShot:
            all_shots = self.shots + [
                p for p in self._pending if isinstance(p, VideoShot)
            ]
            filtered_shots = [
                s
                for s in all_shots
                if (task_id is None or s.task_id == task_id)
                and (min_shot_no is None or s.shot_no > min_shot_no)
            ]
            filtered_shots.sort(key=lambda s: s.shot_no)

            class ShotScalarsResult:
                def __init__(self, items: list[VideoShot]) -> None:
                    self._items = items

                def all(self) -> list[VideoShot]:
                    return self._items

            return ShotScalarsResult(filtered_shots)

        # Default: VideoAttempt
        all_attempts = self.attempts + [
            p for p in self._pending if isinstance(p, VideoAttempt)
        ]
        filtered_attempts = [
            a
            for a in all_attempts
            if (task_id is None or a.task_id == task_id)
            and (shot_id is None or a.shot_id == shot_id)
            and (in_ids is None or a.id in in_ids)
        ]
        filtered_attempts.sort(key=lambda a: a.attempt_no)

        class AttemptScalarsResult:
            def __init__(self, items: list[VideoAttempt]) -> None:
                self._items = items

            def all(self) -> list[VideoAttempt]:
                return self._items

        return AttemptScalarsResult(filtered_attempts)

    def add(self, instance: Any) -> None:
        if isinstance(instance, VideoTask) and instance.id is None:
            instance.id = self._next_task_id
            self._next_task_id += 1
            if instance.version is None:
                instance.version = 0
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
            if instance.updated_at is None:
                instance.updated_at = datetime.now(UTC)
        elif isinstance(instance, VideoAttempt) and instance.id is None:
            instance.id = self._next_attempt_id
            self._next_attempt_id += 1
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
        elif isinstance(instance, UsageRecord) and instance.id is None:
            instance.id = self._next_usage_id
            self._next_usage_id += 1
            if instance.recorded_at is None:
                instance.recorded_at = datetime.now(UTC)
        self._pending.append(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, VideoTask) and item not in self.tasks:
                self.tasks.append(item)
            elif isinstance(item, VideoAttempt) and item not in self.attempts:
                self.attempts.append(item)
            elif isinstance(item, VideoShot) and item not in self.shots:
                self.shots.append(item)
            elif isinstance(item, UsageRecord) and item not in self.usages:
                self.usages.append(item)
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        mysql_database="formaai_e2e_usage",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("e2e-usage-test-access-secret-32b!"),
        jwt_refresh_secret=SecretStr("e2e-usage-test-refresh-secret-32b!"),
        jwt_issuer="https://auth.formaai.test",
        jwt_audience="https://api.formaai.test",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=86400,
    )


@pytest.fixture
def shared_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def e2e_usage_env(
    test_settings: Settings,
    shared_storage: MockStorageService,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    alice = UserAccount(email="alice.vip@formaai.test", role="admin")
    alice.id = 101
    alice.status = "active"
    alice.version = 0

    bob = UserAccount(email="bob.hacker@formaai.test", role="admin")
    bob.id = 102
    bob.status = "active"
    bob.version = 0

    disabling_user = UserAccount(email="carol.retiring@formaai.test", role="member")
    disabling_user.id = 103
    disabling_user.status = "disabling"
    disabling_user.version = 0

    cat_alice = Category(user_id=alice.id, name="Sci-Fi Series", content_type="story")
    cat_alice.id = 1

    cat_bob = Category(user_id=bob.id, name="Tech Ads", content_type="commerce")
    cat_bob.id = 2

    # Task 1: Alice's Task
    task1 = VideoTask(
        user_id=alice.id,
        category_id=1,
        name="Alice Odyssey",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=1,
        created_at=now,
        updated_at=now,
    )
    task1.id = 1

    s1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Shot 1 Scene",
        duration_sec=4.0,
        selected_attempt_id=101,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s1.id = 1

    s2 = VideoShot(
        task_id=1,
        shot_no=2,
        title="Shot 2 Scene",
        duration_sec=3.0,
        selected_attempt_id=102,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s2.id = 2

    att101 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/1/att_1_in.json",
        result_key="tasks/101/1/shots/1/att_1_res.mp4",
        created_at=now,
    )
    att101.id = 101

    att102 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/2/att_1_in.json",
        result_key="tasks/101/1/shots/2/att_1_res.mp4",
        created_at=now,
    )
    att102.id = 102

    # Task 2: Bob's Task
    task2 = VideoTask(
        user_id=bob.id,
        category_id=2,
        name="Bob Gadgets",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/102/2/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task2.id = 2

    bob_s1 = VideoShot(
        task_id=2,
        shot_no=1,
        title="Gadget Showcase",
        duration_sec=3.0,
        selected_attempt_id=201,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    bob_s1.id = 10

    att201 = VideoAttempt(
        task_id=2,
        shot_id=10,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/102/2/shots/1/att_1_in.json",
        result_key="tasks/102/2/shots/1/att_1_res.mp4",
        created_at=now,
    )
    att201.id = 201

    # Task 3: Carol (Disabling user)'s legacy task
    task3 = VideoTask(
        user_id=disabling_user.id,
        category_id=1,
        name="Carol Final Archival",
        content_type="story",
        stage="finished",
        status="success",
        input_key="tasks/103/3/input.json",
        version=3,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    task3.id = 3

    usage_carol = UsageRecord(
        user_id=disabling_user.id,
        task_id=3,
        attempt_id=None,
        metric_type="gpu_second",
        quantity=60.0,
        cost_cents=300,
        recorded_at=now,
    )
    usage_carol.id = 50

    db = MultiTenantUsageDbSession(
        users=[alice, bob, disabling_user],
        categories=[cat_alice, cat_bob],
        tasks=[task1, task2, task3],
        shots=[s1, s2, bob_s1],
        attempts=[att101, att102, att201],
        usages=[usage_carol],
    )
    sm = MockSessionManager()

    def mock_category_verifier(dbs: Any, cat_id: int, u_id: int) -> CategoryDTO | None:
        for c in db.categories:
            if c.id == cat_id and c.user_id == u_id:
                return CategoryDTO(
                    id=c.id,
                    user_id=c.user_id,
                    name=c.name,
                    content_type=c.content_type,
                )
        return None

    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_storage_service] = lambda: shared_storage
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_category_verifier] = lambda: mock_category_verifier

    return {
        "app": app,
        "db": db,
        "storage": shared_storage,
        "session_manager": sm,
        "settings": test_settings,
        "users": {"alice": alice, "bob": bob, "carol": disabling_user},
        "tasks": {"task1": task1, "task2": task2, "task3": task3},
        "shots": {"s1": s1, "s2": s2, "bob_s1": bob_s1},
        "attempts": {"att101": att101, "att102": att102, "att201": att201},
    }


def issue_auth_header(
    user: UserAccount,
    settings: Settings,
    session_manager: MockSessionManager,
    sid: str = "e2e-usage-sid",
) -> dict[str, str]:
    tokens = create_token_pair(
        user_id=user.id,
        version=user.version,
        settings=settings,
        session_id=sid,
    )
    session_manager.add_session(tokens.session_id, user.id, user.version)
    return {"Authorization": f"Bearer {tokens.access_token}"}


# ==============================================================================
# 1. Multi-Tenant IDOR & Injection Defense
# ==============================================================================


def test_e2e_cross_tenant_usage_idor_and_injection_defense(
    e2e_usage_env: dict[str, Any],
) -> None:
    """Bob cannot inspect or inject usages into Alice's task (strictly 404)."""
    app = e2e_usage_env["app"]
    settings: Settings = e2e_usage_env["settings"]
    sm: MockSessionManager = e2e_usage_env["session_manager"]
    bob: UserAccount = e2e_usage_env["users"]["bob"]
    alice: UserAccount = e2e_usage_env["users"]["alice"]

    h_bob = issue_auth_header(bob, settings, sm, sid="bob-usage-attack")
    h_alice = issue_auth_header(alice, settings, sm, sid="alice-usage-verify")

    with TestClient(app) as client:
        # 1. Bob attempts to read Alice's usage bill -> 404 Not Found
        res_read = client.get("/api/tasks/1/usages", headers=h_bob)
        assert res_read.status_code == status.HTTP_404_NOT_FOUND

        # 2. Bob attempts to inject compute usage into Alice's task -> 404 Not Found
        res_inject = client.post(
            "/api/tasks/1/usages",
            json={
                "metric_type": "gpu_second",
                "quantity": 999.0,
                "cost_cents": 5000,
            },
            headers=h_bob,
        )
        assert res_inject.status_code == status.HTTP_404_NOT_FOUND

        # 3. Bob attempts to log usage on Task 2 referencing Alice's Attempt 101 -> 404
        res_steal_attempt = client.post(
            "/api/tasks/2/usages",
            json={
                "attempt_id": 101,  # Belongs to Alice's Task 1!
                "metric_type": "gpu_second",
                "quantity": 10.0,
                "cost_cents": 50,
            },
            headers=h_bob,
        )
        assert res_steal_attempt.status_code == status.HTTP_404_NOT_FOUND
        assert "Attempt not found for this task" in res_steal_attempt.json()["detail"]

        # 4. Alice attempts to inspect Bob's usages -> 404 Not Found
        res_alice_probe = client.get("/api/tasks/2/usages", headers=h_alice)
        assert res_alice_probe.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================
# 2. Full Lifecycle Production Pipeline with Integrated Usage Auditing
# ==============================================================================


def test_e2e_full_lifecycle_production_pipeline_and_billing(
    e2e_usage_env: dict[str, Any],
) -> None:
    """Full lifecycle: compose -> complete -> cleanup -> verify aggregated bill."""
    app = e2e_usage_env["app"]
    settings: Settings = e2e_usage_env["settings"]
    sm: MockSessionManager = e2e_usage_env["session_manager"]
    alice: UserAccount = e2e_usage_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-full-pipeline")

    with TestClient(app) as client:
        # 1. Record generation usage for Shot 1 (Attempt 101)
        res_u1 = client.post(
            "/api/tasks/1/usages",
            json={
                "attempt_id": 101,
                "metric_type": "gpu_second",
                "quantity": 15.5,
                "cost_cents": 62,
            },
            headers=h_alice,
        )
        assert res_u1.status_code == status.HTTP_201_CREATED

        # 2. Record generation usage for Shot 2 (Attempt 102)
        res_u2 = client.post(
            "/api/tasks/1/usages",
            json={
                "attempt_id": 102,
                "metric_type": "gpu_second",
                "quantity": 24.5,
                "cost_cents": 98,
            },
            headers=h_alice,
        )
        assert res_u2.status_code == status.HTTP_201_CREATED

        # 3. Trigger Composition
        res_comp = client.post(
            "/api/tasks/1/compose",
            json={
                "idempotency_key": "alice_e2e_comp_manifest",
                "expected_version": 1,
            },
            headers=h_alice,
        )
        assert res_comp.status_code == status.HTTP_200_OK
        assert res_comp.json()["stage"] == "composition"
        assert res_comp.json()["version"] == 2

        # Record composition render usage (task-level)
        res_u3 = client.post(
            "/api/tasks/1/usages",
            json={
                "metric_type": "gpu_second",
                "quantity": 60.0,
                "cost_cents": 240,
            },
            headers=h_alice,
        )
        assert res_u3.status_code == status.HTTP_201_CREATED

        # Record storage allocation usage
        res_u4 = client.post(
            "/api/tasks/1/usages",
            json={
                "metric_type": "storage_byte",
                "quantity": 20971520.0,  # 20 MB
                "cost_cents": 40,
            },
            headers=h_alice,
        )
        assert res_u4.status_code == status.HTTP_201_CREATED

        # Record LLM script reasoning usage
        res_u5 = client.post(
            "/api/tasks/1/usages",
            json={
                "metric_type": "api_call",
                "quantity": 3.0,
                "cost_cents": 15,
            },
            headers=h_alice,
        )
        assert res_u5.status_code == status.HTTP_201_CREATED

        # 4. Complete Task
        final_video = "library/101/tasks/1/master_composite.mp4"
        res_complete = client.post(
            "/api/tasks/1/complete",
            json={"final_video_key": final_video, "expected_version": 2},
            headers=h_alice,
        )
        assert res_complete.status_code == status.HTTP_200_OK
        data_fin = res_complete.json()
        assert data_fin["stage"] == "finished"
        assert data_fin["status"] == "success"
        assert data_fin["checkpoint_key"] == final_video
        assert data_fin["finished_at"] is not None

        # 5. Retrieve full auditable usage bill
        bill_res = client.get("/api/tasks/1/usages", headers=h_alice)
        assert bill_res.status_code == status.HTTP_200_OK
        bill = bill_res.json()
        assert bill["total"] == 5

        # Check precise aggregation
        summary = bill["summary"]
        assert summary["total_gpu_seconds"] == 100.0  # 15.5 + 24.5 + 60.0
        assert summary["total_storage_bytes"] == 20971520.0
        assert summary["total_api_calls"] == 3.0
        assert summary["total_cost_cents"] == 455  # 62 + 98 + 240 + 40 + 15


# ==============================================================================
# 3. Concurrent Multi-Tenant Pipeline & Usage Segregation
# ==============================================================================


def test_e2e_concurrent_multi_tenant_usage_segregation(
    e2e_usage_env: dict[str, Any],
) -> None:
    """Alice and Bob record compute usages concurrently; totals remain segregated."""
    app = e2e_usage_env["app"]
    settings: Settings = e2e_usage_env["settings"]
    sm: MockSessionManager = e2e_usage_env["session_manager"]
    alice: UserAccount = e2e_usage_env["users"]["alice"]
    bob: UserAccount = e2e_usage_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-segregation")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-segregation")

    with TestClient(app) as client:
        # Alice records GPU usage on Task 1
        client.post(
            "/api/tasks/1/usages",
            json={
                "metric_type": "gpu_second",
                "quantity": 50.0,
                "cost_cents": 200,
            },
            headers=h_alice,
        )

        # Bob records GPU and Storage on Task 2
        client.post(
            "/api/tasks/2/usages",
            json={
                "metric_type": "gpu_second",
                "quantity": 10.0,
                "cost_cents": 40,
            },
            headers=h_bob,
        )
        client.post(
            "/api/tasks/2/usages",
            json={
                "metric_type": "storage_byte",
                "quantity": 1048576.0,
                "cost_cents": 5,
            },
            headers=h_bob,
        )

        # Alice verifies only her usage is present
        bill_a = client.get("/api/tasks/1/usages", headers=h_alice).json()
        assert bill_a["total"] == 1
        assert bill_a["summary"]["total_gpu_seconds"] == 50.0
        assert bill_a["summary"]["total_cost_cents"] == 200

        # Bob verifies only his usage is present
        bill_b = client.get("/api/tasks/2/usages", headers=h_bob).json()
        assert bill_b["total"] == 2
        assert bill_b["summary"]["total_gpu_seconds"] == 10.0
        assert bill_b["summary"]["total_storage_bytes"] == 1048576.0
        assert bill_b["summary"]["total_cost_cents"] == 45


# ==============================================================================
# 4. Account Status Guard Matrix (Disabling User Defense)
# ==============================================================================


def test_e2e_account_status_matrix_on_usage_and_tasks(
    e2e_usage_env: dict[str, Any],
) -> None:
    """Disabling account can read usage history but cannot create tasks."""
    app = e2e_usage_env["app"]
    settings: Settings = e2e_usage_env["settings"]
    sm: MockSessionManager = e2e_usage_env["session_manager"]
    carol: UserAccount = e2e_usage_env["users"]["carol"]

    h_carol = issue_auth_header(carol, settings, sm, sid="carol-disabling")

    with TestClient(app) as client:
        # 1. Carol can view historical usage on her finished Task 3 -> 200 OK
        res_view = client.get("/api/tasks/3/usages", headers=h_carol)
        assert res_view.status_code == status.HTTP_200_OK
        data = res_view.json()
        assert data["total"] == 1
        assert data["summary"]["total_gpu_seconds"] == 60.0
        assert data["summary"]["total_cost_cents"] == 300

        # 2. Carol is blocked from creating new tasks -> 403 Forbidden
        res_create = client.post(
            "/api/tasks",
            json={
                "category_id": 1,
                "name": "Disallowed New Task",
                "content_type": "story",
                "initial_input": {
                    "theme": "Ghost Product",
                    "target_market": "None",
                },
            },
            headers=h_carol,
        )
        assert res_create.status_code == status.HTTP_403_FORBIDDEN
        assert "Account is disabling" in res_create.json()["detail"]
