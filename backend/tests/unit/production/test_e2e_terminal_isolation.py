"""End-to-end multi-tenant composition gatekeeper, terminal, and cleanup tests.

Validates security, gatekeeper invariants, and state transitions for video production:
1. Cross-tenant IDOR defense: Bob cannot compose, complete, cancel, or cleanup
   Alice's task (404 Not Found).
2. Composition gatekeeper defense: unselected attempts and unreviewed shots strictly
   block composition (400 Bad Request); resolution unblocks composition.
3. Terminal completion & artifact cleanup: successful completion updates status,
   and derivative artifact cleanup frees unselected files while keeping assets.
4. Concurrency & cancellation: optimistic locking prevents stale updates (409 Conflict).
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


class MultiTenantTerminalDbSession:
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
        self._next_attempt_id = 2000
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
        mysql_database="formaai_e2e_terminal",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("e2e-term-test-access-secret-32b!"),
        jwt_refresh_secret=SecretStr("e2e-term-test-refresh-secret-32b!"),
        jwt_issuer="https://auth.formaai.test",
        jwt_audience="https://api.formaai.test",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=86400,
    )


@pytest.fixture
def shared_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def e2e_terminal_env(
    test_settings: Settings,
    shared_storage: MockStorageService,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    alice = UserAccount(email="alice.creator@formaai.test", role="admin")
    alice.id = 101
    alice.account_status = "active"
    alice.version = 0

    bob = UserAccount(email="bob.spectator@formaai.test", role="admin")
    bob.id = 102
    bob.account_status = "active"
    bob.version = 0

    # Task 1: Alice's production task
    task1 = VideoTask(
        user_id=alice.id,
        category_id=1,
        name="Alice Masterpiece",
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
        title="Shot 1 Establishing",
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
        title="Shot 2 Action",
        duration_sec=4.0,
        selected_attempt_id=102,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s2.id = 2

    s3 = VideoShot(
        task_id=1,
        shot_no=3,
        title="Shot 3 Climax",
        duration_sec=5.0,
        selected_attempt_id=103,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    s3.id = 3

    # Storage paths for Alice's task attempts
    k101_in = "tasks/101/1/shots/1/att_1_in.json"
    k101_res = "tasks/101/1/shots/1/att_1_res.mp4"
    k102_in = "tasks/101/1/shots/2/att_1_in.json"
    k102_res = "tasks/101/1/shots/2/att_1_res.mp4"
    k103_in = "tasks/101/1/shots/3/att_1_in.json"
    k103_res = "tasks/101/1/shots/3/att_1_res.mp4"

    # Temporary unselected attempt files to test cleanup
    k_junk_in = "tasks/101/1/shots/2/att_junk_in.json"
    k_junk_res = "tasks/101/1/shots/2/att_junk_res.mp4"

    shared_storage.files[k101_in] = b"input101"
    shared_storage.files[k101_res] = b"video101"
    shared_storage.files[k102_in] = b"input102"
    shared_storage.files[k102_res] = b"video102"
    shared_storage.files[k103_in] = b"input103"
    shared_storage.files[k103_res] = b"video103"
    shared_storage.files[k_junk_in] = b"input_junk"
    shared_storage.files[k_junk_res] = b"video_junk"

    att101 = VideoAttempt(
        task_id=1,
        shot_id=1,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key=k101_in,
        result_key=k101_res,
        created_at=now,
    )
    att101.id = 101

    att102 = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key=k102_in,
        result_key=k102_res,
        created_at=now,
    )
    att102.id = 102

    att103 = VideoAttempt(
        task_id=1,
        shot_id=3,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key=k103_in,
        result_key=k103_res,
        created_at=now,
    )
    att103.id = 103

    att_junk = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=2,
        operation="redo",
        status="failed",
        input_key=k_junk_in,
        result_key=k_junk_res,
        created_at=now,
    )
    att_junk.id = 199

    # Task 2: Bob's task
    task2 = VideoTask(
        user_id=bob.id,
        category_id=2,
        name="Bob Feature",
        content_type="commerce",
        stage="shot_video",
        status="running",
        input_key="tasks/102/2/input.json",
        version=0,
        created_at=now,
        updated_at=now,
    )
    task2.id = 2

    db = MultiTenantTerminalDbSession(
        users=[alice, bob],
        tasks=[task1, task2],
        shots=[s1, s2, s3],
        attempts=[att101, att102, att103, att_junk],
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
        "tasks": {"task1": task1, "task2": task2},
        "shots": {"s1": s1, "s2": s2, "s3": s3},
        "keys": {
            "junk_in": k_junk_in,
            "junk_res": k_junk_res,
            "k101_in": k101_in,
            "k101_res": k101_res,
        },
    }


def issue_auth_header(
    user: UserAccount,
    settings: Settings,
    session_manager: MockSessionManager,
    sid: str = "e2e-term-session",
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
# 1. Multi-Tenant IDOR Attack Defense on Terminal Endpoints
# ==============================================================================


def test_e2e_cross_tenant_terminal_idor_rejected(
    e2e_terminal_env: dict[str, Any],
) -> None:
    """Bob cannot compose, complete, cancel, or cleanup Alice's task (all 404)."""
    app = e2e_terminal_env["app"]
    settings: Settings = e2e_terminal_env["settings"]
    sm: MockSessionManager = e2e_terminal_env["session_manager"]
    bob: UserAccount = e2e_terminal_env["users"]["bob"]
    alice: UserAccount = e2e_terminal_env["users"]["alice"]

    h_bob = issue_auth_header(bob, settings, sm, sid="bob-term-idor")
    h_alice = issue_auth_header(alice, settings, sm, sid="alice-term-idor")

    with TestClient(app) as client:
        # 1. Bob attempts to compose Alice's Task 1 -> 404
        res1 = client.post(
            "/api/tasks/1/compose",
            json={"idempotency_key": "bob_hack_comp_01", "expected_version": 1},
            headers=h_bob,
        )
        assert res1.status_code == status.HTTP_404_NOT_FOUND

        # 2. Bob attempts to complete Alice's Task 1 -> 404
        res2 = client.post(
            "/api/tasks/1/complete",
            json={
                "final_video_key": "library/101/fake.mp4",
                "expected_version": 1,
            },
            headers=h_bob,
        )
        assert res2.status_code == status.HTTP_404_NOT_FOUND

        # 3. Bob attempts to cancel Alice's Task 1 -> 404
        res3 = client.post(
            "/api/tasks/1/cancel",
            json={"expected_version": 1, "reason": "malicious abort"},
            headers=h_bob,
        )
        assert res3.status_code == status.HTTP_404_NOT_FOUND

        # 4. Bob attempts to trigger cleanup on Alice's Task 1 -> 404
        res4 = client.post(
            "/api/tasks/1/cleanup",
            json={"keep_selected_only": True},
            headers=h_bob,
        )
        assert res4.status_code == status.HTTP_404_NOT_FOUND

        # 5. Alice attempts to cancel Bob's Task 2 -> 404
        res5 = client.post(
            "/api/tasks/2/cancel",
            json={"expected_version": 0},
            headers=h_alice,
        )
        assert res5.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================
