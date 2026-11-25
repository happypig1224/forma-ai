"""Unit tests for usage recording and aggregation RESTful routes."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from formaai.modules.identity.dependencies import get_current_user, get_db
from formaai.modules.identity.models import UserAccount
from formaai.modules.production.models import UsageRecord, VideoAttempt, VideoTask
from formaai.modules.production.router import task_router


class MockUsageDbSession:
    """Mock DB session for testing usage recording and aggregation."""

    def __init__(
        self,
        tasks: list[VideoTask] | None = None,
        attempts: list[VideoAttempt] | None = None,
        usages: list[UsageRecord] | None = None,
    ) -> None:
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.attempts: list[VideoAttempt] = attempts if attempts is not None else []
        self.usages: list[UsageRecord] = usages if usages is not None else []
        self._next_usage_id = 100

    def add(self, instance: Any) -> None:
        if isinstance(instance, UsageRecord):
            if instance.id is None:
                instance.id = self._next_usage_id
                self._next_usage_id += 1
            if instance.recorded_at is None:
                instance.recorded_at = datetime.now(UTC)
            self.usages.append(instance)

    def commit(self) -> None:
        pass

    def refresh(self, instance: Any) -> None:
        pass

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        clauses = (
            (whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause])
            if whereclause is not None
            else []
        )

        target_id = None
        user_id = None
        task_id = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "id":
                    target_id = val
                elif col == "user_id":
                    user_id = val
                elif col == "task_id":
                    task_id = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is VideoAttempt:
            for a in self.attempts:
                if target_id is not None and a.id != target_id:
                    continue
                if task_id is not None and a.task_id != task_id:
                    continue
                return a
            return None

        # Default entity: VideoTask
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
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "task_id":
                    task_id = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is UsageRecord:
            filtered = [
                u for u in self.usages if task_id is None or u.task_id == task_id
            ]
            filtered.sort(key=lambda u: (u.recorded_at, u.id))

            class UsageScalarsResult:
                def __init__(self, items: list[UsageRecord]) -> None:
                    self._items = items

                def all(self) -> list[UsageRecord]:
                    return self._items

            return UsageScalarsResult(filtered)

        return None


@pytest.fixture
def test_user() -> UserAccount:
    user = UserAccount(email="director@formaai.test", role="admin")
    user.id = 101
    user.account_status = "active"
    user.token_version = 0
    return user


@pytest.fixture
def other_user() -> UserAccount:
    user = UserAccount(email="other@formaai.test", role="member")
    user.id = 202
    user.account_status = "active"
    user.token_version = 0
    return user


def create_env(
    user: UserAccount,
    tasks: list[VideoTask] | None = None,
    attempts: list[VideoAttempt] | None = None,
    usages: list[UsageRecord] | None = None,
) -> tuple[TestClient, MockUsageDbSession]:
    db = MockUsageDbSession(tasks=tasks, attempts=attempts, usages=usages)
    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


def test_record_task_usage_success_and_multi_tenant_404(
    test_user: UserAccount,
    other_user: UserAccount,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Billing Task",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    payload = {
        "metric_type": "gpu_second",
        "quantity": 25.5,
        "cost_cents": 120,
    }

    # 1. Owner records task-level usage -> 201 Created
    client, db = create_env(test_user, tasks=[task])
    res = client.post("/api/tasks/1/usages", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["task_id"] == 1
    assert data["user_id"] == test_user.id
    assert data["metric_type"] == "gpu_second"
    assert data["quantity"] == 25.5
    assert data["cost_cents"] == 120

    # 2. Non-owner gets 404
    client_other, _ = create_env(other_user, tasks=[task])
    res_other = client_other.post("/api/tasks/1/usages", json=payload)
    assert res_other.status_code == status.HTTP_404_NOT_FOUND


def test_record_task_usage_with_attempt_validation(
    test_user: UserAccount,
) -> None:
    now = datetime.now(UTC)
    task1 = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Task 1",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task1.id = 1

    # Attempt 10 belongs to Task 1
    att10 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/att_1.json",
        created_at=now,
    )
    att10.id = 10

    # Attempt 20 belongs to another task (Task 2)
    att20 = VideoAttempt(
        task_id=2,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/2/att_1.json",
        created_at=now,
    )
    att20.id = 20

    client, _ = create_env(test_user, tasks=[task1], attempts=[att10, att20])

    # 1. Valid attempt on task -> 201 Created
    res1 = client.post(
        "/api/tasks/1/usages",
        json={
            "attempt_id": 10,
            "metric_type": "storage_byte",
            "quantity": 1048576.0,
            "cost_cents": 10,
        },
    )
    assert res1.status_code == status.HTTP_201_CREATED
    assert res1.json()["attempt_id"] == 10

    # 2. Foreign attempt belonging to Task 2 -> 404 Not Found
    res2 = client.post(
        "/api/tasks/1/usages",
        json={
            "attempt_id": 20,
            "metric_type": "storage_byte",
            "quantity": 1048576.0,
            "cost_cents": 10,
        },
    )
    assert res2.status_code == status.HTTP_404_NOT_FOUND
    assert "Attempt not found for this task" in res2.json()["detail"]


def test_list_task_usages_aggregation_and_ordering(
    test_user: UserAccount,
    other_user: UserAccount,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Audit Task",
        content_type="story",
        stage="finished",
        status="success",
        input_key="tasks/101/1/input.json",
        version=2,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    u1 = UsageRecord(
        user_id=test_user.id,
        task_id=1,
        attempt_id=10,
        metric_type="gpu_second",
        quantity=30.0,
        cost_cents=100,
        recorded_at=now,
    )
    u1.id = 1

    u2 = UsageRecord(
        user_id=test_user.id,
        task_id=1,
        attempt_id=11,
        metric_type="gpu_second",
        quantity=20.0,
        cost_cents=80,
        recorded_at=now,
    )
    u2.id = 2

    u3 = UsageRecord(
        user_id=test_user.id,
        task_id=1,
        attempt_id=None,
        metric_type="api_call",
        quantity=5.0,
        cost_cents=25,
        recorded_at=now,
    )
    u3.id = 3

    u4 = UsageRecord(
        user_id=test_user.id,
        task_id=1,
        attempt_id=None,
        metric_type="storage_byte",
        quantity=5242880.0,
        cost_cents=15,
        recorded_at=now,
    )
    u4.id = 4

    client, _ = create_env(test_user, tasks=[task], usages=[u2, u1, u4, u3])

    res = client.get("/api/tasks/1/usages")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 4
    assert len(data["items"]) == 4

    # Verify aggregated totals
    summary = data["summary"]
    assert summary["total_gpu_seconds"] == 50.0  # 30.0 + 20.0
    assert summary["total_api_calls"] == 5.0
    assert summary["total_storage_bytes"] == 5242880.0
    assert summary["total_cost_cents"] == 220  # 100 + 80 + 25 + 15

    # Non-owner gets 404
    client_other, _ = create_env(other_user, tasks=[task], usages=[u1])
    res_other = client_other.get("/api/tasks/1/usages")
    assert res_other.status_code == status.HTTP_404_NOT_FOUND
