"""End-to-end multi-tenant asset lifecycle isolation and integration tests.

Validates complete multi-tenant boundaries for assets, storage service,
and category referential protection:
1. Multi-tenant concurrent asset upload into isolated private categories.
2. Cross-tenant category hijacking defense: Alice cannot upload to Bob's category (404).
3. Cross-tenant IDOR attack defense: Bob cannot read or delete Alice's asset (404).
4. Cross-tenant copy defense:
   - Bob cannot copy Alice's asset (404).
   - Alice cannot copy her asset into Bob's category (404).
5. Copy independence and deletion isolation:
   - Alice copies asset across categories; deleting original does not affect copy.
6. Category referential protection:
   - Category deletion blocked (400) until all assets inside are deleted.
"""

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
from formaai.modules.library.models import Asset, Category, Material
from formaai.modules.library.router import (
    asset_router,
    category_router,
    material_router,
)


class MultiTenantAssetDbSession:
    """In-memory DB session mimicking MySQL across multi-tenant library data."""

    def __init__(
        self,
        categories: list[Category] | None = None,
        materials: list[Material] | None = None,
        assets: list[Asset] | None = None,
    ) -> None:
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

        if entity is Asset:
            # Check for asset reference (check_category_in_use)
            if category_id is not None and target_id is None:
                for a in self.assets:
                    if a.category_id == category_id:
                        return a.id
                return None

            # Single asset lookup
            for a in self.assets:
                if target_id is not None and a.id != target_id:
                    continue
                if user_id is not None and a.user_id != user_id:
                    continue
                return a
            return None

        if entity is Material:
            # Check for material reference (check_category_in_use)
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

        class ScalarsResult:
            def __init__(self, items: list[Asset]) -> None:
                self._items = items

            def all(self) -> list[Asset]:
                return self._items

        return ScalarsResult(filtered_a)

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
        if instance in self._pending:
            self._pending.remove(instance)

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
def shared_db() -> MultiTenantAssetDbSession:
    """Single shared in-memory database instance across multi-tenant tests."""
    return MultiTenantAssetDbSession()


@pytest.fixture
def shared_storage() -> MockStorageService:
    """Single shared mock storage service instance."""
    return MockStorageService()


@pytest.fixture
def user_alice() -> UserAccount:
    """Tenant A: Alice (id=101)."""
    user = UserAccount()
    user.id = 101
    user.email = "alice@example.com"
    user.role = "user"
    user.status = "active"
    return user


@pytest.fixture
def user_bob() -> UserAccount:
    """Tenant B: Bob (id=102)."""
    user = UserAccount()
    user.id = 102
    user.email = "bob@example.com"
    user.role = "user"
    user.status = "active"
    return user


