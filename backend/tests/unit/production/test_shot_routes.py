"""Unit tests for shot and attempt RESTful routes and cascade review engine."""

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


class MockShotDbSession:
    """Mock DB session supporting VideoTask, VideoShot, and VideoAttempt."""

    def __init__(
        self,
        tasks: list[VideoTask] | None = None,
        shots: list[VideoShot] | None = None,
        attempts: list[VideoAttempt] | None = None,
    ) -> None:
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.shots: list[VideoShot] = shots if shots is not None else []
        self.attempts: list[VideoAttempt] = attempts if attempts is not None else []
        self._next_attempt_id = 500

    def add(self, instance: Any) -> None:
        if isinstance(instance, VideoAttempt):
            if instance.id is None:
                instance.id = self._next_attempt_id
                self._next_attempt_id += 1
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
            self.attempts.append(instance)
        elif isinstance(instance, VideoShot):
            if instance.id is None:
                instance.id = len(self.shots) + 1
            self.shots.append(instance)

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
        shot_no = None
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
                elif col == "shot_id":
                    shot_id = val
                elif col == "shot_no":
                    shot_no = val
                elif col == "idempotency_key":
                    idempotency_key = val

        desc = getattr(stmt, "column_descriptions", [{}])
        expr_target = desc[0].get("expr") if desc else None
        entity = desc[0].get("entity") if desc else None

        # Check for max(attempt_no) aggregation
        if str(expr_target).lower().startswith("max("):
            matched = [
                a.attempt_no
                for a in self.attempts
                if shot_id is None or a.shot_id == shot_id
            ]
            return max(matched) if matched else None

        if entity is VideoAttempt:
            for a in self.attempts:
                if target_id is not None and a.id != target_id:
                    continue
                if shot_id is not None and a.shot_id != shot_id:
                    continue
                if idempotency_key is not None and a.idempotency_key != idempotency_key:
                    continue
                return a
            return None

        if entity is VideoShot:
            for s in self.shots:
                if target_id is not None and s.id != target_id:
                    continue
                if task_id is not None and s.task_id != task_id:
                    continue
                if shot_no is not None and s.shot_no != shot_no:
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
        shot_id = None
        min_shot_no = None
        in_ids: list[int] | None = None

        for expr in clauses:
            # Check for in_ clause
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                operator_name = getattr(expr, "operator", None)
                op_str = str(operator_name)
                if col == "task_id":
                    task_id = val
                elif col == "shot_id":
                    shot_id = val
                elif col == "shot_no" and ">" in op_str:
                    min_shot_no = val
            elif hasattr(expr, "left") and hasattr(expr, "right"):
                pass
            # in_ expression handling
            if hasattr(expr, "modifier") or "in" in str(getattr(expr, "operator", "")):
                if hasattr(expr, "right") and hasattr(expr.right, "clauses"):
                    in_ids = [c.value for c in expr.right.clauses]

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is VideoShot:
            filtered_shots = [
                s
                for s in self.shots
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
        filtered_attempts = [
            a
            for a in self.attempts
            if (shot_id is None or a.shot_id == shot_id)
            and (in_ids is None or a.id in in_ids)
        ]
        filtered_attempts.sort(key=lambda a: a.attempt_no)

        class AttemptScalarsResult:
            def __init__(self, items: list[VideoAttempt]) -> None:
                self._items = items

            def all(self) -> list[VideoAttempt]:
                return self._items

        return AttemptScalarsResult(filtered_attempts)


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


@pytest.fixture
def mock_storage() -> MockStorageService:
    return MockStorageService()


def create_test_env(
    test_user: UserAccount,
    storage: MockStorageService,
    tasks: list[VideoTask] | None = None,
    shots: list[VideoShot] | None = None,
    attempts: list[VideoAttempt] | None = None,
) -> tuple[TestClient, MockShotDbSession]:
    db = MockShotDbSession(tasks=tasks, shots=shots, attempts=attempts)
    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage
    return TestClient(app), db


def test_list_task_shots_ownership_and_ordering(
    test_user: UserAccount,
    other_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Sci-Fi Intro",
        content_type="story",
        stage="keyframes",
        status="running",
        input_key="tasks/101/1/input.json",
        version=1,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    shot1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Hero Close Up",
        duration_sec=3.0,
        selected_attempt_id=10,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    shot1.id = 1

    shot2 = VideoShot(
        task_id=1,
        shot_no=2,
        title="Spaceship Takeoff",
        duration_sec=5.0,
        selected_attempt_id=None,
        needs_review=True,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot2.id = 2

    attempt = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/1/attempt_1_input.json",
        result_key="tasks/101/1/shots/1/attempt_1_result.mp4",
        created_at=now,
    )
    attempt.id = 10

    # 1. Owner requests shots
    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot2, shot1],  # unordered input
        attempts=[attempt],
    )
    res = client.get("/api/tasks/1/shots")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["shot_no"] == 1
    assert data["items"][0]["selected_attempt"]["id"] == 10
    assert data["items"][1]["shot_no"] == 2
    assert data["items"][1]["selected_attempt"] is None
    assert data["items"][1]["needs_review"] is True

    # 2. Other user gets 404 (multi-tenant boundary)
    client_other, _ = create_test_env(
        other_user,
        mock_storage,
        tasks=[task],
        shots=[shot1, shot2],
    )
    res_other = client_other.get("/api/tasks/1/shots")
    assert res_other.status_code == status.HTTP_404_NOT_FOUND


