"""End-to-end multi-tenant shot isolation, cascade review, and concurrency tests.

Validates security, narrative consistency, and state machine boundaries for shots:
1. Cross-tenant IDOR defense: Bob cannot access or modify Alice's shots (404).
2. Cascade review engine: selecting an attempt on a shot triggers needs_review=True
   on all downstream shots, while upstream shots remain untouched.
3. Multi-tenant storage & idempotency: concurrent redos isolate storage snapshots;
   same key returns 200 OK; modified payload returns 409 Conflict.
4. Optimistic locking defense: concurrent conflicting selects/reviews fail with 409.
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
from formaai.modules.production.models import VideoAttempt, VideoShot, VideoTask
from formaai.modules.production.router import task_router


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


class MultiTenantShotDbSession:
    """In-memory DB session mimicking MySQL for VideoTask, VideoShot, VideoAttempt."""

    def __init__(
        self,
        users: list[UserAccount] | None = None,
        tasks: list[VideoTask] | None = None,
        shots: list[VideoShot] | None = None,
        attempts: list[VideoAttempt] | None = None,
    ) -> None:
        self.users: list[UserAccount] = users if users is not None else []
        self.tasks: list[VideoTask] = tasks if tasks is not None else []
        self.shots: list[VideoShot] = shots if shots is not None else []
        self.attempts: list[VideoAttempt] = attempts if attempts is not None else []
        self._next_attempt_id = 1000
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

        # Check for max(attempt_no) aggregation
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

    def add(self, instance: Any) -> None:
        if isinstance(instance, VideoAttempt):
            if instance.id is None:
                instance.id = self._next_attempt_id
                self._next_attempt_id += 1
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
            if instance.status is None:
                instance.status = "pending"
        elif isinstance(instance, VideoShot):
            if instance.id is None:
                instance.id = len(self.shots) + 1
            if instance.version is None:
                instance.version = 0
            if instance.needs_review is None:
                instance.needs_review = False
        self._pending.append(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, VideoAttempt) and item not in self.attempts:
                self.attempts.append(item)
            elif isinstance(item, VideoShot) and item not in self.shots:
                self.shots.append(item)
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        mysql_database="formaai_e2e_shot",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("e2e-shot-test-access-secret-32b!"),
        jwt_refresh_secret=SecretStr("e2e-shot-test-refresh-secret-32b!"),
        jwt_issuer="https://auth.formaai.test",
        jwt_audience="https://api.formaai.test",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=86400,
    )


@pytest.fixture
def shared_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def e2e_shot_env(
    test_settings: Settings,
    shared_storage: MockStorageService,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    alice = UserAccount(email="alice.director@formaai.test", role="admin")
    alice.id = 101
    alice.account_status = "active"
    alice.version = 0

    bob = UserAccount(email="bob.producer@formaai.test", role="admin")
    bob.id = 102
    bob.account_status = "active"
    bob.version = 0

    # Task 1: Alice's task with 4 shots
    task1 = VideoTask(
        user_id=alice.id,
        category_id=1,
        name="Alice Short Film",
        content_type="story",
        stage="shot_video",
        status="running",
        input_key="tasks/101/1/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task1.id = 1

    s1 = VideoShot(
        task_id=1,
        shot_no=1,
        title="Shot 1 Intro",
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
        title="Shot 2 Dialogue",
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
        title="Shot 3 Conflict",
        duration_sec=5.0,
        selected_attempt_id=103,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s3.id = 3

    s4 = VideoShot(
        task_id=1,
        shot_no=4,
        title="Shot 4 Outro",
        duration_sec=3.5,
        selected_attempt_id=104,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s4.id = 4

    att_101 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/1/att_1.json",
        result_key="tasks/101/1/shots/1/att_1.mp4",
        created_at=now,
    )
    att_101.id = 101

    att_102 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/2/att_1.json",
        result_key="tasks/101/1/shots/2/att_1.mp4",
        created_at=now,
    )
    att_102.id = 102

    att_103 = VideoAttempt(
        task_id=1,
        shot_id=3,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/3/att_1.json",
        result_key="tasks/101/1/shots/3/att_1.mp4",
        created_at=now,
    )
    att_103.id = 103

    att_104 = VideoAttempt(
        task_id=1,
        shot_id=4,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/4/att_1.json",
        result_key="tasks/101/1/shots/4/att_1.mp4",
        created_at=now,
    )
    att_104.id = 104

    # Task 2: Bob's task with 1 shot
    task2 = VideoTask(
        user_id=bob.id,
        category_id=2,
        name="Bob Commercial",
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
        title="Product Reveal",
        duration_sec=3.0,
        selected_attempt_id=201,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    bob_s1.id = 10

    bob_att_201 = VideoAttempt(
        task_id=2,
        shot_id=10,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/102/2/shots/1/att_1.json",
        result_key="tasks/102/2/shots/1/att_1.mp4",
        created_at=now,
    )
    bob_att_201.id = 201

    db = MultiTenantShotDbSession(
        users=[alice, bob],
        tasks=[task1, task2],
        shots=[s1, s2, s3, s4, bob_s1],
        attempts=[att_101, att_102, att_103, att_104, bob_att_201],
    )
    sm = MockSessionManager()

    app = FastAPI()
    app.include_router(task_router)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_storage_service] = lambda: shared_storage
    app.dependency_overrides[get_db] = lambda: db

    return {
        "app": app,
        "db": db,
        "storage": shared_storage,
        "session_manager": sm,
        "settings": test_settings,
        "users": {"alice": alice, "bob": bob},
        "shots": {"s1": s1, "s2": s2, "s3": s3, "s4": s4, "bob_s1": bob_s1},
        "attempts": {"att_102": att_102, "bob_att_201": bob_att_201},
    }


def issue_auth_header(
    user: UserAccount,
    settings: Settings,
    session_manager: MockSessionManager,
    sid: str = "e2e-session",
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
# 1. Multi-Tenant IDOR Attack Defense
# ==============================================================================


def test_e2e_cross_tenant_shot_idor_rejected(
    e2e_shot_env: dict[str, Any],
) -> None:
    """Bob cannot access, redo, select, or review Alice's shots; all return 404."""
    app = e2e_shot_env["app"]
    settings: Settings = e2e_shot_env["settings"]
    sm: MockSessionManager = e2e_shot_env["session_manager"]
    bob: UserAccount = e2e_shot_env["users"]["bob"]
    alice: UserAccount = e2e_shot_env["users"]["alice"]

    h_bob = issue_auth_header(bob, settings, sm, sid="bob-idor-s1")
    h_alice = issue_auth_header(alice, settings, sm, sid="alice-idor-s1")

    with TestClient(app) as client:
        # 1. Bob attempts to list Alice's shots (Task 1) -> 404
        res1 = client.get("/api/tasks/1/shots", headers=h_bob)
        assert res1.status_code == status.HTTP_404_NOT_FOUND

        # 2. Bob attempts to view attempts on Alice's Shot 1 -> 404
        res2 = client.get("/api/tasks/1/shots/1/attempts", headers=h_bob)
        assert res2.status_code == status.HTTP_404_NOT_FOUND

        # 3. Bob attempts to trigger Redo on Alice's Shot 1 -> 404
        res3 = client.post(
            "/api/tasks/1/shots/1/redo",
            json={
                "idempotency_key": "bob_attack_redo_01",
                "prompt": "Hacked malicious prompt",
            },
            headers=h_bob,
        )
        assert res3.status_code == status.HTTP_404_NOT_FOUND

        # 4. Bob attempts to select an attempt on Alice's Shot 1 -> 404
        res4 = client.post(
            "/api/tasks/1/shots/1/select",
            json={"attempt_id": 101, "expected_version": 1},
            headers=h_bob,
        )
        assert res4.status_code == status.HTTP_404_NOT_FOUND

        # 5. Bob attempts to accept review on Alice's Shot 1 -> 404
        res5 = client.post(
            "/api/tasks/1/shots/1/accept-review",
            json={"expected_version": 1},
            headers=h_bob,
        )
        assert res5.status_code == status.HTTP_404_NOT_FOUND

        # 6. Alice attempts to trigger Redo on Bob's Task 2 Shot 1 -> 404
        res6 = client.post(
            "/api/tasks/2/shots/1/redo",
            json={
                "idempotency_key": "alice_attack_redo_01",
                "prompt": "Cross-tenant intrusion",
            },
            headers=h_alice,
        )
        assert res6.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================
