"""Unit tests for category lifecycle operations (PATCH and DELETE)."""

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


class MockLifecycleCategoryDbSession:
    """In-memory database session mimicking MySQL behavior for category lifecycle."""

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
def current_test_user() -> UserAccount:
    user = UserAccount()
    user.id = 100
    user.email = "owner@formaai.test"
    user.role = "user"
    user.status = "active"
    return user


@pytest.fixture
def other_user() -> UserAccount:
    user = UserAccount()
    user.id = 200
    user.email = "other@formaai.test"
    user.role = "user"
    user.status = "active"
    return user


@pytest.fixture
def mock_db(
    current_test_user: UserAccount, other_user: UserAccount
) -> MockLifecycleCategoryDbSession:
    now = datetime.now(UTC)
    c1 = Category(
        id=1,
        user_id=current_test_user.id,
        content_type="commerce",
        name="Digital 3C",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    c2 = Category(
        id=2,
        user_id=current_test_user.id,
        content_type="commerce",
        name="Beauty",
        sort_order=1,
        created_at=now,
        updated_at=now,
    )
    c3 = Category(
        id=3,
        user_id=current_test_user.id,
        content_type="brand",
        name="Luxury",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    c4 = Category(
        id=4,
        user_id=other_user.id,
        content_type="commerce",
        name="Other Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    return MockLifecycleCategoryDbSession([c1, c2, c3, c4])


def build_test_client(
    user: UserAccount | None,
    db: MockLifecycleCategoryDbSession,
    usage_checker: Callable[[Session, int], bool] | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(category_router)

    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    if usage_checker is not None:
        app.dependency_overrides[get_category_usage_checker] = lambda: usage_checker

    return TestClient(app)


def test_patch_category_unauthorized(mock_db: MockLifecycleCategoryDbSession) -> None:
    client = build_test_client(None, mock_db)
    res = client.patch("/api/categories/1", json={"name": "New Name"})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_delete_category_unauthorized(mock_db: MockLifecycleCategoryDbSession) -> None:
    client = build_test_client(None, mock_db)
    res = client.delete("/api/categories/1")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_patch_category_not_found(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.patch("/api/categories/999", json={"name": "New Name"})
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Category not found"


def test_patch_category_other_user_stealth_not_found(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    # Category 4 belongs to other_user (id=200). Current user is id=100.
    res = client.patch("/api/categories/4", json={"name": "Hacked"})
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Category not found"


def test_patch_category_rename_success(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.patch("/api/categories/1", json={"name": "Consumer Electronics"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["id"] == 1
    assert data["name"] == "Consumer Electronics"
    assert data["sort_order"] == 0


def test_patch_category_rename_idempotent(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    # Renaming to same name (different case) should succeed without 409
    res = client.patch("/api/categories/1", json={"name": "digital 3c"})
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["name"] == "digital 3c"


def test_patch_category_rename_duplicate_conflict(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    # Attempt to rename Cat 1 to "Beauty" (which already exists in commerce)
    res = client.patch("/api/categories/1", json={"name": "Beauty"})
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res.json()["detail"]


def test_patch_category_rename_cross_content_type_allowed(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    # Cat 3 is in "brand". Rename Cat 1 (in commerce) to "Luxury" -> allowed
    res = client.patch("/api/categories/1", json={"name": "Luxury"})
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["name"] == "Luxury"


def test_patch_category_sort_order_only(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.patch("/api/categories/1", json={"sort_order": 99})
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["name"] == "Digital 3C"  # Unchanged
    assert res.json()["sort_order"] == 99


def test_patch_category_both_name_and_sort_order(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.patch(
        "/api/categories/1", json={"name": "Smart Home", "sort_order": 5}
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["name"] == "Smart Home"
    assert res.json()["sort_order"] == 5


def test_patch_category_empty_body_rejected(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.patch("/api/categories/1", json={})
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_delete_category_not_found(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.delete("/api/categories/999")
    assert res.status_code == status.HTTP_404_NOT_FOUND


def test_delete_category_other_user_stealth_not_found(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    # Cat 4 belongs to other_user
    res = client.delete("/api/categories/4")
    assert res.status_code == status.HTTP_404_NOT_FOUND


def test_delete_category_success(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    client = build_test_client(current_test_user, mock_db)
    res = client.delete("/api/categories/1")
    assert res.status_code == status.HTTP_204_NO_CONTENT

    # Verify category 1 is gone from database
    assert not any(c.id == 1 for c in mock_db.categories)

    # Deleting again returns 404
    res_again = client.delete("/api/categories/1")
    assert res_again.status_code == status.HTTP_404_NOT_FOUND


def test_delete_category_referenced_guard_blocks(
    mock_db: MockLifecycleCategoryDbSession, current_test_user: UserAccount
) -> None:
    # Inject usage_checker returning True (simulate referenced by assets)
    client = build_test_client(
        current_test_user, mock_db, usage_checker=lambda _db, _cid: True
    )
    res = client.delete("/api/categories/1")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "referenced" in res.json()["detail"]

    # Category 1 should NOT be deleted
    assert any(c.id == 1 for c in mock_db.categories)
