"""End-to-end multi-tenant lifecycle isolation and integration tests.

Validates complete multi-tenant boundaries during update and delete:
1. Cross-tenant IDOR attack defense: Bob cannot PATCH Alice's category (404).
2. Cross-tenant IDOR attack defense: Bob cannot DELETE Alice's category (404).
3. Cross-tenant rename independence: Bob can rename to Alice's category name.
4. Intra-tenant rename duplicate defense: Bob cannot rename to his own duplicate (409).
5. Cross-tenant deletion data integrity: Alice's deletion never affects Bob.
6. Multi-tenant sort_order isolation: reordering categories affects only owner.
"""

import operator
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from formaai.modules.identity.dependencies import get_current_user, get_db
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Category
from formaai.modules.library.router import (
    category_router,
    get_category_usage_checker,
)


class MultiTenantLifecycleDbSession:
    """In-memory DB session mimicking MySQL across multi-tenant lifecycle actions."""

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
        cat_id = None
        exclude_id = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                op = getattr(expr, "operator", None)
                if col == "user_id":
                    user_id = val
                elif col == "content_type":
                    content_type = val
                elif col == "name":
                    name = val
                elif col == "id":
                    if op is operator.ne:
                        exclude_id = val
                    else:
                        cat_id = val

        for c in self.categories:
            if cat_id is not None:
                if c.id == cat_id and (user_id is None or c.user_id == user_id):
                    return c
                continue

            if (
                c.user_id == user_id
                and c.content_type == content_type
                and c.name.lower() == str(name).lower()
                and (exclude_id is None or c.id != exclude_id)
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

    def delete(self, category: Category) -> None:
        if category in self.categories:
            self.categories.remove(category)
        if category in self._pending:
            self._pending.remove(category)

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
                        statement="INSERT/UPDATE category...",
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
def shared_db() -> MultiTenantLifecycleDbSession:
    """Single shared in-memory DB session across multiple tenants."""
    return MultiTenantLifecycleDbSession()


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
    db: MultiTenantLifecycleDbSession,
    usage_checker: Callable[[Session, int], bool] | None = None,
) -> TestClient:
    """Create a TestClient simulating requests from an authenticated user."""
    app = FastAPI()
    app.include_router(category_router)

    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    if usage_checker is not None:
        app.dependency_overrides[get_category_usage_checker] = lambda: usage_checker

    return TestClient(app)


def test_e2e_cross_tenant_idor_patch_shield(
    shared_db: MultiTenantLifecycleDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that Bob cannot PATCH Alice's category; returns 404 stealthily."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # 1. Alice creates category (id=1)
    res_a = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Alice Vault"}
    )
    assert res_a.status_code == status.HTTP_201_CREATED
    cat_id = res_a.json()["id"]

    # 2. Bob attempts to PATCH Alice's category
    res_idor = client_bob.patch(
        f"/api/categories/{cat_id}",
        json={"name": "Bob Injected"},
    )
    assert res_idor.status_code == status.HTTP_404_NOT_FOUND
    assert res_idor.json()["detail"] == "Category not found"

    # 3. Verify Alice's category remains completely untouched
    res_get = client_alice.get("/api/categories?content_type=commerce").json()
    assert res_get["total"] == 1
    assert res_get["items"][0]["name"] == "Alice Vault"


def test_e2e_cross_tenant_idor_delete_shield(
    shared_db: MultiTenantLifecycleDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that Bob cannot DELETE Alice's category; returns 404 stealthily."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # 1. Alice creates category
    res_a = client_alice.post(
        "/api/categories", json={"content_type": "brand", "name": "Alice Brand"}
    )
    assert res_a.status_code == status.HTTP_201_CREATED
    cat_id = res_a.json()["id"]

    # 2. Bob attempts to DELETE Alice's category
    res_del = client_bob.delete(f"/api/categories/{cat_id}")
    assert res_del.status_code == status.HTTP_404_NOT_FOUND
    assert res_del.json()["detail"] == "Category not found"

    # 3. Alice's category is still present
    res_get = client_alice.get("/api/categories?content_type=brand").json()
    assert res_get["total"] == 1
    assert res_get["items"][0]["name"] == "Alice Brand"


def test_e2e_cross_tenant_rename_independence(
    shared_db: MultiTenantLifecycleDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies Bob can rename to Alice's category name without 409 collision."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice has "Electronics"
    client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Electronics"}
    )

    # Bob has "Gadgets" and "Apparel"
    res_b1 = client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Gadgets"}
    )
    b1_id = res_b1.json()["id"]

    client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Apparel"}
    )

    # 1. Bob renames "Gadgets" to "Electronics" (same as Alice's) -> Allowed!
    res_rename = client_bob.patch(
        f"/api/categories/{b1_id}",
        json={"name": "Electronics"},
    )
    assert res_rename.status_code == status.HTTP_200_OK
    assert res_rename.json()["name"] == "Electronics"

    # 2. Bob attempts to rename "Gadgets" to "Apparel" (Bob's own duplicate) -> 409
    res_conflict = client_bob.patch(
        f"/api/categories/{b1_id}",
        json={"name": "Apparel"},
    )
    assert res_conflict.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res_conflict.json()["detail"]


