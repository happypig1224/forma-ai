"""Unit tests for production task decision routes, idempotency, and checkpoints."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from formaai.infrastructure.storage import MockStorageService
from formaai.modules.identity.dependencies import (
    get_current_user,
    get_db,
    get_storage_service,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.production.models import VideoDecision, VideoTask
from formaai.modules.production.router import task_router


class MockDecisionTaskDbSession:
    """In-memory DB session mimicking MySQL for VideoTask and VideoDecision."""

    def __init__(
        self,
        tasks: list[VideoTask] | None = None,
        decisions: list[VideoDecision] | None = None,
    ) -> None:
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.decisions: list[VideoDecision] = decisions if decisions is not None else []
        self._next_dec_id = max([d.id for d in self.decisions], default=0) + 1
        self._pending: list[Any] = []

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )

        target_id = None
        user_id = None
        task_id = None
        idempotency_key = None

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
                elif col == "idempotency_key":
                    idempotency_key = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

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
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "task_id":
                    task_id = val

        filtered = [
            d for d in self.decisions if task_id is None or d.task_id == task_id
        ]
        filtered.sort(key=lambda d: (d.created_at, d.id))

        class DecisionScalarsResult:
            def __init__(self, items: list[VideoDecision]) -> None:
                self._items = items

            def all(self) -> list[VideoDecision]:
                return self._items

        return DecisionScalarsResult(filtered)

    def add(self, instance: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(instance, VideoDecision):
            if instance.id is None:
                instance.id = self._next_dec_id
                self._next_dec_id += 1
            if instance.created_at is None:
                instance.created_at = now
            self._pending.append(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, VideoDecision) and item not in self.decisions:
                self.decisions.append(item)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def current_user() -> UserAccount:
    user = UserAccount()
    user.id = 101
    user.email = "creator@formaai.test"
    user.role = "user"
    user.status = "active"
    user.version = 0
    return user


@pytest.fixture
def mock_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def base_task() -> VideoTask:
    now = datetime.now(UTC)
    return VideoTask(
        id=1,
        user_id=101,
        category_id=1,
        name="Brand Video",
        content_type="brand",
        stage="created",
        status="pending",
        input_key="library/101/input.json",
        checkpoint_key=None,
        version=0,
        created_at=now,
        updated_at=now,
    )


def create_test_client(
    user: UserAccount,
    db: MockDecisionTaskDbSession,
    storage: MockStorageService,
) -> TestClient:
    app = FastAPI()
    app.include_router(task_router)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage

    return TestClient(app)


def test_submit_decision_lock_spec_advances_stage(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    req_payload = {
        "idempotency_key": "dec_token_20261123_01",
        "expected_version": 0,
        "action": "lock_spec",
        "target_key": "tasks/101/1/script_candidate_1.json",
        "details": {
            "selected_script_index": 0,
            "spec": {
                "aspect_ratio": "16:9",
                "resolution": "1080p",
                "target_duration_sec": 30,
                "fps": 30,
            },
            "notes": "Locked for production",
        },
    }

    res = client.post("/api/tasks/1/decisions", json=req_payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["id"] == 1
    assert data["task_id"] == 1
    assert data["action"] == "lock_spec"
    assert data["target_key"] == "tasks/101/1/script_candidate_1.json"
    assert data["idempotency_key"] == "dec_token_20261123_01"

    # Task stage and checkpoint updated
    assert base_task.stage == "script_confirmed"
    assert base_task.version == 1
    assert base_task.checkpoint_key is not None

    # Checkpoint file stored in storage
    ckpt_raw = mock_storage.get_file(base_task.checkpoint_key)
    assert ckpt_raw is not None
    ckpt_json = json.loads(ckpt_raw.decode("utf-8"))
    assert ckpt_json["stage"] == "script_confirmed"
    assert ckpt_json["details"]["spec"]["aspect_ratio"] == "16:9"


def test_submit_decision_task_not_found(
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    db = MockDecisionTaskDbSession(tasks=[])
    client = create_test_client(current_user, db, mock_storage)

    res = client.post(
        "/api/tasks/999/decisions",
        json={
            "idempotency_key": "dec_token_999",
            "expected_version": 0,
            "action": "lock_spec",
            "target_key": "tasks/101/1/snap.json",
            "details": {},
        },
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Task not found"


def test_submit_decision_for_paused_task_fails(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    base_task.status = "paused"
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    res = client.post(
        "/api/tasks/1/decisions",
        json={
            "idempotency_key": "dec_token_paused",
            "expected_version": 0,
            "action": "lock_spec",
            "target_key": "tasks/101/1/snap.json",
            "details": {},
        },
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot submit decision for paused task" in res.json()["detail"]


def test_submit_decision_for_finished_task_fails(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    base_task.status = "success"
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    res = client.post(
        "/api/tasks/1/decisions",
        json={
            "idempotency_key": "dec_token_finished",
            "expected_version": 0,
            "action": "lock_spec",
            "target_key": "tasks/101/1/snap.json",
            "details": {},
        },
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot submit decision for task in terminal status" in res.json()["detail"]


def test_submit_decision_invalid_stage_action_fails(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    # Task is in "created" stage; attempting "confirm_keyframes" is disallowed
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    res = client.post(
        "/api/tasks/1/decisions",
        json={
            "idempotency_key": "dec_token_invalid_stage",
            "expected_version": 0,
            "action": "confirm_keyframes",
            "target_key": "tasks/101/1/snap.json",
            "details": {},
        },
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "is not permitted for task stage 'created'" in res.json()["detail"]


def test_submit_decision_optimistic_concurrency_conflict(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    base_task.version = 2
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    res = client.post(
        "/api/tasks/1/decisions",
        json={
            "idempotency_key": "dec_token_conflict",
            "expected_version": 0,  # Stale version
            "action": "lock_spec",
            "target_key": "tasks/101/1/snap.json",
            "details": {},
        },
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "version conflict" in res.json()["detail"]


def test_submit_decision_idempotent_replay_success(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    payload = {
        "idempotency_key": "dec_token_replay_test",
        "expected_version": 0,
        "action": "lock_spec",
        "target_key": "tasks/101/1/snap.json",
        "details": {
            "selected_script_index": 1,
            "notes": "Initial lock",
        },
    }

    # First request: 201 Created
    res1 = client.post("/api/tasks/1/decisions", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED
    dec_id = res1.json()["id"]
    assert base_task.version == 1

    # Second request with exact same payload: 200 OK (idempotent replay)
    res2 = client.post("/api/tasks/1/decisions", json=payload)
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["id"] == dec_id
    assert res2.json()["idempotency_key"] == "dec_token_replay_test"

    # Ensure task version was not incremented again and only one decision exists
    assert base_task.version == 1
    assert len(db.decisions) == 1


def test_submit_decision_idempotency_payload_mismatch_fails(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    db = MockDecisionTaskDbSession(tasks=[base_task])
    client = create_test_client(current_user, db, mock_storage)

    payload1 = {
        "idempotency_key": "dec_token_mismatch_test",
        "expected_version": 0,
        "action": "lock_spec",
        "target_key": "tasks/101/1/snap.json",
        "details": {"selected_script_index": 1},
    }
    res1 = client.post("/api/tasks/1/decisions", json=payload1)
    assert res1.status_code == status.HTTP_201_CREATED

    # Attempt reuse of same token with different action / details
    payload2 = {
        "idempotency_key": "dec_token_mismatch_test",
        "expected_version": 1,
        "action": "skip_research",  # Different action!
        "target_key": "tasks/101/1/snap.json",
        "details": {"selected_script_index": 1},
    }
    res2 = client.post("/api/tasks/1/decisions", json=payload2)
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "already used with different decision payload" in res2.json()["detail"]


def test_list_task_decisions_success_and_not_found(
    current_user: UserAccount,
    base_task: VideoTask,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    d1 = VideoDecision(
        id=1,
        task_id=1,
        actor_user_id=101,
        action="skip_research",
        target_key="tasks/101/1/init.json",
        details={"notes": "Skipped research"},
        idempotency_key="idemp_111111",
        created_at=now,
    )
    d2 = VideoDecision(
        id=2,
        task_id=1,
        actor_user_id=101,
        action="lock_spec",
        target_key="tasks/101/1/script.json",
        details={"selected_script_index": 2},
        idempotency_key="idemp_222222",
        created_at=now,
    )
    db = MockDecisionTaskDbSession(tasks=[base_task], decisions=[d1, d2])
    client = create_test_client(current_user, db, mock_storage)

    # 1. Existing task returns chronological decisions
    res = client.get("/api/tasks/1/decisions")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["action"] == "skip_research"
    assert data["items"][1]["action"] == "lock_spec"

    # 2. Non-existent task returns 404
    res_nf = client.get("/api/tasks/999/decisions")
    assert res_nf.status_code == status.HTTP_404_NOT_FOUND
