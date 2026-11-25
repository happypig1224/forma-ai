"""Unit tests for video production task routes, snapshot storage, and lifecycle."""

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
    require_can_create_tasks,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.library import CategoryDTO
from formaai.modules.production.models import VideoTask
from formaai.modules.production.router import get_category_verifier, task_router


class MockTaskDbSession:
    """In-memory DB session mimicking MySQL for VideoTask operations."""

    def __init__(self, tasks: list[VideoTask] | None = None) -> None:
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self._next_id = max([t.id for t in self.tasks], default=0) + 1
        self._pending: list[VideoTask] = []

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )
        task_id = None
        user_id = None
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "id":
                    task_id = val
                elif col == "user_id":
                    user_id = val

        for t in self.tasks:
            if task_id is not None and t.id != task_id:
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
        user_id = None
        category_id = None
        content_type = None
        target_status = None
        target_stage = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "user_id":
                    user_id = val
                elif col == "category_id":
                    category_id = val
                elif col == "content_type":
                    content_type = val
                elif col == "status":
                    target_status = val
                elif col == "stage":
                    target_stage = val

        filtered = [
            t
            for t in self.tasks
            if (user_id is None or t.user_id == user_id)
            and (category_id is None or t.category_id == category_id)
            and (content_type is None or t.content_type == content_type)
            and (target_status is None or t.status == target_status)
            and (target_stage is None or t.stage == target_stage)
        ]
        filtered.sort(key=lambda t: (t.created_at, t.id), reverse=True)

        class ScalarsResult:
            def __init__(self, items: list[VideoTask]) -> None:
                self._items = items

            def all(self) -> list[VideoTask]:
                return self._items

        return ScalarsResult(filtered)

    def add(self, instance: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(instance, VideoTask):
            if instance.id is None:
                instance.id = self._next_id
                self._next_id += 1
            if instance.version is None:
                instance.version = 0
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
            self._pending.append(instance)

    def commit(self) -> None:
        for item in self._pending:
            if item not in self.tasks:
                self.tasks.append(item)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def active_user() -> UserAccount:
    user = UserAccount()
    user.id = 101
    user.email = "creator@formaai.test"
    user.role = "user"
    user.status = "active"
    user.version = 0
    return user


@pytest.fixture
def disabling_user() -> UserAccount:
    user = UserAccount()
    user.id = 102
    user.email = "disabling@formaai.test"
    user.role = "user"
    user.status = "disabling"
    user.version = 0
    return user


@pytest.fixture
def mock_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def mock_db() -> MockTaskDbSession:
    return MockTaskDbSession()


def mock_category_verifier(
    db: Any, category_id: int, user_id: int
) -> CategoryDTO | None:
    categories = {
        (1, 101): CategoryDTO(
            id=1,
            user_id=101,
            content_type="brand",
            name="Brand Cat",
            sort_order=0,
        ),
        (2, 101): CategoryDTO(
            id=2,
            user_id=101,
            content_type="commerce",
            name="Commerce Cat",
            sort_order=1,
        ),
    }
    return categories.get((category_id, user_id))


def create_test_client(
    user: UserAccount,
    db: MockTaskDbSession,
    storage: MockStorageService,
) -> TestClient:
    app = FastAPI()
    app.include_router(task_router)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_can_create_tasks] = lambda: (
        require_can_create_tasks(user)
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage
    app.dependency_overrides[get_category_verifier] = lambda: mock_category_verifier

    return TestClient(app)


def test_create_task_success(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    client = create_test_client(active_user, mock_db, mock_storage)
    payload = {
        "category_id": 1,
        "name": "Smart Earbuds Brand Launch",
        "content_type": "brand",
        "initial_input": {
            "theme": "Acoustic excellence and active lifestyle",
            "target_market": "Young urban professionals",
            "material_ids": [10, 11],
            "asset_ids": [100, 101],
            "allow_ai_expansion": True,
            "notes": "Fast tempo visual rhythm",
        },
    }

    res = client.post("/api/tasks", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["id"] == 1
    assert data["name"] == "Smart Earbuds Brand Launch"
    assert data["content_type"] == "brand"
    assert data["stage"] == "created"
    assert data["status"] == "pending"
    assert data["version"] == 0
    assert data["checkpoint_key"] is None
    assert "user_id" not in data

    # Verify input_key snapshot content in storage
    input_key = data["input_key"]
    raw_snapshot = mock_storage.get_file(input_key)
    assert raw_snapshot is not None
    snapshot_json = json.loads(raw_snapshot.decode("utf-8"))
    assert snapshot_json["theme"] == "Acoustic excellence and active lifestyle"
    assert snapshot_json["allow_ai_expansion"] is True


def test_create_task_category_not_found(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    client = create_test_client(active_user, mock_db, mock_storage)
    res = client.post(
        "/api/tasks",
        json={
            "category_id": 999,  # Non-existent
            "name": "Invalid Category Task",
            "content_type": "brand",
            "initial_input": {"theme": "Theme"},
        },
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Category not found"


def test_create_task_content_type_mismatch(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    client = create_test_client(active_user, mock_db, mock_storage)
    res = client.post(
        "/api/tasks",
        json={
            "category_id": 1,  # Category 1 is "brand"
            "name": "Mismatch Task",
            "content_type": "commerce",  # Mismatched
            "initial_input": {"theme": "Theme"},
        },
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "does not match category content_type" in res.json()["detail"]


def test_create_task_disabling_user_forbidden(
    disabling_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    client = create_test_client(disabling_user, mock_db, mock_storage)
    res = client.post(
        "/api/tasks",
        json={
            "category_id": 1,
            "name": "Prohibited Task",
            "content_type": "brand",
            "initial_input": {"theme": "Theme"},
        },
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "Account is disabling" in res.json()["detail"]


def test_list_tasks_and_filters(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    t1 = VideoTask(
        id=1,
        user_id=101,
        category_id=1,
        name="Task 1",
        content_type="brand",
        stage="created",
        status="pending",
        input_key="tasks/101/1/input_v1.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    t2 = VideoTask(
        id=2,
        user_id=101,
        category_id=2,
        name="Task 2",
        content_type="commerce",
        stage="research",
        status="running",
        input_key="tasks/101/2/input_v1.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    mock_db.tasks.extend([t1, t2])

    client = create_test_client(active_user, mock_db, mock_storage)

    # 1. List all
    res_all = client.get("/api/tasks")
    assert res_all.status_code == status.HTTP_200_OK
    assert res_all.json()["total"] == 2

    # 2. Filter by category
    res_cat = client.get("/api/tasks?category_id=1")
    assert res_cat.status_code == status.HTTP_200_OK
    assert res_cat.json()["total"] == 1
    assert res_cat.json()["items"][0]["name"] == "Task 1"

    # 3. Filter by status
    res_stat = client.get("/api/tasks?status=running")
    assert res_stat.status_code == status.HTTP_200_OK
    assert res_stat.json()["total"] == 1
    assert res_stat.json()["items"][0]["name"] == "Task 2"

    # 4. Filter by stage
    res_stage = client.get("/api/tasks?stage=research")
    assert res_stage.status_code == status.HTTP_200_OK
    assert res_stage.json()["total"] == 1


def test_get_task_by_id(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    t = VideoTask(
        id=10,
        user_id=101,
        category_id=1,
        name="Detail Task",
        content_type="brand",
        stage="script_proposal",
        status="waiting_confirmation",
        input_key="tasks/101/10/input_v1.json",
        checkpoint_key="tasks/101/10/ckpt_1.json",
        version=2,
        created_at=now,
        updated_at=now,
    )
    mock_db.tasks.append(t)

    client = create_test_client(active_user, mock_db, mock_storage)

    # Found
    res = client.get("/api/tasks/10")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["id"] == 10
    assert data["checkpoint_key"] == "tasks/101/10/ckpt_1.json"
    assert data["version"] == 2

    # Not found
    res_nf = client.get("/api/tasks/999")
    assert res_nf.status_code == status.HTTP_404_NOT_FOUND


def test_pause_and_resume_task_lifecycle(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        id=20,
        user_id=101,
        category_id=1,
        name="Pausable Task",
        content_type="brand",
        stage="created",
        status="pending",
        input_key="tasks/101/20/input_v1.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    mock_db.tasks.append(task)

    client = create_test_client(active_user, mock_db, mock_storage)

    # 1. Version conflict on pause
    res_conf = client.post(
        "/api/tasks/20/pause",
        json={"version": 99},
    )
    assert res_conf.status_code == status.HTTP_409_CONFLICT
    assert "version conflict" in res_conf.json()["detail"]

    # 2. Pause successfully
    res_pause = client.post(
        "/api/tasks/20/pause",
        json={"version": 0, "reason": "User requested pause"},
    )
    assert res_pause.status_code == status.HTTP_200_OK
    assert res_pause.json()["status"] == "paused"
    assert res_pause.json()["version"] == 1
    assert task.status == "paused"
    assert task.version == 1

    # 3. Cannot pause already paused task
    res_pause_dup = client.post(
        "/api/tasks/20/pause",
        json={"version": 1},
    )
    assert res_pause_dup.status_code == status.HTTP_400_BAD_REQUEST
    assert "already paused" in res_pause_dup.json()["detail"]

    # 4. Resume successfully
    res_resume = client.post(
        "/api/tasks/20/resume",
        json={"version": 1},
    )
    assert res_resume.status_code == status.HTTP_200_OK
    assert res_resume.json()["status"] == "pending"
    assert res_resume.json()["version"] == 2
    assert task.status == "pending"
    assert task.version == 2

    # 5. Cannot resume a non-paused task
    res_res_fail = client.post(
        "/api/tasks/20/resume",
        json={"version": 2},
    )
    assert res_res_fail.status_code == status.HTTP_400_BAD_REQUEST
    assert "Only paused tasks can be resumed" in res_res_fail.json()["detail"]


def test_cannot_pause_terminal_task(
    active_user: UserAccount,
    mock_db: MockTaskDbSession,
    mock_storage: MockStorageService,
) -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        id=30,
        user_id=101,
        category_id=1,
        name="Finished Task",
        content_type="brand",
        stage="finished",
        status="success",
        input_key="tasks/101/30/input_v1.json",
        version=5,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    mock_db.tasks.append(task)

    client = create_test_client(active_user, mock_db, mock_storage)

    res = client.post(
        "/api/tasks/30/pause",
        json={"version": 5},
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot pause task in terminal status 'success'" in res.json()["detail"]