def test_e2e_cross_tenant_deletion_data_integrity(
    shared_db: MultiTenantLifecycleDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies that Alice deleting her category never alters Bob's dataset."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice creates 3 categories
    a1 = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "A1"}
    ).json()["id"]
    client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "A2"}
    )
    client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "A3"}
    )

    # Bob creates 2 categories
    client_bob.post("/api/categories", json={"content_type": "commerce", "name": "B1"})
    client_bob.post("/api/categories", json={"content_type": "commerce", "name": "B2"})

    # Alice deletes A1
    res_del = client_alice.delete(f"/api/categories/{a1}")
    assert res_del.status_code == status.HTTP_204_NO_CONTENT

    # Alice list now has 2 items
    alice_list = client_alice.get("/api/categories").json()
    assert alice_list["total"] == 2
    assert [item["name"] for item in alice_list["items"]] == ["A2", "A3"]

    # Bob list still has exactly 2 items, unchanged
    bob_list = client_bob.get("/api/categories").json()
    assert bob_list["total"] == 2
    assert [item["name"] for item in bob_list["items"]] == ["B1", "B2"]

    # Alice deleting A1 again -> 404
    res_del_again = client_alice.delete(f"/api/categories/{a1}")
    assert res_del_again.status_code == status.HTTP_404_NOT_FOUND


def test_e2e_multi_tenant_reorder_and_lifecycle_flow(
    shared_db: MultiTenantLifecycleDbSession,
    user_alice: UserAccount,
    user_bob: UserAccount,
) -> None:
    """Verifies complete lifecycle flow: POST -> PATCH sort_order -> GET -> DELETE."""
    client_alice = create_tenant_client(user_alice, shared_db)
    client_bob = create_tenant_client(user_bob, shared_db)

    # Alice creates two categories: Cat 1 (sort 0), Cat 2 (sort 1)
    c1_id = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Cat 1"}
    ).json()["id"]
    c2_id = client_alice.post(
        "/api/categories", json={"content_type": "commerce", "name": "Cat 2"}
    ).json()["id"]

    # Bob creates one category: Cat Bob (sort 0)
    client_bob.post(
        "/api/categories", json={"content_type": "commerce", "name": "Cat Bob"}
    )

    # Alice reorders Cat 2 to sort_order 0, and renames Cat 2 to Cat 2 Premium
    res_patch = client_alice.patch(
        f"/api/categories/{c2_id}",
        json={"name": "Cat 2 Premium", "sort_order": 0},
    )
    assert res_patch.status_code == status.HTTP_200_OK

    # Query Alice's categories: Cat 1 (id=1, sort=0), Cat 2 Premium (id=2, sort=0)
    # Ordered by sort_order ASC, id ASC -> Cat 1 then Cat 2 Premium
    alice_items = client_alice.get("/api/categories?content_type=commerce").json()[
        "items"
    ]
    assert len(alice_items) == 2
    assert alice_items[0]["id"] == c1_id
    assert alice_items[1]["id"] == c2_id
    assert alice_items[1]["name"] == "Cat 2 Premium"

    # Bob's category is still Cat Bob with sort_order 0
    bob_items = client_bob.get("/api/categories?content_type=commerce").json()["items"]
    assert len(bob_items) == 1
    assert bob_items[0]["name"] == "Cat Bob"
    assert bob_items[0]["sort_order"] == 0

    # Alice cleans up Cat 1 and Cat 2 Premium
    assert (
        client_alice.delete(f"/api/categories/{c1_id}").status_code
        == status.HTTP_204_NO_CONTENT
    )
    assert (
        client_alice.delete(f"/api/categories/{c2_id}").status_code
        == status.HTTP_204_NO_CONTENT
    )
    assert client_alice.get("/api/categories").json()["total"] == 0

    # Bob still has his category
    assert client_bob.get("/api/categories").json()["total"] == 1
