"""Unit tests for composition, completion, cancellation, and cleanup routes."""

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
from formaai.modules.production.models import VideoAttempt, VideoShot, VideoTask
from formaai.modules.production.router import task_router


class MockTerminalDbSession:
    """Mock DB session supporting terminal lifecycle tests."""

    def __init__(
        self,
        tasks: list[VideoTask] | None = None,
        shots: list[VideoShot] | None = None,
        attempts: list[VideoAttempt] | None = None,
    ) -> None:
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.shots: list[VideoShot] = shots if shots is not None else []
        self.attempts: list[VideoAttempt] = attempts if attempts is not None else []

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
        shot_id = None

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
                elif col == "shot_id":
                    shot_id = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is VideoAttempt:
            for a in self.attempts:
                if target_id is not None and a.id != target_id:
                    continue
                if shot_id is not None and a.shot_id != shot_id:
                    continue
                return a
            return None

        if entity is VideoShot:
            for s in self.shots:
                if target_id is not None and s.id != target_id:
                    continue
                if task_id is not None and s.task_id != task_id:
                    continue
                return s
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
        in_ids: list[int] | None = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "task_id":
                    task_id = val
            if hasattr(expr, "modifier") or "in" in str(getattr(expr, "operator", "")):
                if hasattr(expr, "right") and hasattr(expr.right, "clauses"):
                    in_ids = [c.value for c in expr.right.clauses]

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is VideoShot:
            filtered_shots = [
                s for s in self.shots if task_id is None or s.task_id == task_id
            ]
            filtered_shots.sort(key=lambda s: s.shot_no)

            class ShotResult:
                def __init__(self, items: list[VideoShot]) -> None:
                    self._items = items

                def all(self) -> list[VideoShot]:
                    return self._items

            return ShotResult(filtered_shots)

        # Default: VideoAttempt
        filtered_attempts = [
            a
            for a in self.attempts
            if (task_id is None or a.task_id == task_id)
            and (in_ids is None or a.id in in_ids)
        ]

        class AttemptResult:
            def __init__(self, items: list[VideoAttempt]) -> None:
                self._items = items

            def all(self) -> list[VideoAttempt]:
                return self._items

        return AttemptResult(filtered_attempts)


@pytest.fixture
def test_user() -> UserAccount:
    user = UserAccount(email="director@formaai.test", role="admin")
    user.id = 101
    user.account_status = "active"
    user.token_version = 0
    return user


@pytest.fixture
def mock_storage() -> MockStorageService:
    return MockStorageService()


def create_env(
    test_user: UserAccount,
    storage: MockStorageService,
    tasks: list[VideoTask] | None = None,
    shots: list[VideoShot] | None = None,
    attempts: list[VideoAttempt] | None = None,
) -> tuple[TestClient, MockTerminalDbSession]:
    db = MockTerminalDbSession(tasks=tasks, shots=shots, attempts=attempts)
    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage
    return TestClient(app), db


def test_compose_task_gatekeeper_rejects_empty_shots(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Empty Task",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    client, _ = create_env(test_user, mock_storage, tasks=[task], shots=[])
    res = client.post(
        "/api/tasks/1/compose",
        json={"idempotency_key": "compose_tok_empty_01", "expected_version": 0},
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "no configured shots" in res.json()["detail"]


def test_compose_task_gatekeeper_rejects_unselected_or_pending_review_shots(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Incomplete Task",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    s1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Shot 1",
        duration_sec=3.0,
        selected_attempt_id=None,  # Unselected!
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    s1.id = 1
    s2 = VideoShot(
        task_id=1,
        shot_no=2,
        title="Shot 2",
        duration_sec=4.0,
        selected_attempt_id=102,
        needs_review=True,  # Needs review!
        version=0,
        created_at=now,
        updated_at=now,
    )
    s2.id = 2

    # 1. Unselected attempt rejected
    client1, _ = create_env(test_user, mock_storage, tasks=[task], shots=[s1, s2])
    res1 = client1.post(
        "/api/tasks/1/compose",
        json={"idempotency_key": "compose_tok_unsel_01", "expected_version": 0},
    )
    assert res1.status_code == status.HTTP_400_BAD_REQUEST
    assert "do not have a selected attempt" in res1.json()["detail"]

    # 2. Needs review rejected
    s1.selected_attempt_id = 101
    att101 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/att1.json",
        result_key="tasks/101/1/att1.mp4",
        created_at=now,
    )
    att101.id = 101
    att102 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/att2.json",
        result_key="tasks/101/1/att2.mp4",
        created_at=now,
    )
    att102.id = 102

    client2, _ = create_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[s1, s2],
        attempts=[att101, att102],
    )
    res2 = client2.post(
        "/api/tasks/1/compose",
        json={"idempotency_key": "compose_tok_rev_01", "expected_version": 0},
    )
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
    assert "require review before composition" in res2.json()["detail"]


