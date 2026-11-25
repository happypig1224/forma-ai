"""End-to-end multi-tenant task isolation, concurrency, and lifecycle integration tests.

Validates complete security and state machine boundaries for video production:
1. Multi-tenant concurrent task creation into isolated private categories.
2. Cross-tenant category hijacking defense: Bob cannot create in Alice's cat (404).
3. Cross-tenant IDOR attack defense: Bob cannot read Alice's task (404).
4. Cross-tenant IDOR mutation defense: Bob cannot pause/resume Alice's task (404).
5. Optimistic concurrency control: concurrent conflicting pause/resume triggers 409.
6. Account state matrix: disabling user cannot create (403) but can manage;
   disabled and deleted users are rejected at authentication boundary (401).
7. Snapshot immutability: input snapshot is safely persisted in private storage.
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
from formaai.modules.production.models import VideoTask
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


class MultiTenantTaskDbSession:
    """In-memory DB session mimicking MySQL across multi-tenant production."""

    def __init__(
        self,
        users: list[UserAccount] | None = None,
        categories: list[Category] | None = None,
        tasks: list[VideoTask] | None = None,
    ) -> None:
        self.users: list[UserAccount] = users if users is not None else []
        self.categories: list[Category] = categories if categories is not None else []
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self._next_task_id = max([t.id for t in self.tasks], default=0) + 1
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
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "user_id":
                    user_id = val
                elif col == "id":
                    target_id = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        # UserAccount query for authentication
        if entity is UserAccount:
            for u in self.users:
                if target_id is not None and u.id != target_id:
                    continue
                return u
            return None

        # Category query
        if entity is Category:
            for c in self.categories:
                if target_id is not None and c.id != target_id:
                    continue
                if user_id is not None and c.user_id != user_id:
                    continue
                return c
            return None

        # VideoTask query
        if entity is VideoTask or entity is None:
            for t in self.tasks:
                if target_id is not None and t.id != target_id:
                    continue
                if user_id is not None and t.user_id != user_id:
                    continue
                return t
            return None

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
            if isinstance(item, VideoTask) and item not in self.tasks:
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
        mysql_database="formaai_e2e_production",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("e2e-production-test-access-sec-32!"),
        jwt_refresh_secret=SecretStr("e2e-production-test-refr-sec-32!"),
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
def e2e_env(
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
    charlie = UserAccount(
        id=103, email="charlie@formaai.test", role="user", status="disabling", version=0
    )
    david = UserAccount(
        id=104, email="david@formaai.test", role="user", status="disabled", version=0
    )
    eve = UserAccount(
        id=105,
        email="eve@formaai.test",
        role="user",
        status="deleted",
        version=0,
        deleted_at=now,
    )
    users = [alice, bob, charlie, david, eve]

    # Pre-seeded categories
    cat_alice = Category(
        id=1,
        user_id=101,
        content_type="brand",
        name="Alice Brand Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    cat_bob = Category(
        id=2,
        user_id=102,
        content_type="commerce",
        name="Bob Commerce Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    cat_charlie = Category(
        id=3,
        user_id=103,
        content_type="story",
        name="Charlie Story Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    categories = [cat_alice, cat_bob, cat_charlie]

    # Pre-seeded existing task for Charlie (disabling user)
    charlie_task = VideoTask(
        id=100,
        user_id=103,
        category_id=3,
        name="Charlie Existing Task",
        content_type="story",
        stage="created",
        status="pending",
        input_key="library/103/ch/snapshot.json",
        checkpoint_key=None,
        version=0,
        created_at=now,
        updated_at=now,
    )

    db_session = MultiTenantTaskDbSession(
        users=users,
        categories=categories,
        tasks=[charlie_task],
    )

    def e2e_category_verifier(
        db: Any, category_id: int, user_id: int
    ) -> CategoryDTO | None:
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
    app.dependency_overrides[get_category_verifier] = lambda: e2e_category_verifier

    return {
        "app": app,
        "settings": test_settings,
        "session_manager": session_manager,
        "storage": shared_storage,
        "db": db_session,
        "users": {
            "alice": alice,
            "bob": bob,
            "charlie": charlie,
            "david": david,
            "eve": eve,
        },
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
# 1. Multi-Tenant Task Creation & List Isolation
# ==============================================================================


def test_e2e_concurrent_task_creation_and_list_isolation(
    e2e_env: dict[str, Any],
) -> None:
    """Alice and Bob create tasks; lists and snapshots are strictly isolated."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    storage: MockStorageService = e2e_env["storage"]
    alice: UserAccount = e2e_env["users"]["alice"]
    bob: UserAccount = e2e_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-s1")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-s1")

    with TestClient(app) as client:
        # 1. Alice creates task in Category 1 (brand)
        res_a = client.post(
            "/api/tasks",
            json={
                "category_id": 1,
                "name": "Alice Brand Story",
                "content_type": "brand",
                "initial_input": {
                    "theme": "Acoustic Purity",
                    "target_market": "Creators",
                    "allow_ai_expansion": False,
                },
            },
            headers=h_alice,
        )
        assert res_a.status_code == status.HTTP_201_CREATED
        task_a_data = res_a.json()
        assert task_a_data["name"] == "Alice Brand Story"
        assert task_a_data["input_key"].startswith("library/101/")

        # 2. Bob creates task in Category 2 (commerce)
        res_b = client.post(
            "/api/tasks",
            json={
                "category_id": 2,
                "name": "Bob Product Ad",
                "content_type": "commerce",
                "initial_input": {
                    "theme": "Direct conversion discount",
                    "target_market": "E-commerce shoppers",
                    "allow_ai_expansion": True,
                },
            },
            headers=h_bob,
        )
        assert res_b.status_code == status.HTTP_201_CREATED
        task_b_data = res_b.json()
        assert task_b_data["name"] == "Bob Product Ad"
        assert task_b_data["input_key"].startswith("library/102/")

        # 3. Verify storage file isolation
        snap_a = json.loads(storage.get_file(task_a_data["input_key"]).decode("utf-8"))
        assert snap_a["theme"] == "Acoustic Purity"
        snap_b = json.loads(storage.get_file(task_b_data["input_key"]).decode("utf-8"))
        assert snap_b["theme"] == "Direct conversion discount"

        # 4. Verify list query isolation
        list_a = client.get("/api/tasks", headers=h_alice).json()
        assert list_a["total"] == 1
        assert list_a["items"][0]["name"] == "Alice Brand Story"

        list_b = client.get("/api/tasks", headers=h_bob).json()
        assert list_b["total"] == 1
        assert list_b["items"][0]["name"] == "Bob Product Ad"