# 2. Cascade Review Engine & Lifecycle Flow
# ==============================================================================


def test_e2e_cascade_review_propagation_and_resolution(
    e2e_shot_env: dict[str, Any],
) -> None:
    """Selecting shot 2 candidate propagates needs_review to downstream shots 3 & 4."""
    app = e2e_shot_env["app"]
    settings: Settings = e2e_shot_env["settings"]
    sm: MockSessionManager = e2e_shot_env["session_manager"]
    alice: UserAccount = e2e_shot_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-cascade-s1")

    with TestClient(app) as client:
        # Verify initial states: all 4 shots have needs_review = False
        initial_shots = client.get("/api/tasks/1/shots", headers=h_alice).json()
        assert initial_shots["total"] == 4
        assert not any(s["needs_review"] for s in initial_shots["items"])

        # Alice selects attempt 102 on Shot 2 (expected_version: 0)
        res_sel = client.post(
            "/api/tasks/1/shots/2/select",
            json={"attempt_id": 102, "expected_version": 0},
            headers=h_alice,
        )
        assert res_sel.status_code == status.HTTP_200_OK
        data_s2 = res_sel.json()
        assert data_s2["selected_attempt_id"] == 102
        assert data_s2["version"] == 1
        assert data_s2["needs_review"] is False

        # Verify Cascade Review Engine propagation:
        # Shot 1 (upstream) -> needs_review: False
        # Shot 2 (current) -> needs_review: False
        # Shot 3 (downstream) -> needs_review: True
        # Shot 4 (downstream) -> needs_review: True
        shots_after_select = client.get("/api/tasks/1/shots", headers=h_alice).json()[
            "items"
        ]
        assert shots_after_select[0]["shot_no"] == 1
        assert shots_after_select[0]["needs_review"] is False
        assert shots_after_select[1]["shot_no"] == 2
        assert shots_after_select[1]["needs_review"] is False
        assert shots_after_select[2]["shot_no"] == 3
        assert shots_after_select[2]["needs_review"] is True
        assert shots_after_select[3]["shot_no"] == 4
        assert shots_after_select[3]["needs_review"] is True

        # Alice reviews and accepts Shot 3 (version was 1)
        res_rev3 = client.post(
            "/api/tasks/1/shots/3/accept-review",
            json={"expected_version": 1},
            headers=h_alice,
        )
        assert res_rev3.status_code == status.HTTP_200_OK
        data_s3 = res_rev3.json()
        assert data_s3["needs_review"] is False
        assert data_s3["version"] == 2

        # Check Shot 4 is still flagged for review
        shots_intermediate = client.get("/api/tasks/1/shots", headers=h_alice).json()[
            "items"
        ]
        assert shots_intermediate[2]["needs_review"] is False
        assert shots_intermediate[3]["needs_review"] is True

        # Alice reviews and accepts Shot 4 (version was 1)
        res_rev4 = client.post(
            "/api/tasks/1/shots/4/accept-review",
            json={"expected_version": 1},
            headers=h_alice,
        )
        assert res_rev4.status_code == status.HTTP_200_OK
        data_s4 = res_rev4.json()
        assert data_s4["needs_review"] is False
        assert data_s4["version"] == 2

        # All shots are clean
        final_shots = client.get("/api/tasks/1/shots", headers=h_alice).json()["items"]
        assert not any(s["needs_review"] for s in final_shots)