def create_tenant_client(
    user: UserAccount | None,
    db: MultiTenantAssetDbSession,
    storage: MockStorageService,
) -> TestClient:
    """Create a TestClient with multi-tenant dependency overrides."""
    app = FastAPI()
    app.include_router(category_router)
    app.include_router(material_router)
    app.include_router(asset_router)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_e2e_multi_tenant_concurrent_asset_upload(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies Alice and Bob can concurrently upload assets without collision."""
    client_alice = create_tenant_client(user_alice, shared_db, shared_storage)
    client_bob = create_tenant_client(user_bob, shared_db, shared_storage)

    # Alice creates a brand category
    cat_res_a = client_alice.post(
        "/api/categories",
        json={"name": "Alice Diagrams", "content_type": "brand"},
    )
    assert cat_res_a.status_code == status.HTTP_201_CREATED
    cat_a_id = cat_res_a.json()["id"]

    # Bob creates a brand category
    cat_res_b = client_bob.post(
        "/api/categories",
        json={"name": "Bob Blueprints", "content_type": "brand"},
    )
    assert cat_res_b.status_code == status.HTTP_201_CREATED
    cat_b_id = cat_res_b.json()["id"]

    # Alice uploads an asset
    upload_res_a = client_alice.post(
        "/api/assets/upload",
        data={"category_id": cat_a_id},
        files={"file": ("diagram.png", b"ALICE_DIAGRAM_DATA", "image/png")},
    )
    assert upload_res_a.status_code == status.HTTP_201_CREATED
    data_a = upload_res_a.json()
    assert data_a["category_id"] == cat_a_id
    assert data_a["name"] == "diagram.png"
    assert data_a["file_size"] == len(b"ALICE_DIAGRAM_DATA")
    assert shared_db.assets[0].user_id == 101

    # Bob uploads an asset
    upload_res_b = client_bob.post(
        "/api/assets/upload",
        data={"category_id": cat_b_id},
        files={"file": ("blueprint.png", b"BOB_BLUEPRINT_DATA", "image/png")},
    )
    assert upload_res_b.status_code == status.HTTP_201_CREATED
    data_b = upload_res_b.json()
    assert data_b["category_id"] == cat_b_id
    assert data_b["name"] == "blueprint.png"
    assert shared_db.assets[1].user_id == 102

    # Query verification: Alice only sees hers, Bob only sees his
    list_a = client_alice.get("/api/assets").json()
    assert list_a["total"] == 1
    assert list_a["items"][0]["id"] == data_a["id"]

    list_b = client_bob.get("/api/assets").json()
    assert list_b["total"] == 1
    assert list_b["items"][0]["id"] == data_b["id"]


def test_e2e_cross_tenant_upload_hijacking_defense(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies Alice cannot upload an asset into Bob's category (404 defense)."""
    client_alice = create_tenant_client(user_alice, shared_db, shared_storage)
    client_bob = create_tenant_client(user_bob, shared_db, shared_storage)

    # Bob creates a category
    cat_b = client_bob.post(
        "/api/categories",
        json={"name": "Bob Vault", "content_type": "brand"},
    ).json()
    bob_cat_id = cat_b["id"]

    # Alice attempts to upload into Bob's category
    res = client_alice.post(
        "/api/assets/upload",
        data={"category_id": bob_cat_id},
        files={"file": ("exploit.png", b"EXPLOIT_PAYLOAD", "image/png")},
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Category not found"

    # Verify no asset was created in Bob's category or under Alice
    assert len(shared_db.assets) == 0


def test_e2e_cross_tenant_idor_defense_read_and_delete(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies Bob cannot read or delete Alice's assets via IDOR attacks."""
    client_alice = create_tenant_client(user_alice, shared_db, shared_storage)
    client_bob = create_tenant_client(user_bob, shared_db, shared_storage)

    # Alice creates category & uploads asset
    cat_a = client_alice.post(
        "/api/categories",
        json={"name": "Alice Private", "content_type": "brand"},
    ).json()
    asset_a = client_alice.post(
        "/api/assets/upload",
        data={"category_id": cat_a["id"]},
        files={"file": ("private.png", b"CONFIDENTIAL_DATA", "image/png")},
    ).json()
    asset_a_id = asset_a["id"]

    # Bob attempts IDOR read
    read_res = client_bob.get(f"/api/assets/{asset_a_id}")
    assert read_res.status_code == status.HTTP_404_NOT_FOUND
    assert read_res.json()["detail"] == "Asset not found"

    # Bob attempts IDOR delete
    del_res = client_bob.delete(f"/api/assets/{asset_a_id}")
    assert del_res.status_code == status.HTTP_404_NOT_FOUND
    assert del_res.json()["detail"] == "Asset not found"

    # Alice can still access her asset unharmed
    verify_res = client_alice.get(f"/api/assets/{asset_a_id}")
    assert verify_res.status_code == status.HTTP_200_OK
    assert verify_res.json()["name"] == "private.png"


def test_e2e_cross_tenant_copy_defense(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies cross-tenant copy attacks are strictly rejected."""
    client_alice = create_tenant_client(user_alice, shared_db, shared_storage)
    client_bob = create_tenant_client(user_bob, shared_db, shared_storage)

    # Alice sets up category & asset
    cat_a = client_alice.post(
        "/api/categories",
        json={"name": "Alice Source", "content_type": "brand"},
    ).json()
    asset_a = client_alice.post(
        "/api/assets/upload",
        data={"category_id": cat_a["id"]},
        files={"file": ("source.png", b"SOURCE_IMAGE", "image/png")},
    ).json()
    asset_a_id = asset_a["id"]

    # Bob sets up category
    cat_b = client_bob.post(
        "/api/categories",
        json={"name": "Bob Target", "content_type": "brand"},
    ).json()
    cat_b_id = cat_b["id"]

    # Attack 1: Bob tries to copy Alice's asset into Bob's category
    bob_copy_res = client_bob.post(
        f"/api/assets/{asset_a_id}/copy",
        json={"target_category_id": cat_b_id},
    )
    assert bob_copy_res.status_code == status.HTTP_404_NOT_FOUND
    assert bob_copy_res.json()["detail"] == "Asset not found"

    # Attack 2: Alice tries to copy her asset into Bob's category
    alice_copy_res = client_alice.post(
        f"/api/assets/{asset_a_id}/copy",
        json={"target_category_id": cat_b_id},
    )
    assert alice_copy_res.status_code == status.HTTP_404_NOT_FOUND
    assert alice_copy_res.json()["detail"] == "Target category not found"

    # Verify no rogue copy was created
    assert len(shared_db.assets) == 1


def test_e2e_asset_copy_independence_and_lifecycle(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
) -> None:
    """Verifies that copies are independent and deletion does not break copy."""
    client = create_tenant_client(user_alice, shared_db, shared_storage)

    # Alice creates Category A1 and Category A2
    cat_1 = client.post(
        "/api/categories",
        json={"name": "Folder 1", "content_type": "brand"},
    ).json()
    cat_2 = client.post(
        "/api/categories",
        json={"name": "Folder 2", "content_type": "brand"},
    ).json()

    # Upload original asset to Category A1
    orig = client.post(
        "/api/assets/upload",
        data={"category_id": cat_1["id"]},
        files={"file": ("shared_pic.jpg", b"PIC_BYTES", "image/jpeg")},
    ).json()
    orig_id = orig["id"]
    file_key = orig["file_key"]

    # Copy asset from Category A1 to Category A2
    copy_res = client.post(
        f"/api/assets/{orig_id}/copy",
        json={"target_category_id": cat_2["id"]},
    )
    assert copy_res.status_code == status.HTTP_201_CREATED
    copy_data = copy_res.json()
    copy_id = copy_data["id"]

    assert copy_id != orig_id
    assert copy_data["category_id"] == cat_2["id"]
    assert copy_data["file_key"] == file_key
    assert copy_data["name"] == "shared_pic.jpg"

    # Deleting original asset in Folder 1
    del_res = client.delete(f"/api/assets/{orig_id}")
    assert del_res.status_code == status.HTTP_204_NO_CONTENT

    # Original is gone
    assert client.get(f"/api/assets/{orig_id}").status_code == status.HTTP_404_NOT_FOUND

    # Copied asset in Folder 2 remains perfectly valid and accessible
    get_copy = client.get(f"/api/assets/{copy_id}")
    assert get_copy.status_code == status.HTTP_200_OK
    assert get_copy.json()["id"] == copy_id
    assert get_copy.json()["category_id"] == cat_2["id"]


def test_e2e_category_referential_protection_with_assets(
    shared_db: MultiTenantAssetDbSession,
    shared_storage: MockStorageService,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies category deletion is blocked if containing assets until cleared."""
    client_alice = create_tenant_client(user_alice, shared_db, shared_storage)
    client_bob = create_tenant_client(user_bob, shared_db, shared_storage)

    # Alice creates Category A1 (with asset) and Category A2 (empty)
    cat_a1 = client_alice.post(
        "/api/categories",
        json={"name": "Alice With Assets", "content_type": "brand"},
    ).json()
    cat_a2 = client_alice.post(
        "/api/categories",
        json={"name": "Alice Empty", "content_type": "brand"},
    ).json()

    # Bob creates Category B1 (with asset)
    cat_b1 = client_bob.post(
        "/api/categories",
        json={"name": "Bob Vault", "content_type": "brand"},
    ).json()

    # Upload asset into A1 and B1
    asset_a = client_alice.post(
        "/api/assets/upload",
        data={"category_id": cat_a1["id"]},
        files={"file": ("asset_a.png", b"BYTES_A", "image/png")},
    ).json()
    client_bob.post(
        "/api/assets/upload",
        data={"category_id": cat_b1["id"]},
        files={"file": ("asset_b.png", b"BYTES_B", "image/png")},
    )

    # Attempt to delete Category A1 -> 400 Bad Request
    del_a1_fail = client_alice.delete(f"/api/categories/{cat_a1['id']}")
    assert del_a1_fail.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        del_a1_fail.json()["detail"]
        == "Cannot delete category: it is referenced by existing library items"
    )

    # Attempt to delete Category A2 (empty) -> 204 No Content
    del_a2_ok = client_alice.delete(f"/api/categories/{cat_a2['id']}")
    assert del_a2_ok.status_code == status.HTTP_204_NO_CONTENT

    # Alice deletes the asset inside A1
    del_asset_ok = client_alice.delete(f"/api/assets/{asset_a['id']}")
    assert del_asset_ok.status_code == status.HTTP_204_NO_CONTENT

    # Now Alice can delete Category A1 -> 204 No Content
    del_a1_ok = client_alice.delete(f"/api/categories/{cat_a1['id']}")
    assert del_a1_ok.status_code == status.HTTP_204_NO_CONTENT

    # Bob's Category B1 and his asset remain completely intact
    bob_check = client_bob.get("/api/categories?content_type=brand")
    assert bob_check.status_code == status.HTTP_200_OK
    assert len(bob_check.json()["items"]) == 1
    assert bob_check.json()["items"][0]["id"] == cat_b1["id"]
