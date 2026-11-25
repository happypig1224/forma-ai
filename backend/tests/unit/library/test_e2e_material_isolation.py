"""End-to-end multi-tenant material lifecycle isolation and integration tests.

Validates complete multi-tenant boundaries for materials and category protection:
1. Multi-tenant concurrent material creation in isolated private categories.
2. Cross-tenant category hijacking defense: Alice cannot bind to Bob's category (404).
3. Cross-tenant IDOR attack defense: Bob cannot read, edit, or delete Alice's material.
4. Zero-data-leakage queries: list filters return strictly authenticated tenant data.
5. Category deletion referential protection: referenced category deletion is blocked,
   while independent tenant's empty category deletion succeeds unimpeded.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from formaai.modules.identity.dependencies import get_current_user, get_db
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Asset, Category, Material
from formaai.modules.library.router import category_router, material_router


class MultiTenantMaterialDbSession:
    """In-memory DB session mimicking MySQL across multi-tenant data."""

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
            for a in self.assets:
                if category_id is not None and a.category_id == category_id:
                    return a.id
            return None

        if entity is Material:
            # Query for material reference (check_category_in_use)
            if category_id is not None and target_id is None:
                for m in self.materials:
                    if m.category_id == category_id:
                        return m.id
                return None

            # Query for single material
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
        material_type = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = expr.left.name
                val = expr.right.value
                if col == "user_id":
                    user_id = val
                elif col == "category_id":
                    category_id = val
                elif col == "material_type":
                    material_type = val

        filtered = [
            m
            for m in self.materials
            if (user_id is None or m.user_id == user_id)
            and (category_id is None or m.category_id == category_id)
            and (material_type is None or m.material_type == material_type)
        ]
        filtered.sort(key=lambda m: (m.created_at, m.id), reverse=True)

        class ScalarsResult:
            def __init__(self, items: list[Material]) -> None:
                self._items = items

            def all(self) -> list[Material]:
                return self._items

        return ScalarsResult(filtered)

    def add(self, instance: Any) -> None:
        if isinstance(instance, Material):
            if instance.id is None:
                instance.id = self._next_mat_id
                self._next_mat_id += 1
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
            if instance.updated_at is None:
                instance.updated_at = datetime.now(UTC)
        elif isinstance(instance, Category):
            if instance.id is None:
                instance.id = self._next_cat_id
                self._next_cat_id += 1
            if instance.created_at is None:
                instance.created_at = datetime.now(UTC)
            if instance.updated_at is None:
                instance.updated_at = datetime.now(UTC)
        self._pending.append(instance)

    def delete(self, instance: Any) -> None:
        if isinstance(instance, Material) and instance in self.materials:
            self.materials.remove(instance)
        elif isinstance(instance, Category) and instance in self.categories:
            self.categories.remove(instance)
        if instance in self._pending:
            self._pending.remove(instance)

    def commit(self) -> None:
        for item in self._pending:
            if isinstance(item, Material) and item not in self.materials:
                self.materials.append(item)
            elif isinstance(item, Category) and item not in self.categories:
                self.categories.append(item)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, instance: Any) -> None:
        pass


@pytest.fixture
def shared_db() -> MultiTenantMaterialDbSession:
    """Single shared in-memory database instance shared across all tenants."""
    return MultiTenantMaterialDbSession()


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
    user: UserAccount | None, db: MultiTenantMaterialDbSession
) -> TestClient:
    """Create a TestClient simulating requests from an authenticated user."""
    app = FastAPI()
    app.include_router(category_router)
    app.include_router(material_router)

    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_e2e_multi_tenant_concurrent_material_creation(
    shared_db: MultiTenantMaterialDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies Alice and Bob can concurrently create materials."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # 1. Alice creates category "Tech" (commerce)
    res_ca = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Tech"}
    )
    cat_alice_id = res_ca.json()["id"]

    # 2. Bob creates category "Tech" (commerce)
    res_cb = client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Tech"}
    )
    cat_bob_id = res_cb.json()["id"]
    assert cat_alice_id != cat_bob_id

    # 3. Alice creates material under her category
    res_ma = client_alice.post(
        "/api/materials",
        json={
            "category_id": cat_alice_id,
            "material_type": "research",
            "title": "Smartphone Market 2026",
            "content": "Deep research on foldables",
            "sources": [{"title": "TechPortal", "url": "https://tech.test"}],
        },
    )
    assert res_ma.status_code == status.HTTP_201_CREATED
    mat_alice_id = res_ma.json()["id"]

    # 4. Bob creates material with identical title under his category
    res_mb = client_bob.post(
        "/api/materials",
        json={
            "category_id": cat_bob_id,
            "material_type": "research",
            "title": "Smartphone Market 2026",
            "content": "Bob independent research",
            "sources": [],
        },
    )
    assert res_mb.status_code == status.HTTP_201_CREATED
    mat_bob_id = res_mb.json()["id"]
    assert mat_alice_id != mat_bob_id


def test_e2e_cross_tenant_category_hijacking_shield(
    shared_db: MultiTenantMaterialDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that Alice cannot bind materials to Bob's category, and vice versa."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Bob creates a category (id=B)
    res_cb = client_bob.post(
        "/api/categories", json={"content_type": "brand", "name": "Bob Brand"}
    )
    bob_cat_id = res_cb.json()["id"]

    # Alice attempts to create material pointing to Bob's category
    res_hijack = client_alice.post(
        "/api/materials",
        json={
            "category_id": bob_cat_id,
            "material_type": "outline",
            "title": "Alice Trojan Material",
            "content": "Malicious payload",
        },
    )
    assert res_hijack.status_code == status.HTTP_404_NOT_FOUND
    assert res_hijack.json()["detail"] == "Category not found"

    # Alice creates a valid category of her own
    res_ca = client_alice.post(
        "/api/categories", json={"content_type": "brand", "name": "Alice Brand"}
    )
    alice_cat_id = res_ca.json()["id"]

    res_alice_mat = client_alice.post(
        "/api/materials",
        json={
            "category_id": alice_cat_id,
            "material_type": "outline",
            "title": "Alice Legit Material",
            "content": "Legit content",
        },
    )
    alice_mat_id = res_alice_mat.json()["id"]

    # Alice attempts to PATCH reassign material to Bob's category -> 404
    res_patch_hijack = client_alice.patch(
        f"/api/materials/{alice_mat_id}",
        json={"category_id": bob_cat_id},
    )
    assert res_patch_hijack.status_code == status.HTTP_404_NOT_FOUND
    assert res_patch_hijack.json()["detail"] == "Target category not found"


def test_e2e_cross_tenant_idor_material_shield(
    shared_db: MultiTenantMaterialDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that Bob cannot read, modify, or delete Alice's material."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice creates category and material
    cat_id = client_alice.post(
        "/api/categories", json={"content_type": "knowledge", "name": "Science"}
    ).json()["id"]

    mat_id = client_alice.post(
        "/api/materials",
        json={
            "category_id": cat_id,
            "material_type": "research",
            "title": "Quantum Computing",
            "content": "Superposition principles",
        },
    ).json()["id"]

    # 1. Bob attempts GET -> 404
    assert (
        client_bob.get(f"/api/materials/{mat_id}").status_code
        == status.HTTP_404_NOT_FOUND
    )

    # 2. Bob attempts PATCH -> 404
    res_patch = client_bob.patch(
        f"/api/materials/{mat_id}", json={"title": "Hacked Title"}
    )
    assert res_patch.status_code == status.HTTP_404_NOT_FOUND

    # 3. Bob attempts DELETE -> 404
    res_del = client_bob.delete(f"/api/materials/{mat_id}")
    assert res_del.status_code == status.HTTP_404_NOT_FOUND

    # 4. Alice verifies material is intact
    res_verify = client_alice.get(f"/api/materials/{mat_id}").json()
    assert res_verify["title"] == "Quantum Computing"


def test_e2e_cross_tenant_query_zero_leakage(
    shared_db: MultiTenantMaterialDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies query filters return strictly authenticated tenant's materials."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice setup
    a_cat = client_alice.post(
        "/api/categories", json={"content_type": "story", "name": "SciFi"}
    ).json()["id"]
    client_alice.post(
        "/api/materials",
        json={
            "category_id": a_cat,
            "material_type": "outline",
            "title": "Mars Colony 2050",
            "content": "Story plot",
        },
    )

    # Bob setup
    b_cat = client_bob.post(
        "/api/categories", json={"content_type": "story", "name": "Fantasy"}
    ).json()["id"]
    client_bob.post(
        "/api/materials",
        json={
            "category_id": b_cat,
            "material_type": "outline",
            "title": "Dragon Realm",
            "content": "Fantasy plot",
        },
    )

    # Alice query
    alice_list = client_alice.get("/api/materials").json()
    assert alice_list["total"] == 1
    assert [m["title"] for m in alice_list["items"]] == ["Mars Colony 2050"]

    # Bob query
    bob_list = client_bob.get("/api/materials").json()
    assert bob_list["total"] == 1
    assert [m["title"] for m in bob_list["items"]] == ["Dragon Realm"]


def test_e2e_category_deletion_protection_multi_tenant_isolation(
    shared_db: MultiTenantMaterialDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies category referential protection works strictly per-tenant."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice creates Category A with Material A
    cat_a = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Alice Cat"}
    ).json()["id"]
    mat_a = client_alice.post(
        "/api/materials",
        json={
            "category_id": cat_a,
            "material_type": "research",
            "title": "Alice Res",
            "content": "Content",
        },
    ).json()["id"]

    # Bob creates Category B (no materials)
    cat_b = client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Bob Cat"}
    ).json()["id"]

    # 1. Alice attempts to delete Category A -> Blocked (400) because it has Material A
    res_a_del = client_alice.delete(f"/api/categories/{cat_a}")
    assert res_a_del.status_code == status.HTTP_400_BAD_REQUEST
    assert "referenced" in res_a_del.json()["detail"]

    # 2. Bob deletes Category B -> Succeeds (204) because Bob's category is unreferenced
    # Alice's referenced category does NOT block Bob!
    res_b_del = client_bob.delete(f"/api/categories/{cat_b}")
    assert res_b_del.status_code == status.HTTP_204_NO_CONTENT
    assert client_bob.get("/api/categories").json()["total"] == 0

    # 3. Alice deletes Material A
    assert (
        client_alice.delete(f"/api/materials/{mat_a}").status_code
        == status.HTTP_204_NO_CONTENT
    )

    # 4. Now Alice's Category A is unreferenced: deletion succeeds!
    res_a_del_now = client_alice.delete(f"/api/categories/{cat_a}")
    assert res_a_del_now.status_code == status.HTTP_204_NO_CONTENT
    assert client_alice.get("/api/categories").json()["total"] == 0