# 2. Composition Gatekeeper Defense & Resolution Flow
# ==============================================================================


def test_e2e_composition_gatekeeper_defense_and_resolution(
    e2e_terminal_env: dict[str, Any],
) -> None:
    """Downstream unreviewed shots block composition; review unblocks it."""
    app = e2e_terminal_env["app"]
    settings: Settings = e2e_terminal_env["settings"]
    sm: MockSessionManager = e2e_terminal_env["session_manager"]
    storage: MockStorageService = e2e_terminal_env["storage"]
    alice: UserAccount = e2e_terminal_env["users"]["alice"]
    s2: VideoShot = e2e_terminal_env["shots"]["s2"]
    s3: VideoShot = e2e_terminal_env["shots"]["s3"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-gatekeeper")

    with TestClient(app) as client:
        # Simulate an upstream modification that cascades needs_review=True to Shot 3
        s3.needs_review = True

        # 1. Attempt compose when Shot 3 requires review -> Blocked (400)
        res_blocked = client.post(
            "/api/tasks/1/compose",
            json={"idempotency_key": "comp_idemp_gate_01", "expected_version": 1},
            headers=h_alice,
        )
        assert res_blocked.status_code == status.HTTP_400_BAD_REQUEST
        assert "require review before composition" in res_blocked.json()["detail"]

        # Also simulate Shot 2 losing selection -> Blocked (400)
        s2.selected_attempt_id = None
        res_blocked2 = client.post(
            "/api/tasks/1/compose",
            json={"idempotency_key": "comp_idemp_gate_01", "expected_version": 1},
            headers=h_alice,
        )
        assert res_blocked2.status_code == status.HTTP_400_BAD_REQUEST
        assert "do not have a selected attempt" in res_blocked2.json()["detail"]

        # 2. Resolve blockers:
        s2.selected_attempt_id = 102
        s3.needs_review = False

        # 3. Re-trigger compose -> Succeeded (200 OK)
        res_ok = client.post(
            "/api/tasks/1/compose",
            json={"idempotency_key": "comp_idemp_gate_01", "expected_version": 1},
            headers=h_alice,
        )
        assert res_ok.status_code == status.HTTP_200_OK
        data = res_ok.json()
        assert data["stage"] == "composition"
        assert data["status"] == "running"
        assert data["version"] == 2
        assert data["checkpoint_key"] is not None

        # Verify composition manifest content integrity in storage
        raw_manifest = storage.get_file(data["checkpoint_key"])
        assert raw_manifest is not None
        manifest_obj = json.loads(raw_manifest.decode("utf-8"))
        assert manifest_obj["task_id"] == 1
        assert len(manifest_obj["shots"]) == 3
        assert manifest_obj["shots"][0]["attempt_id"] == 101
        assert manifest_obj["shots"][1]["attempt_id"] == 102
        assert manifest_obj["shots"][2]["attempt_id"] == 103


# ==============================================================================
# 3. Terminal Completion & Derivative Artifact Cleanup
# ==============================================================================


def test_e2e_terminal_completion_and_artifact_cleanup(
    e2e_terminal_env: dict[str, Any],
) -> None:
    """Full lifecycle from composition to finished state and temporary cleanup."""
    app = e2e_terminal_env["app"]
    settings: Settings = e2e_terminal_env["settings"]
    sm: MockSessionManager = e2e_terminal_env["session_manager"]
    storage: MockStorageService = e2e_terminal_env["storage"]
    alice: UserAccount = e2e_terminal_env["users"]["alice"]
    task1: VideoTask = e2e_terminal_env["tasks"]["task1"]
    keys = e2e_terminal_env["keys"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-complete-flow")

    with TestClient(app) as client:
        # Move task into composition stage
        task1.stage = "composition"
        task1.status = "running"
        task1.version = 2

        # 1. Complete Task
        final_key = "library/101/tasks/1/master_render.mp4"
        res_comp = client.post(
            "/api/tasks/1/complete",
            json={"final_video_key": final_key, "expected_version": 2},
            headers=h_alice,
        )
        assert res_comp.status_code == status.HTTP_200_OK
        data = res_comp.json()
        assert data["stage"] == "finished"
        assert data["status"] == "success"
        assert data["checkpoint_key"] == final_key
        assert data["finished_at"] is not None
        assert data["version"] == 3

        # 2. Cannot re-complete already finished task -> 400
        res_recomp = client.post(
            "/api/tasks/1/complete",
            json={"final_video_key": final_key, "expected_version": 3},
            headers=h_alice,
        )
        assert res_recomp.status_code == status.HTTP_400_BAD_REQUEST

        # 3. Execute artifact cleanup: unselected attempts files reclaimed
        assert keys["junk_in"] in storage.files
        assert keys["junk_res"] in storage.files
        assert keys["k101_in"] in storage.files
        assert keys["k101_res"] in storage.files

        res_clean = client.post(
            "/api/tasks/1/cleanup",
            json={"keep_selected_only": True},
            headers=h_alice,
        )
        assert res_clean.status_code == status.HTTP_200_OK
        clean_data = res_clean.json()
        assert clean_data["reclaimed_files_count"] == 2

        # Verify junk attempt files deleted from storage
        assert keys["junk_in"] not in storage.files
        assert keys["junk_res"] not in storage.files

        # Verify selected attempt files intact
        assert keys["k101_in"] in storage.files
        assert keys["k101_res"] in storage.files


# ==============================================================================
# 4. Cancellation Lifecycle & Optimistic Locking Concurrency
# ==============================================================================


def test_e2e_cancellation_lifecycle_and_optimistic_race(
    e2e_terminal_env: dict[str, Any],
) -> None:
    """Abort task with reason and verify optimistic locking defense on race."""
    app = e2e_terminal_env["app"]
    settings: Settings = e2e_terminal_env["settings"]
    sm: MockSessionManager = e2e_terminal_env["session_manager"]
    alice: UserAccount = e2e_terminal_env["users"]["alice"]

    h_alice = issue_auth_header(alice, settings, sm, sid="alice-cancel-race")

    with TestClient(app) as client:
        # 1. Stale version cancellation attempt -> 409 Conflict
        res_stale = client.post(
            "/api/tasks/1/cancel",
            json={"expected_version": 99, "reason": "Stale abort"},
            headers=h_alice,
        )
        assert res_stale.status_code == status.HTTP_409_CONFLICT
        assert "Task version conflict" in res_stale.json()["detail"]

        # 2. Correct version cancellation -> 200 OK
        res_cancel = client.post(
            "/api/tasks/1/cancel",
            json={"expected_version": 1, "reason": "Director aborted project"},
            headers=h_alice,
        )
        assert res_cancel.status_code == status.HTTP_200_OK
        data = res_cancel.json()
        assert data["status"] == "failed"
        assert data["finished_at"] is not None
        assert data["version"] == 2

        # 3. Re-cancellation of already failed/cancelled task -> 400 Bad Request
        res_re_cancel = client.post(
            "/api/tasks/1/cancel",
            json={"expected_version": 2},
            headers=h_alice,
        )
        assert res_re_cancel.status_code == status.HTTP_400_BAD_REQUEST
        assert "already 'failed'" in res_re_cancel.json()["detail"]
