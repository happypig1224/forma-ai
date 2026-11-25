"""Comprehensive multi-tenant penetration, IDOR, and account state defense matrix.

Validates zero-trust boundaries across the entire library module:
1. Account state matrix: active, disabling, disabled, deleted, expired tokens.
2. Cross-tenant horizontal privilege escalation (IDOR) attacks.
3. Cross-tenant category hijacking during material creation, update, upload/copy.
4. Admin privilege boundary: admin cannot access or tamper with private data.
5. Storage path traversal defense: filenames and keys cannot escape sandbox.
6. Multi-tenant referential deletion protection: checks respect boundaries.
"""

import tempfile
import time
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from formaai.config import Settings
from formaai.infrastructure.storage import LocalStorageService, MockStorageService
from formaai.modules.identity.dependencies import (
    get_db,
    get_session_manager,
    get_settings,
    get_storage_service,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.tokens import create_token_pair
from formaai.modules.library.models import Asset, Category, Material
from formaai.modules.library.router import (
    asset_router,
    category_router,
    material_router,
)


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


class PenetrationTestDbSession:
    """In-memory multi-tenant DB session mimicking MySQL with entity introspection."""

    def __init__(
        self,
        users: list[UserAccount] | None = None,
        categories: list[Category] | None = None,
        materials: list[Material] | None = None,
        assets: list[Asset] | None = None,
    ) -> None:
        self.users: list[UserAccount] = users if users is not None else []
        self.categories: list[Category] = categories if categories is not None else []
        self.materials: list[Material] = materials if materials is not None else []
        self.assets: list[Asset] = assets if assets is not None else []
        self._next_cat_id = max([c.id for c in self.categories], default=0) + 1
        self._next_mat_id = max([m.id for m in self.materials], default=0) + 1
        self._next_ass_id = max([a.id for a in self.assets], default=0) + 1
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
        category_id = None
        name = None
        target_type = None

        stmt_str = str(stmt).lower()
        if "max" in stmt_str:
            target_content_type = None
            max_user_id = None
            for expr in clauses:
                if hasattr(expr, "left") and hasattr(expr, "right"):
                    if expr.left.name == "content_type":
                        target_content_type = expr.right.value
                    elif expr.left.name == "user_id":
                        max_user_id = expr.right.value
            matches = [
                c.sort_order
                for c in self.categories
                if c.user_id == max_user_id and c.content_type == target_content_type
            ]
            return max(matches) if matches else None

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "user_id":
                    user_id = val
                elif col == "id":
                    target_id = val
                elif col == "category_id":
                    category_id = val
                elif col == "name":
                    name = val
                elif col == "content_type":
                    target_type = val

        # Handle UserAccount query for authentication
        if entity is UserAccount:
            for u in self.users:
                if target_id is not None and u.id != target_id:
                    continue
                return u
            return None

        # Handle Asset queries
        if entity is Asset:
            if category_id is not None and target_id is None:
                for a in self.assets:
                    if a.category_id == category_id:
                        return a.id
                return None
            for a in self.assets:
                if target_id is not None and a.id != target_id:
                    continue
                if user_id is not None and a.user_id != user_id:
                    continue
                return a
            return None

        # Handle Material queries
        if entity is Material:
            if category_id is not None and target_id is None:
                for m in self.materials:
                    if m.category_id == category_id:
                        return m.id
                return None
            for m in self.materials:
                if target_id is not None and m.id != target_id:
                    continue
                if user_id is not None and m.user_id != user_id:
                    continue
                return m
            return None

        # Handle Category queries
        if entity is Category or entity is None:
            for c in self.categories:
                if target_id is not None and c.id != target_id:
                    continue
                if user_id is not None and c.user_id != user_id:
                    continue
                if target_type is not None and c.content_type != target_type:
                    continue
                if name is not None and c.name.lower() != str(name).lower():
                    continue
                return c
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
        target_type = None
        mime_type = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "user_id":
                    user_id = val
                elif col == "category_id":
                    category_id = val
                elif col == "content_type":
                    target_type = val
                elif col == "mime_type":
                    mime_type = val

        desc = getattr(stmt, "column_descriptions", [{}])
        entity = desc[0].get("entity") if desc else None

        if entity is Material:
            filtered_m = [
                m
                for m in self.materials
                if (user_id is None or m.user_id == user_id)
                and (category_id is None or m.category_id == category_id)
            ]
            filtered_m.sort(key=lambda m: (m.created_at, m.id), reverse=True)

            class MaterialScalarsResult:
                def __init__(self, items: list[Material]) -> None:
                    self._items = items

                def all(self) -> list[Material]:
                    return self._items

            return MaterialScalarsResult(filtered_m)

        if entity is Category:
            filtered_c = [
                c
                for c in self.categories
                if (user_id is None or c.user_id == user_id)
                and (target_type is None or c.content_type == target_type)
            ]
            filtered_c.sort(key=lambda c: (c.sort_order, c.id))

            class CategoryResult:
                def __init__(self, items: list[Category]) -> None:
                    self._items = items

                def all(self) -> list[Category]:
                    return self._items

            return CategoryResult(filtered_c)

        filtered_a = [
            a
            for a in self.assets
            if (user_id is None or a.user_id == user_id)
            and (category_id is None or a.category_id == category_id)
            and (mime_type is None or a.mime_type == mime_type)
        ]
        filtered_a.sort(key=lambda a: (a.created_at, a.id), reverse=True)

        class AssetScalarsResult:
            def __init__(self, items: list[Asset]) -> None:
                self._items = items

            def all(self) -> list[Asset]:
                return self._items

        return AssetScalarsResult(filtered_a)

    def add(self, instance: Any) -> None:
        now = datetime.now(UTC)
        if isinstance(instance, Asset):
            if instance.id is None:
                instance.id = self._next_ass_id
                self._next_ass_id += 1
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
        elif isinstance(instance, Material):
            if instance.id is None:
                instance.id = self._next_mat_id
                self._next_mat_id += 1
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
        elif isinstance(instance, Category):
            if instance.id is None:
                instance.id = self._next_cat_id
                self._next_cat_id += 1
            if instance.created_at is None:
                instance.created_at = now
            if instance.updated_at is None:
                instance.updated_at = now
        self._pending.append(instance)

    def delete(self, instance: Any) -> None:
        if isinstance(instance, Asset) and instance in self.assets:
            self.assets.remove(instance)
        elif isinstance(instance, Material) and instance in self.materials:
            self.materials.remove(instance)
        elif isinstance(instance, Category) and instance in self.categories:
            self.categories.remove(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, Asset) and item not in self.assets:
                self.assets.append(item)
            elif isinstance(item, Material) and item not in self.materials:
                self.materials.append(item)
            elif isinstance(item, Category) and item not in self.categories:
                self.categories.append(item)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def penetration_settings() -> Settings:
    """Deterministic application settings for security token evaluation."""
    return Settings(
        app_env="test",
        mysql_database="formaai_penetration",
        mysql_user="formaai_user",
        mysql_password=SecretStr("super-secret-password-12345!"),
        redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        jwt_access_secret=SecretStr("penetration-test-access-secret-32b!"),
        jwt_refresh_secret=SecretStr("penetration-test-refresh-secret-32!"),
        jwt_issuer="https://auth.formaai.test",
        jwt_audience="https://api.formaai.test",
        jwt_access_ttl_seconds=300,
        jwt_refresh_ttl_seconds=86400,
    )


@pytest.fixture
def mock_session_manager() -> MockSessionManager:
    return MockSessionManager()


@pytest.fixture
def mock_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def penetration_env(
    penetration_settings: Settings,
    mock_session_manager: MockSessionManager,
    mock_storage: MockStorageService,
) -> dict[str, Any]:
    """Sets up a comprehensive multi-tenant penetration testing environment."""
    now = datetime.now(UTC)

    # 1. Six distinct accounts covering roles and lifecycles
    alice = UserAccount(
        id=101,
        email="alice@formaai.test",
        role="user",
        status="active",
        version=0,
    )
    bob = UserAccount(
        id=102,
        email="bob@formaai.test",
        role="user",
        status="active",
        version=0,
    )
    charlie = UserAccount(
        id=103,
        email="charlie@formaai.test",
        role="user",
        status="disabling",
        version=0,
    )
    david = UserAccount(
        id=104,
        email="david@formaai.test",
        role="user",
        status="disabled",
        version=0,
    )
    eve = UserAccount(
        id=105,
        email="eve@formaai.test",
        role="user",
        status="deleted",
        version=0,
        deleted_at=now,
    )
    admin = UserAccount(
        id=999,
        email="admin@formaai.test",
        role="admin",
        status="active",
        version=0,
    )
    users = [alice, bob, charlie, david, eve, admin]

    # 2. Pre-seeded resources
    # Alice (id=101) owns Category 1, Material 1, Asset 1
    cat_alice = Category(
        id=1,
        user_id=101,
        content_type="brand",
        name="Alice Brand Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    mat_alice = Material(
        id=1,
        user_id=101,
        category_id=1,
        material_type="research",
        title="Alice Brand Research",
        content="Confidential Alice analysis",
        sources=[],
        created_at=now,
        updated_at=now,
    )
    asset_alice = Asset(
        id=1,
        user_id=101,
        category_id=1,
        name="alice_logo.png",
        file_key="library/101/al/alice_logo_hash.png",
        mime_type="image/png",
        file_size=1024,
        created_at=now,
        updated_at=now,
    )

    # Bob (id=102) owns Category 2, Material 2, Asset 2
    cat_bob = Category(
        id=2,
        user_id=102,
        content_type="brand",
        name="Bob Brand Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    mat_bob = Material(
        id=2,
        user_id=102,
        category_id=2,
        material_type="research",
        title="Bob Brand Research",
        content="Confidential Bob analysis",
        sources=[],
        created_at=now,
        updated_at=now,
    )
    asset_bob = Asset(
        id=2,
        user_id=102,
        category_id=2,
        name="bob_logo.png",
        file_key="library/102/bo/bob_logo_hash.png",
        mime_type="image/png",
        file_size=2048,
        created_at=now,
        updated_at=now,
    )

    # Charlie (id=103, disabling) owns Category 3, Material 3, Asset 3
    cat_charlie = Category(
        id=3,
        user_id=103,
        content_type="commerce",
        name="Charlie Commerce Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    mat_charlie = Material(
        id=3,
        user_id=103,
        category_id=3,
        material_type="outline",
        title="Charlie Commerce Outline",
        content="Charlie draft",
        sources=[],
        created_at=now,
        updated_at=now,
    )
    asset_charlie = Asset(
        id=3,
        user_id=103,
        category_id=3,
        name="charlie.png",
        file_key="library/103/ch/charlie_hash.png",
        mime_type="image/png",
        file_size=512,
        created_at=now,
        updated_at=now,
    )

    db_session = PenetrationTestDbSession(
        users=users,
        categories=[cat_alice, cat_bob, cat_charlie],
        materials=[mat_alice, mat_bob, mat_charlie],
        assets=[asset_alice, asset_bob, asset_charlie],
    )

    app = FastAPI()
    app.include_router(category_router)
    app.include_router(material_router)
    app.include_router(asset_router)

    app.dependency_overrides[get_settings] = lambda: penetration_settings
    app.dependency_overrides[get_session_manager] = lambda: mock_session_manager
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_storage_service] = lambda: mock_storage

    return {
        "app": app,
        "settings": penetration_settings,
        "session_manager": mock_session_manager,
        "storage": mock_storage,
        "db": db_session,
        "users": {
            "alice": alice,
            "bob": bob,
            "charlie": charlie,
            "david": david,
            "eve": eve,
            "admin": admin,
        },
    }


