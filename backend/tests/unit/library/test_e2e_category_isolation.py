"""End-to-end multi-tenant isolation and integration tests for Category API.

Validates complete multi-tenant boundaries and lifecycle:
1. Multi-tenant concurrent creation of identical names without collisions.
2. Per-tenant duplicate conflict detection (409 Conflict) strictly isolated.
3. Multi-tenant independent sort_order sequences per content_type.
4. Complete query zero-data-leakage across tenants under all filter options.
5. Cross-tenant tampering defense: extra fields (e.g. user_id) rejected (422).
6. Authentication boundary shield: unauthenticated requests rejected (401).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from formaai.modules.identity.dependencies import get_current_user, get_db
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Category
from formaai.modules.library.router import category_router


class MultiTenantCategoryDbSession:
    """In-memory DB session mimicking MySQL behavior across multi-tenant categories."""

    def __init__(self, categories: list[Category] | None = None) -> None:
        self.categories: list[Category] = categories if categories is not None else []
        self._next_id = max([c.id for c in self.categories], default=0) + 1
        self._pending: list[Category] = []

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )

        stmt_str = str(stmt).lower()
        if "max" in stmt_str:
            content_type = None
            user_id = None
            for expr in clauses:
                if hasattr(expr, "left") and hasattr(expr, "right"):
                    if expr.left.name == "content_type":
                        content_type = expr.right.value
                    elif expr.left.name == "user_id":
                        user_id = expr.right.value
            matches = [
                c.sort_order
                for c in self.categories
                if c.user_id == user_id and c.content_type == content_type
            ]
            return max(matches) if matches else None

        user_id = None
        content_type = None
        name = None
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = expr.left.name
                val = expr.right.value
                if col == "user_id":
                    user_id = val
                elif col == "content_type":
                    content_type = val
                elif col == "name":
                    name = val

        for c in self.categories:
            if (
                c.user_id == user_id
                and c.content_type == content_type
                and c.name.lower() == str(name).lower()
            ):
                return c
            if (
                c.user_id == user_id
                and content_type is None
                and c.name.lower() == str(name).lower()
            ):
                return c
        return None

    def scalars(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        clauses = (
            (whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause])
            if whereclause is not None
            else []
        )

        user_id = None
        content_type = None
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = expr.left.name
                val = expr.right.value
                if col == "user_id":
                    user_id = val
                elif col == "content_type":
                    content_type = val

        filtered = [
            c
            for c in self.categories
            if c.user_id == user_id
            and (content_type is None or c.content_type == content_type)
        ]
        filtered.sort(key=lambda c: (c.sort_order, c.id))

        class ScalarsResult:
            def __init__(self, items: list[Category]) -> None:
                self._items = items

            def all(self) -> list[Category]:
                return self._items

        return ScalarsResult(filtered)

    def add(self, category: Category) -> None:
        if category.id is None:
            category.id = self._next_id
            self._next_id += 1
        if category.created_at is None:
            category.created_at = datetime.now(UTC)
        if category.updated_at is None:
            category.updated_at = datetime.now(UTC)
        self._pending.append(category)

    def commit(self) -> None:
        for c in self._pending:
            for existing in self.categories:
                if (
                    existing.user_id == c.user_id
                    and existing.content_type == c.content_type
                    and existing.name.lower() == c.name.lower()
                    and existing.id != c.id
                ):
                    raise IntegrityError(
                        statement="INSERT INTO category...",
                        params={},
                        orig=Exception("Duplicate entry for key"),
                    )
            self.categories.append(c)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()

    def refresh(self, category: Category) -> None:
        pass


@pytest.fixture
def shared_db() -> MultiTenantCategoryDbSession:
    """Single shared in-memory database instance shared across all tenants."""
    return MultiTenantCategoryDbSession()


@pytest.fixture
def user_alice() -> UserAccount:
    """Tenant A: Alice (user_id=101)."""
    user = UserAccount()
    user.id = 101
    user.email = "alice@example.com"
    user.role = "user"
    user.status = "active"
    return user


@pytest.fixture
def user_bob() -> UserAccount:
    """Tenant B: Bob (user_id=102)."""
    user = UserAccount()
    user.id = 102
    user.email = "bob@example.com"
    user.role = "user"
    user.status = "active"
    return user


def create_tenant_client(
    user: UserAccount | None, db: MultiTenantCategoryDbSession
) -> TestClient:
    """Create a TestClient simulating requests from a specific authenticated user."""
    app = FastAPI()
    app.include_router(category_router)

    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_e2e_multi_tenant_concurrent_same_name_creation(
    shared_db: MultiTenantCategoryDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Demonstrates Alice and Bob can concurrently create identical category names."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # 1. Alice creates "Electronics" under "commerce"
    payload = {"content_type": "commerce", "name": "Electronics"}
    res_a = client_alice.post("/api/categories", json=payload)
    assert res_a.status_code == status.HTTP_201_CREATED
    data_a = res_a.json()
    assert data_a["name"] == "Electronics"
    assert data_a["sort_order"] == 0

    # 2. Bob creates identical "Electronics" under "commerce" -> MUST succeed
    res_b = client_bob.post("/api/categories", json=payload)
    assert res_b.status_code == status.HTTP_201_CREATED
    data_b = res_b.json()
    assert data_b["name"] == "Electronics"
    assert data_b["sort_order"] == 0  # Bob's first category also starts at 0!
    assert data_b["id"] != data_a["id"]  # Distinct database records

    # 3. Alice attempting to create duplicate "Electronics" -> 409 Conflict
    res_a_dup = client_alice.post("/api/categories", json=payload)
    assert res_a_dup.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res_a_dup.json()["detail"]

    # 4. Bob attempting to create duplicate "electronics" -> 409 Conflict
    res_b_dup = client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "electronics"}
    )
    assert res_b_dup.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res_b_dup.json()["detail"]


def test_e2e_multi_tenant_independent_sort_order_sequences(
    shared_db: MultiTenantCategoryDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Demonstrates sort_order sequences are completely isolated per tenant."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice creates 3 categories in commerce
    alice_cats = ["Phones", "Laptops", "Tablets"]
    alice_orders = []
    for name in alice_cats:
        res = client_alice.post(
            "/api/categories", json={"content_type": "commerce", "name": name}
        )
        assert res.status_code == status.HTTP_201_CREATED
        alice_orders.append(res.json()["sort_order"])
    assert alice_orders == [0, 1, 2]

    # Bob creates 2 categories in commerce
    bob_cats = ["Apparel", "Footwear"]
    bob_orders = []
    for name in bob_cats:
        res = client_bob.post(
            "/api/categories", json={"content_type": "commerce", "name": name}
        )
        assert res.status_code == status.HTTP_201_CREATED
        bob_orders.append(res.json()["sort_order"])
    # Bob's sequence must start at 0, not contaminated by Alice's max=2
    assert bob_orders == [0, 1]

    # Alice creates a 4th category -> must increment to 3
    res_a_4 = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Accessories"}
    )
    assert res_a_4.status_code == status.HTTP_201_CREATED
    assert res_a_4.json()["sort_order"] == 3


def test_e2e_multi_tenant_zero_data_leakage(
    shared_db: MultiTenantCategoryDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that queries return strictly only the authenticated tenant's data."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Seed Alice's categories
    client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Alice Product"}
    )
    client_alice.post(
        "/api/categories", json={"content_type": "brand", "name": "Alice Brand"}
    )

    # Seed Bob's categories
    client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Bob Product"}
    )
    client_bob.post(
        "/api/categories", json={"content_type": "knowledge", "name": "Bob Guide"}
    )

    # Alice queries commerce
    alice_com = client_alice.get("/api/categories?content_type=commerce").json()
    assert alice_com["total"] == 1
    assert [item["name"] for item in alice_com["items"]] == ["Alice Product"]

    # Bob queries commerce
    bob_com = client_bob.get("/api/categories?content_type=commerce").json()
    assert bob_com["total"] == 1
    assert [item["name"] for item in bob_com["items"]] == ["Bob Product"]

    # Alice queries all (unfiltered)
    alice_all = client_alice.get("/api/categories").json()
    assert alice_all["total"] == 2
    alice_names = {item["name"] for item in alice_all["items"]}
    assert alice_names == {"Alice Product", "Alice Brand"}
    assert "Bob Product" not in alice_names
    assert "Bob Guide" not in alice_names

    # Bob queries all (unfiltered)
    bob_all = client_bob.get("/api/categories").json()
    assert bob_all["total"] == 2
    bob_names = {item["name"] for item in bob_all["items"]}
    assert bob_names == {"Bob Product", "Bob Guide"}
    assert "Alice Product" not in bob_names
    assert "Alice Brand" not in bob_names


def test_e2e_adversarial_user_id_tamper_shield(
    shared_db: MultiTenantCategoryDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Tests schema rejection of injected user_id and strict session tenancy."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # 1. Alice malicious attempt: inject user_id = 102 in body
    # Pydantic schema configured with extra="forbid" strictly rejects unexpected fields
    malicious_payload = {
        "content_type": "commerce",
        "name": "Spoofed Category",
        "user_id": 102,
    }
    res = client_alice.post("/api/categories", json=malicious_payload)
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # 2. Legitimate creation by Alice must strictly bind to Alice's id in database
    legit_payload = {
        "content_type": "commerce",
        "name": "Legit Alice Category",
    }
    res_legit = client_alice.post("/api/categories", json=legit_payload)
    assert res_legit.status_code == status.HTTP_201_CREATED

    saved_cat = next(
        c for c in shared_db.categories if c.name == "Legit Alice Category"
    )
    assert saved_cat.user_id == 101
    assert saved_cat.user_id != 102

    # Verify Bob listing categories does NOT see Alice's category
    bob_list = client_bob.get("/api/categories").json()
    assert bob_list["total"] == 0


def test_e2e_unauthenticated_request_shield(
    shared_db: MultiTenantCategoryDbSession,
) -> None:
    """Verifies unauthenticated requests are strictly rejected with 401."""
    anonymous_client = create_tenant_client(None, shared_db)

    res_get = anonymous_client.get("/api/categories")
    assert res_get.status_code == status.HTTP_401_UNAUTHORIZED

    res_post = anonymous_client.post(
        "/api/categories", json={"content_type": "commerce", "name": "Illegal Cat"}
    )
    assert res_post.status_code == status.HTTP_401_UNAUTHORIZED