# ==============================================================================
# 2. Cross-Tenant Category Hijacking & IDOR Attack Defense
# ==============================================================================


def test_e2e_cross_tenant_category_hijacking_rejected(
    e2e_env: dict[str, Any],
) -> None:
    """Bob cannot create a task referencing Alice's Category 1 (returns 404)."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    bob: UserAccount = e2e_env["users"]["bob"]

    h_bob = issue_auth_header(bob, settings, sm, sid="bob-s2")

    with TestClient(app) as client:
        res = client.post(
            "/api/tasks",
            json={
                "category_id": 1,  # Belongs to Alice
                "name": "Bob Hijack Task",
                "content_type": "brand",
                "initial_input": {"theme": "Hijack Attempt"},
            },
            headers=h_bob,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Category not found"


def test_e2e_cross_tenant_task_idor_read_rejected(
    e2e_env: dict[str, Any],
) -> None:
    """Bob cannot read Alice's Task detail (returns 404)."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    alice: UserAccount = e2e_env["users"]["alice"]
    bob: UserAccount = e2e_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-s3")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-s3")

    with TestClient(app) as client:
        # Alice creates a task
        create_res = client.post(
            "/api/tasks",
            json={
                "category_id": 1,
                "name": "Alice Private Project",
                "content_type": "brand",
                "initial_input": {"theme": "Top Secret Brand"},
            },
            headers=h_alice,
        )
        task_id = create_res.json()["id"]

        # Bob probes Alice's task ID
        res_bob = client.get(f"/api/tasks/{task_id}", headers=h_bob)
        assert res_bob.status_code == status.HTTP_404_NOT_FOUND
        assert res_bob.json()["detail"] == "Task not found"