def issue_test_auth_header(
    user: UserAccount,
    settings: Settings,
    session_manager: MockSessionManager,
    *,
    version: int | None = None,
    sid: str = "test-session-id",
) -> dict[str, str]:
    """Helper to issue a valid JWT and register the session in MockSessionManager."""
    ver = version if version is not None else user.version
    pair = create_token_pair(
        user_id=user.id,
        version=ver,
        settings=settings,
        session_id=sid,
    )
    session_manager.add_session(pair.session_id, user.id, ver)
    return {"Authorization": f"Bearer {pair.access_token}"}


# ==============================================================================
# Group 1: Account State & Credential Security Matrix
# ==============================================================================


def test_unauthenticated_request_rejected(penetration_env: dict[str, Any]) -> None:
    """Missing Authorization header must reject request immediately."""
    app = penetration_env["app"]
    with TestClient(app) as client:
        res1 = client.get("/api/categories")
        assert res1.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

        res2 = client.get("/api/materials")
        assert res2.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

        res3 = client.get("/api/assets")
        assert res3.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


def test_invalid_and_tampered_token_rejected(penetration_env: dict[str, Any]) -> None:
    """Tokens with bad signature, expired timestamp, or malformed data return 401."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]

    # 1. Rogue signature
    rogue_payload = {
        "sub": "101",
        "ver": 0,
        "sid": "fake-sid",
        "jti": "fake-jti",
        "typ": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    rogue_token = jwt.encode(
        rogue_payload, "completely-wrong-secret-key-32b!", algorithm="HS256"
    )

    with TestClient(app) as client:
        res = client.get(
            "/api/categories",
            headers={"Authorization": f"Bearer {rogue_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Expired token
    expired_payload = {
        **rogue_payload,
        "exp": int(time.time()) - 10,
    }
    expired_token = jwt.encode(
        expired_payload,
        settings.jwt_access_secret.get_secret_value(),
        algorithm="HS256",
    )
    with TestClient(app) as client:
        res = client.get(
            "/api/categories",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_revoked_session_and_version_mismatch_rejected(
    penetration_env: dict[str, Any],
) -> None:
    """Revoked session in Redis or account version mismatch must yield 401."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    alice: UserAccount = penetration_env["users"]["alice"]

    # 1. Session not registered in session manager (revoked session)
    pair = create_token_pair(
        user_id=alice.id,
        version=alice.version,
        settings=settings,
        session_id="unregistered-session-id",
    )
    with TestClient(app) as client:
        res = client.get(
            "/api/categories",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked or expired" in res.json()["detail"]

    # 2. Version mismatch (account version was incremented after token issuance)
    headers = issue_test_auth_header(alice, settings, sm, version=99)
    with TestClient(app) as client:
        res = client.get("/api/categories", headers=headers)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Credentials have been revoked" in res.json()["detail"]


def test_disabled_account_cannot_access_library(
    penetration_env: dict[str, Any],
) -> None:
    """Disabled user account cannot authenticate to any library endpoint."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    david: UserAccount = penetration_env["users"]["david"]

    headers = issue_test_auth_header(david, settings, sm, sid="david-session")

    with TestClient(app) as client:
        # Category
        res_cat = client.get("/api/categories", headers=headers)
        assert res_cat.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is disabled" in res_cat.json()["detail"]

        # Material
        res_mat = client.get("/api/materials", headers=headers)
        assert res_mat.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is disabled" in res_mat.json()["detail"]

        # Asset
        res_ass = client.get("/api/assets", headers=headers)
        assert res_ass.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is disabled" in res_ass.json()["detail"]


def test_deleted_account_cannot_access_library(
    penetration_env: dict[str, Any],
) -> None:
    """Logically deleted user account cannot authenticate to any library endpoint."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    eve: UserAccount = penetration_env["users"]["eve"]

    headers = issue_test_auth_header(eve, settings, sm, sid="eve-session")

    with TestClient(app) as client:
        # Category
        res_cat = client.get("/api/categories", headers=headers)
        assert res_cat.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is deleted" in res_cat.json()["detail"]

        # Material
        res_mat = client.get("/api/materials", headers=headers)
        assert res_mat.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is deleted" in res_mat.json()["detail"]

        # Asset
        res_ass = client.get("/api/assets", headers=headers)
        assert res_ass.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account is deleted" in res_ass.json()["detail"]


def test_disabling_account_can_access_and_manage_library(
    penetration_env: dict[str, Any],
) -> None:
    """Disabling account retains library access to conclude or clean up assets."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    charlie: UserAccount = penetration_env["users"]["charlie"]

    headers = issue_test_auth_header(charlie, settings, sm, sid="charlie-session")

    with TestClient(app) as client:
        # 1. Can list categories
        res_cat = client.get("/api/categories", headers=headers)
        assert res_cat.status_code == status.HTTP_200_OK
        assert len(res_cat.json()["items"]) == 1

        # 2. Can list materials
        res_mat = client.get("/api/materials", headers=headers)
        assert res_mat.status_code == status.HTTP_200_OK
        assert len(res_mat.json()["items"]) == 1

        # 3. Can list assets
        res_ass = client.get("/api/assets", headers=headers)
        assert res_ass.status_code == status.HTTP_200_OK
        assert len(res_ass.json()["items"]) == 1


# ==============================================================================
# Group 2: Cross-Tenant IDOR Matrix (Attacker Bob attacking Alice)
# ==============================================================================


def test_idor_category_patch_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to patch Alice's Category 1 returns 404 (existence obfuscated)."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-1")

    with TestClient(app) as client:
        res = client.patch(
            "/api/categories/1",
            json={"name": "Hacked Alice Category"},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Category not found"


def test_idor_category_delete_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to delete Alice's Category 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-2")

    with TestClient(app) as client:
        res = client.delete("/api/categories/1", headers=headers)
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Category not found"


def test_idor_material_read_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to read Alice's Material 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-3")

    with TestClient(app) as client:
        res = client.get("/api/materials/1", headers=headers)
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Material not found"


def test_idor_material_patch_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to patch Alice's Material 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-4")

    with TestClient(app) as client:
        res = client.patch(
            "/api/materials/1",
            json={"title": "Hacked Title"},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Material not found"


def test_idor_material_delete_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to delete Alice's Material 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-5")

    with TestClient(app) as client:
        res = client.delete("/api/materials/1", headers=headers)
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Material not found"


def test_idor_material_create_with_victim_category_forbidden(
    penetration_env: dict[str, Any],
) -> None:
    """Bob attempting to link a new material to Alice's Category 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-6")

    with TestClient(app) as client:
        res = client.post(
            "/api/materials",
            json={
                "category_id": 1,
                "material_type": "research",
                "title": "Bob Injected Material",
                "content": "Bob payload",
            },
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Category not found"


def test_idor_material_move_to_victim_category_forbidden(
    penetration_env: dict[str, Any],
) -> None:
    """Bob attempting to move his Material 2 into Alice's Category 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-7")

    with TestClient(app) as client:
        res = client.patch(
            "/api/materials/2",
            json={"category_id": 1},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Target category not found"


def test_idor_material_list_with_victim_category_returns_empty(
    penetration_env: dict[str, Any],
) -> None:
    """Bob querying materials with category_id=1 returns empty list, no leak."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-8")

    with TestClient(app) as client:
        res = client.get("/api/materials?category_id=1", headers=headers)
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []


def test_idor_asset_read_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to read Alice's Asset 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-9")

    with TestClient(app) as client:
        res = client.get("/api/assets/1", headers=headers)
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Asset not found"


def test_idor_asset_delete_forbidden(penetration_env: dict[str, Any]) -> None:
    """Bob attempting to delete Alice's Asset 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-10")

    with TestClient(app) as client:
        res = client.delete("/api/assets/1", headers=headers)
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Asset not found"


def test_idor_asset_upload_to_victim_category_forbidden(
    penetration_env: dict[str, Any],
) -> None:
    """Bob attempting to upload asset to Alice's Category 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-11")

    with TestClient(app) as client:
        res = client.post(
            "/api/assets/upload",
            data={"category_id": 1, "name": "bob_hack.png"},
            files={"file": ("hack.png", b"fake-png-bytes", "image/png")},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Category not found"


def test_idor_asset_copy_victim_asset_forbidden(
    penetration_env: dict[str, Any],
) -> None:
    """Bob attempting to copy Alice's Asset 1 to his own Category 2 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-12")

    with TestClient(app) as client:
        res = client.post(
            "/api/assets/1/copy",
            json={"target_category_id": 2},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Asset not found"


def test_idor_asset_copy_to_victim_category_forbidden(
    penetration_env: dict[str, Any],
) -> None:
    """Bob attempting to copy his Asset 2 into Alice's Category 1 returns 404."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-13")

    with TestClient(app) as client:
        res = client.post(
            "/api/assets/2/copy",
            json={"target_category_id": 1},
            headers=headers,
        )
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert res.json()["detail"] == "Target category not found"


def test_idor_asset_list_with_victim_category_returns_empty(
    penetration_env: dict[str, Any],
) -> None:
    """Bob querying assets with category_id=1 returns empty list."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-14")

    with TestClient(app) as client:
        res = client.get("/api/assets?category_id=1", headers=headers)
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []


# ==============================================================================
# Group 3: Admin Privilege Separation Matrix (Admin cannot access private data)
# ==============================================================================


def test_admin_cannot_access_tenant_category(penetration_env: dict[str, Any]) -> None:
    """Admin role has no backdoor into Alice's Category 1 (returns 404)."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    admin: UserAccount = penetration_env["users"]["admin"]

    headers = issue_test_auth_header(admin, settings, sm, sid="admin-session-1")

    with TestClient(app) as client:
        # Patch
        res_patch = client.patch(
            "/api/categories/1",
            json={"name": "Admin Override"},
            headers=headers,
        )
        assert res_patch.status_code == status.HTTP_404_NOT_FOUND

        # Delete
        res_del = client.delete("/api/categories/1", headers=headers)
        assert res_del.status_code == status.HTTP_404_NOT_FOUND


def test_admin_cannot_access_tenant_material(penetration_env: dict[str, Any]) -> None:
    """Admin role cannot read, update, or delete Alice's Material 1 (returns 404)."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    admin: UserAccount = penetration_env["users"]["admin"]

    headers = issue_test_auth_header(admin, settings, sm, sid="admin-session-2")

    with TestClient(app) as client:
        # Read
        res_get = client.get("/api/materials/1", headers=headers)
        assert res_get.status_code == status.HTTP_404_NOT_FOUND

        # Update
        res_patch = client.patch(
            "/api/materials/1",
            json={"title": "Admin Override Title"},
            headers=headers,
        )
        assert res_patch.status_code == status.HTTP_404_NOT_FOUND

        # Delete
        res_del = client.delete("/api/materials/1", headers=headers)
        assert res_del.status_code == status.HTTP_404_NOT_FOUND


def test_admin_cannot_access_tenant_asset(penetration_env: dict[str, Any]) -> None:
    """Admin role cannot read or delete Alice's Asset 1 (returns 404)."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    admin: UserAccount = penetration_env["users"]["admin"]

    headers = issue_test_auth_header(admin, settings, sm, sid="admin-session-3")

    with TestClient(app) as client:
        # Read
        res_get = client.get("/api/assets/1", headers=headers)
        assert res_get.status_code == status.HTTP_404_NOT_FOUND

        # Delete
        res_del = client.delete("/api/assets/1", headers=headers)
        assert res_del.status_code == status.HTTP_404_NOT_FOUND


def test_admin_list_queries_isolated(penetration_env: dict[str, Any]) -> None:
    """Admin listing categories, materials, or assets returns only admin's own items."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    admin: UserAccount = penetration_env["users"]["admin"]

    headers = issue_test_auth_header(admin, settings, sm, sid="admin-session-4")

    with TestClient(app) as client:
        res_cat = client.get("/api/categories", headers=headers)
        assert res_cat.status_code == status.HTTP_200_OK
        assert res_cat.json()["total"] == 0

        res_mat = client.get("/api/materials", headers=headers)
        assert res_mat.status_code == status.HTTP_200_OK
        assert res_mat.json()["total"] == 0

        res_ass = client.get("/api/assets", headers=headers)
        assert res_ass.status_code == status.HTTP_200_OK
        assert res_ass.json()["total"] == 0


def test_cross_tenant_category_name_independence(
    penetration_env: dict[str, Any],
) -> None:
    """Tenant B can create a category with the identical name as Tenant A's category."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-create-dup")

    with TestClient(app) as client:
        # Tenant A already has "Alice Brand Cat" in "brand"
        # Tenant B creates "Alice Brand Cat" in "brand" -> Should succeed (201 Created)
        res = client.post(
            "/api/categories",
            json={"content_type": "brand", "name": "Alice Brand Cat"},
            headers=headers,
        )
        assert res.status_code == status.HTTP_201_CREATED
        assert res.json()["name"] == "Alice Brand Cat"


def test_cross_tenant_category_deletion_independence(
    penetration_env: dict[str, Any],
) -> None:
    """Tenant A's items in Category 1 never block Bob from deleting Category 2."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    bob: UserAccount = penetration_env["users"]["bob"]

    headers = issue_test_auth_header(bob, settings, sm, sid="bob-session-del-indep")

    with TestClient(app) as client:
        # Bob's Category 2 has Material 2 and Asset 2
        # Delete Material 2 and Asset 2 belonging to Bob
        client.delete("/api/materials/2", headers=headers)
        client.delete("/api/assets/2", headers=headers)

        # Alice's Category 1 still has Material 1 and Asset 1!
        # Bob deletes his Category 2 -> Must succeed (204 No Content)
        res = client.delete("/api/categories/2", headers=headers)
        assert res.status_code == status.HTTP_204_NO_CONTENT


# ==============================================================================
# Group 4: Storage Path Traversal & Injection Defense
# ==============================================================================


def test_asset_upload_filename_traversal_sanitized(
    penetration_env: dict[str, Any],
) -> None:
    """Uploading asset with malicious path traversal in filename is sanitized."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    alice: UserAccount = penetration_env["users"]["alice"]

    headers = issue_test_auth_header(alice, settings, sm, sid="alice-storage-session")

    with TestClient(app) as client:
        res = client.post(
            "/api/assets/upload",
            data={"category_id": 1, "name": "clean_name.png"},
            files={
                "file": (
                    "../../../../etc/passwd.png",
                    b"fake-image-bytes",
                    "image/png",
                )
            },
            headers=headers,
        )
        assert res.status_code == status.HTTP_201_CREATED
        asset_data = res.json()
        # Verify the key is user-scoped and contains no traversal
        assert asset_data["file_key"].startswith("library/101/")
        assert ".." not in asset_data["file_key"]


def test_local_storage_path_traversal_defense() -> None:
    """LocalStorageService strictly rejects directory traversal attempts."""
    with tempfile.TemporaryDirectory() as temp_dir:
        storage = LocalStorageService(temp_dir)

        # 1. Traversal in get_file
        assert storage.get_file("../../etc/passwd") is None
        assert storage.get_file("..\\..\\windows\\system32\\cmd.exe") is None

        # 2. Traversal in delete_file
        assert not storage.delete_file("../../etc/shadow")

        # 3. Traversal in resolve_safe_path raises ValueError directly
        with pytest.raises(ValueError, match="Path traversal attempt detected"):
            storage._resolve_safe_path("../../../outside.txt")


# ==============================================================================
# Group 5: Multi-Tenant Category Referential Deletion Protection
# ==============================================================================


def test_category_referential_deletion_protection(
    penetration_env: dict[str, Any],
) -> None:
    """Category deletion is blocked with 400 when materials or assets exist in it."""
    app = penetration_env["app"]
    settings: Settings = penetration_env["settings"]
    sm: MockSessionManager = penetration_env["session_manager"]
    alice: UserAccount = penetration_env["users"]["alice"]

    headers = issue_test_auth_header(alice, settings, sm, sid="alice-del-session")

    with TestClient(app) as client:
        # Category 1 has Material 1 and Asset 1
        res = client.delete("/api/categories/1", headers=headers)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "referenced by existing library items" in res.json()["detail"]

        # Delete Material 1
        client.delete("/api/materials/1", headers=headers)
        # Still has Asset 1 -> Still blocked
        res2 = client.delete("/api/categories/1", headers=headers)
        assert res2.status_code == status.HTTP_400_BAD_REQUEST

        # Delete Asset 1
        client.delete("/api/assets/1", headers=headers)

        # Now deletion succeeds
        res3 = client.delete("/api/categories/1", headers=headers)
        assert res3.status_code == status.HTTP_204_NO_CONTENT