def test_list_shot_attempts_ordered(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Showcase",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Product Detail",
        duration_sec=3.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot.id = 1

    att1 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="failed",
        input_key="tasks/101/1/shots/1/attempt_1_input.json",
        created_at=now,
    )
    att1.id = 101
    att2 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=2,
        operation="redo",
        status="succeeded",
        input_key="tasks/101/1/shots/1/attempt_2_input.json",
        result_key="tasks/101/1/shots/1/attempt_2_result.mp4",
        created_at=now,
    )
    att2.id = 102

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
        attempts=[att2, att1],
    )
    res = client.get("/api/tasks/1/shots/1/attempts")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["attempt_no"] == 1
    assert data["items"][1]["attempt_no"] == 2


def test_redo_shot_lifecycle_and_storage_snapshot(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Nature Doc",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Eagle Flight",
        duration_sec=4.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot.id = 1

    existing_att = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="failed",
        input_key="tasks/101/1/shots/1/attempt_1_input.json",
        created_at=now,
    )
    existing_att.id = 101

    client, db = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
        attempts=[existing_att],
    )

    payload = {
        "idempotency_key": "redo_token_eagle_001",
        "prompt": "Majestic eagle soaring above snow-capped mountain peaks",
        "negative_prompt": "cartoon, low resolution",
        "reference_image_key": "library/101/ref_eagle.jpg",
    }
    res = client.post("/api/tasks/1/shots/1/redo", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["shot_id"] == 1
    assert data["attempt_no"] == 2
    assert data["operation"] == "redo"
    assert data["status"] == "pending"

    # Verify storage snapshot
    stored_bytes = mock_storage.get_file(data["input_key"])
    assert stored_bytes is not None
    snapshot = json.loads(stored_bytes.decode("utf-8"))
    assert snapshot["shot_no"] == 1
    assert snapshot["attempt_no"] == 2
    assert (
        snapshot["payload"]["prompt"]
        == "Majestic eagle soaring above snow-capped mountain peaks"
    )


def test_redo_shot_idempotent_replay_and_conflict(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Promo",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Car Turning",
        duration_sec=3.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot.id = 1

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
    )

    payload = {
        "idempotency_key": "redo_idemp_car_99",
        "prompt": "Red sports car drifting on mountain pass at sunset",
    }

    # 1. First redo execution -> 201 Created
    res1 = client.post("/api/tasks/1/shots/1/redo", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED
    first_attempt_id = res1.json()["id"]

    # 2. Replay with identical payload -> 200 OK replay
    res2 = client.post("/api/tasks/1/shots/1/redo", json=payload)
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["id"] == first_attempt_id

    # 3. Replay with different payload -> 409 Conflict
    conflicting_payload = {
        "idempotency_key": "redo_idemp_car_99",
        "prompt": "Blue truck driving through city street",
    }
    res3 = client.post("/api/tasks/1/shots/1/redo", json=conflicting_payload)
    assert res3.status_code == status.HTTP_409_CONFLICT
    assert "different redo parameters" in res3.json()["detail"]


def test_redo_shot_rejected_when_task_paused(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Paused Task",
        content_type="commerce",
        stage="shot_video",
        status="paused",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Intro",
        duration_sec=2.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot.id = 1

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
    )
    payload = {
        "idempotency_key": "redo_paused_tok_11",
        "prompt": "Some prompt",
    }
    res = client.post("/api/tasks/1/shots/1/redo", json=payload)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot redo shot when task status is 'paused'" in res.json()["detail"]


def test_select_shot_attempt_optimistic_locking_conflict(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Lock Task",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Scene",
        duration_sec=3.0,
        selected_attempt_id=None,
        needs_review=False,
        version=2,  # Current version is 2
        created_at=now,
        updated_at=now,
    )
    shot.id = 1

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
    )
    res = client.post(
        "/api/tasks/1/shots/1/select",
        json={"attempt_id": 1, "expected_version": 1},  # Stale version 1!
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "Shot version conflict" in res.json()["detail"]


def test_select_shot_attempt_rejects_pending_or_foreign_attempt(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Validation Task",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1
    shot1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Scene 1",
        duration_sec=3.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot1.id = 1
    shot2 = VideoShot(
        task_id=1,
        shot_no=2,
        title="Scene 2",
        duration_sec=3.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    shot2.id = 2

    # Attempt belonging to Shot 2, not Shot 1!
    att_foreign = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/2/attempt_1_input.json",
        created_at=now,
    )
    att_foreign.id = 201

    # Attempt belonging to Shot 1 but still pending
    att_pending = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="pending",
        input_key="tasks/101/1/shots/1/attempt_1_input.json",
        created_at=now,
    )
    att_pending.id = 101

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot1, shot2],
        attempts=[att_foreign, att_pending],
    )

    # 1. Foreign attempt -> 404 Not Found
    res1 = client.post(
        "/api/tasks/1/shots/1/select",
        json={"attempt_id": 201, "expected_version": 0},
    )
    assert res1.status_code == status.HTTP_404_NOT_FOUND
    assert "Attempt not found for this shot" in res1.json()["detail"]

    # 2. Pending attempt -> 400 Bad Request
    res2 = client.post(
        "/api/tasks/1/shots/1/select",
        json={"attempt_id": 101, "expected_version": 0},
    )
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
    assert "Only succeeded attempts can be selected" in res2.json()["detail"]