def test_e2e_cross_tenant_task_idor_pause_resume_rejected(
    e2e_env: dict[str, Any],
) -> None:
    """Bob cannot pause or resume Alice's Task (returns 404)."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    alice: UserAccount = e2e_env["users"]["alice"]
    bob: UserAccount = e2e_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-s4")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-s4")

    with TestClient(app) as client:
        # Alice creates task
        create_res = client.post(
            "/api/tasks",
            json={
                "category_id": 1,
                "name": "Alice Active Project",
                "content_type": "brand",
                "initial_input": {"theme": "Theme"},
            },
            headers=h_alice,
        )
        task_id = create_res.json()["id"]

        # Bob tries to pause Alice's task
        res_pause = client.post(
            f"/api/tasks/{task_id}/pause",
            json={"version": 0},
            headers=h_bob,
        )
        assert res_pause.status_code == status.HTTP_404_NOT_FOUND
        assert res_pause.json()["detail"] == "Task not found"

        # Bob tries to resume Alice's task
        res_resume = client.post(
            f"/api/tasks/{task_id}/resume",
            json={"version": 0},
            headers=h_bob,
        )
        assert res_resume.status_code == status.HTTP_404_NOT_FOUND
        assert res_resume.json()["detail"] == "Task not found"


# ==============================================================================
# 3. Optimistic Concurrency Conflict Defense
# ==============================================================================


def test_e2e_optimistic_lock_concurrency_conflict(
    e2e_env: dict[str, Any],
) -> None:
    """Two concurrent clients reading version=0; second fails with 409."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    alice: UserAccount = e2e_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-s5")

    with TestClient(app) as client:
        create_res = client.post(
            "/api/tasks",
            json={
                "category_id": 1,
                "name": "Concurrent Project",
                "content_type": "brand",
                "initial_input": {"theme": "Theme"},
            },
            headers=h_alice,
        )
        task_id = create_res.json()["id"]
        assert create_res.json()["version"] == 0

        # Tab A pauses task with version 0
        res_tab_a = client.post(
            f"/api/tasks/{task_id}/pause",
            json={"version": 0},
            headers=h_alice,
        )
        assert res_tab_a.status_code == status.HTTP_200_OK
        assert res_tab_a.json()["version"] == 1

        # Tab B attempts action with stale version 0 -> 409 Conflict
        res_tab_b = client.post(
            f"/api/tasks/{task_id}/pause",
            json={"version": 0},
            headers=h_alice,
        )
        assert res_tab_b.status_code == status.HTTP_409_CONFLICT
        assert "version conflict" in res_tab_b.json()["detail"]


# ==============================================================================
# 4. Account State Linkage Matrix (Disabling vs Disabled vs Deleted)
# ==============================================================================


def test_e2e_account_state_matrix_disabling_disabled_deleted(
    e2e_env: dict[str, Any],
) -> None:
    """Disabling cannot create (403); disabled & deleted rejected (401)."""
    app = e2e_env["app"]
    settings: Settings = e2e_env["settings"]
    sm: MockSessionManager = e2e_env["session_manager"]
    charlie: UserAccount = e2e_env["users"]["charlie"]  # disabling
    david: UserAccount = e2e_env["users"]["david"]  # disabled
    eve: UserAccount = e2e_env["users"]["eve"]  # deleted

    h_charlie = issue_auth_header(charlie, settings, sm, sid="charlie-s1")
    h_david = issue_auth_header(david, settings, sm, sid="david-s1")
    h_eve = issue_auth_header(eve, settings, sm, sid="eve-s1")

    with TestClient(app) as client:
        # 1. Charlie (disabling) cannot create new tasks -> 403 Forbidden
        res_charlie_create = client.post(
            "/api/tasks",
            json={
                "category_id": 3,
                "name": "Charlie Prohibited Task",
                "content_type": "story",
                "initial_input": {"theme": "Theme"},
            },
            headers=h_charlie,
        )
        assert res_charlie_create.status_code == status.HTTP_403_FORBIDDEN
        assert "Account is disabling" in res_charlie_create.json()["detail"]

        # 2. Charlie (disabling) CAN list and pause existing task (Task 100) -> 200 OK
        res_charlie_list = client.get("/api/tasks", headers=h_charlie)
        assert res_charlie_list.status_code == status.HTTP_200_OK
        assert res_charlie_list.json()["total"] == 1

        res_charlie_pause = client.post(
            "/api/tasks/100/pause",
            json={"version": 0},
            headers=h_charlie,
        )
        assert res_charlie_pause.status_code == status.HTTP_200_OK
        assert res_charlie_pause.json()["status"] == "paused"

        # 3. David (disabled) rejected at authentication -> 401 Unauthorized
        res_david = client.get("/api/tasks", headers=h_david)
        assert res_david.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is disabled" in res_david.json()["detail"]

        # 4. Eve (deleted) rejected at authentication -> 401 Unauthorized
        res_eve = client.get("/api/tasks", headers=h_eve)
        assert res_eve.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is deleted" in res_eve.json()["detail"]