def test_compose_task_success_manifest_and_state_transition(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Valid Film",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    s1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Intro",
        duration_sec=3.0,
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
        title="Outro",
        duration_sec=4.0,
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
        input_key="tasks/101/1/att1.json",
        result_key="tasks/101/1/att1.mp4",
        created_at=now,
    )
    att101.id = 101
    att102 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/att2.json",
        result_key="tasks/101/1/att2.mp4",
        created_at=now,
    )
    att102.id = 102

    client, _ = create_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[s1, s2],
        attempts=[att101, att102],
    )
    res = client.post(
        "/api/tasks/1/compose",
        json={"idempotency_key": "compose_tok_valid_01", "expected_version": 0},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["stage"] == "composition"
    assert data["status"] == "running"
    assert data["version"] == 1
    assert data["checkpoint_key"] is not None

    # Verify composition manifest stored in storage
    raw = mock_storage.get_file(data["checkpoint_key"])
    assert raw is not None
    manifest = json.loads(raw.decode("utf-8"))
    assert manifest["task_id"] == 1
    assert len(manifest["shots"]) == 2
    assert manifest["shots"][0]["result_key"] == "tasks/101/1/att1.mp4"
    assert manifest["shots"][1]["result_key"] == "tasks/101/1/att2.mp4"


def test_complete_task_lifecycle_and_terminal_state(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Composing Film",
        content_type="story",
        stage="composition",
        status="running",
        input_key="tasks/101/1/input.json",
        version=1,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    client, _ = create_env(test_user, mock_storage, tasks=[task])

    # Complete task
    payload = {
        "final_video_key": "library/101/tasks/1/final_render.mp4",
        "expected_version": 1,
    }
    res = client.post("/api/tasks/1/complete", json=payload)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["stage"] == "finished"
    assert data["status"] == "success"
    assert data["checkpoint_key"] == "library/101/tasks/1/final_render.mp4"
    assert data["finished_at"] is not None
    assert data["version"] == 2

    # Attempting to complete already finished task -> 400 (not in composition)
    res_repeat = client.post(
        "/api/tasks/1/complete",
        json={
            "final_video_key": "library/101/tasks/1/final_render.mp4",
            "expected_version": 2,
        },
    )
    assert res_repeat.status_code == status.HTTP_400_BAD_REQUEST


def test_cancel_task_lifecycle(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Running Film",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    client, _ = create_env(test_user, mock_storage, tasks=[task])

    # Cancel task
    payload = {"expected_version": 0, "reason": "Change of production plans"}
    res = client.post("/api/tasks/1/cancel", json=payload)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "failed"
    assert data["finished_at"] is not None
    assert data["version"] == 1

    # Attempting to cancel already cancelled task -> 400
    res_repeat = client.post("/api/tasks/1/cancel", json={"expected_version": 1})
    assert res_repeat.status_code == status.HTTP_400_BAD_REQUEST
    assert "already 'failed'" in res_repeat.json()["detail"]


def test_cleanup_task_artifacts_reclaims_unselected_attempts(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Finished Film",
        content_type="story",
        stage="finished",
        status="success",
        input_key="tasks/101/1/input.json",
        version=2,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    shot1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Scene 1",
        duration_sec=3.0,
        selected_attempt_id=101,  # selected
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    shot1.id = 1

    # Put attempt files into mock storage
    mock_storage.save_file(b"att1_in", "att1_in.json", "application/json", 101)
    k1_in = "tasks/101/1/att1_in.json"
    k1_res = "tasks/101/1/att1_res.mp4"
    mock_storage.files[k1_in] = b"input1"
    mock_storage.files[k1_res] = b"result1"

    k2_in = "tasks/101/1/att2_in.json"
    k2_res = "tasks/101/1/att2_res.mp4"
    mock_storage.files[k2_in] = b"input2_junk"
    mock_storage.files[k2_res] = b"result2_junk"

    att1 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key=k1_in,
        result_key=k1_res,
        created_at=now,
    )
    att1.id = 101

    att2 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=2,
        operation="redo",
        status="failed",
        input_key=k2_in,
        result_key=k2_res,
        created_at=now,
    )
    att2.id = 102  # Unselected!

    client, _ = create_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot1],
        attempts=[att1, att2],
    )

    res = client.post("/api/tasks/1/cleanup", json={"keep_selected_only": True})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["task_id"] == 1
    assert data["reclaimed_files_count"] == 2

    # Verify unselected files deleted, selected files preserved
    assert k2_in not in mock_storage.files
    assert k2_res not in mock_storage.files
    assert k1_in in mock_storage.files
    assert k1_res in mock_storage.files


def test_terminal_optimistic_locking_defense(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Lock Film",
        content_type="story",
        stage="composition",
        status="running",
        input_key="tasks/101/1/input.json",
        version=5,  # current version is 5
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    client, _ = create_env(test_user, mock_storage, tasks=[task])

    # 1. Compose stale version 4 -> 409
    res1 = client.post(
        "/api/tasks/1/compose",
        json={"idempotency_key": "comp_stale_12345", "expected_version": 4},
    )
    assert res1.status_code == status.HTTP_409_CONFLICT
    assert "Task version conflict" in res1.json()["detail"]

    # 2. Complete stale version 4 -> 409
    res2 = client.post(
        "/api/tasks/1/complete",
        json={"final_video_key": "library/101/out.mp4", "expected_version": 4},
    )
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "Task version conflict" in res2.json()["detail"]

    # 3. Cancel stale version 4 -> 409
    res3 = client.post(
        "/api/tasks/1/cancel",
        json={"expected_version": 4},
    )
    assert res3.status_code == status.HTTP_409_CONFLICT
    assert "Task version conflict" in res3.json()["detail"]