def test_select_shot_attempt_cascade_review_propagation(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    """Selecting attempt on shot 2 marks downstream shots 3 and 4 as needs_review."""
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Cascade Story",
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
        selected_attempt_id=10,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s1.id = 1

    s2 = VideoShot(
        task_id=1,
        shot_no=2,
        title="Conflict",
        duration_sec=4.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
        created_at=now,
        updated_at=now,
    )
    s2.id = 2

    s3 = VideoShot(
        task_id=1,
        shot_no=3,
        title="Climax",
        duration_sec=5.0,
        selected_attempt_id=30,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s3.id = 3

    s4 = VideoShot(
        task_id=1,
        shot_no=4,
        title="Resolution",
        duration_sec=3.0,
        selected_attempt_id=40,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s4.id = 4

    att2 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/2/attempt_1_input.json",
        result_key="tasks/101/1/shots/2/attempt_1_result.mp4",
        created_at=now,
    )
    att2.id = 20

    client, db = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[s1, s2, s3, s4],
        attempts=[att2],
    )

    res = client.post(
        "/api/tasks/1/shots/2/select",
        json={"attempt_id": 20, "expected_version": 0},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["selected_attempt_id"] == 20
    assert data["version"] == 1

    # Verify Cascade Review Engine state across all shots:
    # Shot 1 (upstream) remains needs_review=False
    assert s1.needs_review is False
    # Shot 2 is the current shot
    assert s2.needs_review is False
    # Shot 3 (downstream) marked as needs_review=True!
    assert s3.needs_review is True
    # Shot 4 (downstream) marked as needs_review=True!
    assert s4.needs_review is True


def test_accept_shot_review_clears_flag_and_increments_version(
    test_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=test_user.id,
        category_id=1,
        name="Review Task",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task.id = 1

    shot = VideoShot(
        task_id=1,
        shot_no=3,
        title="Scene 3 Reviewed",
        duration_sec=3.0,
        selected_attempt_id=30,
        needs_review=True,  # Initially flagged for review
        version=1,
        created_at=now,
        updated_at=now,
    )
    shot.id = 3

    att3 = VideoAttempt(
        task_id=1,
        shot_id=3,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/3/attempt_1_input.json",
        result_key="tasks/101/1/shots/3/attempt_1_result.mp4",
        created_at=now,
    )
    att3.id = 30

    client, _ = create_test_env(
        test_user,
        mock_storage,
        tasks=[task],
        shots=[shot],
        attempts=[att3],
    )

    # 1. Version conflict -> 409 Conflict
    res_conflict = client.post(
        "/api/tasks/1/shots/3/accept-review",
        json={"expected_version": 0},
    )
    assert res_conflict.status_code == status.HTTP_409_CONFLICT

    # 2. Correct version -> 200 OK, needs_review becomes False, version becomes 2
    res_ok = client.post(
        "/api/tasks/1/shots/3/accept-review",
        json={"expected_version": 1},
    )
    assert res_ok.status_code == status.HTTP_200_OK
    data = res_ok.json()
    assert data["needs_review"] is False
    assert data["version"] == 2
    assert data["selected_attempt"]["id"] == 30