# ==============================================================================
# 3. Concurrent Redo Idempotency & Storage Isolation
# ==============================================================================


def test_e2e_redo_idempotency_and_tenant_storage_isolation(
    e2e_shot_env: dict[str, Any],
) -> None:
    """Concurrent redos maintain tenant storage isolation and idempotency."""
    app = e2e_shot_env["app"]
    settings: Settings = e2e_shot_env["settings"]
    sm: MockSessionManager = e2e_shot_env["session_manager"]
    storage: MockStorageService = e2e_shot_env["storage"]
    alice: UserAccount = e2e_shot_env["users"]["alice"]
    bob: UserAccount = e2e_shot_env["users"]["bob"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-redo-s1")
    h_bob = issue_auth_header(bob, settings, sm, sid="bob-redo-s1")

    payload_alice = {
        "idempotency_key": "alice_redo_idemp_001",
        "prompt": "Alice cinematic sunset shot with lens flare",
        "negative_prompt": "blurry, low quality",
    }
    payload_bob = {
        "idempotency_key": "bob_redo_idemp_001",
        "prompt": "Bob dynamic sports car studio lighting",
        "negative_prompt": "noise, artifacts",
    }

    with TestClient(app) as client:
        # 1. Concurrent Redo submissions
        res_a = client.post(
            "/api/tasks/1/shots/2/redo", json=payload_alice, headers=h_alice
        )
        assert res_a.status_code == status.HTTP_201_CREATED
        att_a = res_a.json()

        res_b = client.post(
            "/api/tasks/2/shots/1/redo", json=payload_bob, headers=h_bob
        )
        assert res_b.status_code == status.HTTP_201_CREATED
        att_b = res_b.json()

        # 2. Verify Storage snapshot keys are strictly tenant-isolated
        assert att_a["input_key"].startswith("library/101/")
        assert att_b["input_key"].startswith("library/102/")

        # Verify storage content integrity
        bytes_a = storage.get_file(att_a["input_key"])
        assert bytes_a is not None
        snap_a = json.loads(bytes_a.decode("utf-8"))
        assert snap_a["user_id"] == 101
        assert (
            snap_a["payload"]["prompt"] == "Alice cinematic sunset shot with lens flare"
        )

        bytes_b = storage.get_file(att_b["input_key"])
        assert bytes_b is not None
        snap_b = json.loads(bytes_b.decode("utf-8"))
        assert snap_b["user_id"] == 102
        assert snap_b["payload"]["prompt"] == "Bob dynamic sports car studio lighting"

        # 3. Idempotent replay: exact repeat returns 200 OK
        res_a_replay = client.post(
            "/api/tasks/1/shots/2/redo", json=payload_alice, headers=h_alice
        )
        assert res_a_replay.status_code == status.HTTP_200_OK
        assert res_a_replay.json()["id"] == att_a["id"]

        # 4. Same key with conflicting parameters returns 409 Conflict
        conflicting_payload = {
            **payload_alice,
            "prompt": "Modified conflicting prompt for same token",
        }
        res_conflict = client.post(
            "/api/tasks/1/shots/2/redo",
            json=conflicting_payload,
            headers=h_alice,
        )
        assert res_conflict.status_code == status.HTTP_409_CONFLICT
        assert "different redo parameters" in res_conflict.json()["detail"]


# ==============================================================================
# 4. Optimistic Locking Race Condition Defense
# ==============================================================================


def test_e2e_shot_optimistic_locking_race_defense(
    e2e_shot_env: dict[str, Any],
) -> None:
    """Stale version submissions for select and accept-review are rejected (409)."""
    app = e2e_shot_env["app"]
    settings: Settings = e2e_shot_env["settings"]
    sm: MockSessionManager = e2e_shot_env["session_manager"]
    alice: UserAccount = e2e_shot_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-lock-s1")

    with TestClient(app) as client:
        # Tab 1 selects attempt 102 with expected_version 0 -> succeeds
        res1 = client.post(
            "/api/tasks/1/shots/2/select",
            json={"attempt_id": 102, "expected_version": 0},
            headers=h_alice,
        )
        assert res1.status_code == status.HTTP_200_OK
        assert res1.json()["version"] == 1

        # Tab 2 concurrently attempts select with stale expected_version 0 -> 409
        res2 = client.post(
            "/api/tasks/1/shots/2/select",
            json={"attempt_id": 102, "expected_version": 0},
            headers=h_alice,
        )
        assert res2.status_code == status.HTTP_409_CONFLICT
        assert "Shot version conflict" in res2.json()["detail"]

        # Downstream Shot 3 has version 1 and needs_review = True
        # Tab 2 attempts accept-review on Shot 3 with stale expected_version 0 -> 409
        res3 = client.post(
            "/api/tasks/1/shots/3/accept-review",
            json={"expected_version": 0},
            headers=h_alice,
        )
        assert res3.status_code == status.HTTP_409_CONFLICT
        assert "Shot version conflict" in res3.json()["detail"]

        # Tab 2 uses fresh version 1 -> succeeds
        res4 = client.post(
            "/api/tasks/1/shots/3/accept-review",
            json={"expected_version": 1},
            headers=h_alice,
        )
        assert res4.status_code == status.HTTP_200_OK
        assert res4.json()["version"] == 2
        assert res4.json()["needs_review"] is False
