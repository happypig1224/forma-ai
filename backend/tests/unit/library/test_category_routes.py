"""Unit tests for category API routes (list and create)."""

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


class MockCategoryDbSession:
    """In-memory database session mimicking MySQL behavior for Category queries."""

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
def current_test_user() -> UserAccount:
    user = UserAccount()
    user.id = 100
    user.email = "test.user@formaai.test"
    user.role = "user"
    user.status = "active"
    return user


@pytest.fixture
def mock_db() -> MockCategoryDbSession:
    return MockCategoryDbSession()


@pytest.fixture
def test_app(
    current_test_user: UserAccount,
    mock_db: MockCategoryDbSession,
) -> FastAPI:
    app = FastAPI()
    app.include_router(category_router)
    app.dependency_overrides[get_current_user] = lambda: current_test_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return app


def test_list_categories_empty(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        res = client.get("/api/categories")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []


def test_list_categories_filtering_and_ordering(
    test_app: FastAPI,
    mock_db: MockCategoryDbSession,
    current_test_user: UserAccount,
) -> None:
    # 2 commerce categories, 1 brand category
    now = datetime.now(UTC)
    c1 = Category(
        id=1,
        user_id=current_test_user.id,
        content_type="commerce",
        name="Beauty",
        sort_order=2,
        created_at=now,
        updated_at=now,
    )
    c2 = Category(
        id=2,
        user_id=current_test_user.id,
        content_type="commerce",
        name="Digital 3C",
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
    mock_db.categories = [c1, c2, c3]

    with TestClient(test_app) as client:
        # Filter by commerce: ordered by sort_order (3C sort=1, Beauty sort=2)
        res_com = client.get("/api/categories?content_type=commerce")
        assert res_com.status_code == status.HTTP_200_OK
        com_data = res_com.json()
        assert com_data["total"] == 2
        assert com_data["items"][0]["name"] == "Digital 3C"
        assert com_data["items"][1]["name"] == "Beauty"

        # Filter by brand: should return 1 item
        res_brand = client.get("/api/categories?content_type=brand")
        assert res_brand.status_code == status.HTTP_200_OK
        assert res_brand.json()["total"] == 1
        assert res_brand.json()["items"][0]["name"] == "Luxury"

        # No filter: should return all 3 categories
        res_all = client.get("/api/categories")
        assert res_all.status_code == status.HTTP_200_OK
        assert res_all.json()["total"] == 3


def test_create_category_with_explicit_sort_order(
    test_app: FastAPI,
    mock_db: MockCategoryDbSession,
) -> None:
    with TestClient(test_app) as client:
        res = client.post(
            "/api/categories",
            json={
                "content_type": "knowledge",
                "name": "  Science & Technology  ",
                "sort_order": 10,
            },
        )
        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["content_type"] == "knowledge"
        assert data["name"] == "Science & Technology"  # Auto-trimmed
        assert data["sort_order"] == 10
        assert data["id"] == 1
        assert "created_at" in data

    assert len(mock_db.categories) == 1


def test_create_category_auto_increment_sort_order(
    test_app: FastAPI,
) -> None:
    with TestClient(test_app) as client:
        # First category without sort_order -> gets sort_order = 0
        r1 = client.post(
            "/api/categories",
            json={"content_type": "story", "name": "Sci-Fi"},
        )
        assert r1.status_code == status.HTTP_201_CREATED
        assert r1.json()["sort_order"] == 0

        # Second category without sort_order -> gets sort_order = 1
        r2 = client.post(
            "/api/categories",
            json={"content_type": "story", "name": "Fantasy"},
        )
        assert r2.status_code == status.HTTP_201_CREATED
        assert r2.json()["sort_order"] == 1

        # Third category in DIFFERENT content_type -> gets sort_order = 0
        r3 = client.post(
            "/api/categories",
            json={"content_type": "commerce", "name": "Fashion"},
        )
        assert r3.status_code == status.HTTP_201_CREATED
        assert r3.json()["sort_order"] == 0


def test_create_category_duplicate_name_conflict(
    test_app: FastAPI,
) -> None:
    with TestClient(test_app) as client:
        # 1. Create first category
        r1 = client.post(
            "/api/categories",
            json={"content_type": "commerce", "name": "Electronics"},
        )
        assert r1.status_code == status.HTTP_201_CREATED

        # 2. Duplicate in same content_type rejected with 409
        r2 = client.post(
            "/api/categories",
            json={"content_type": "commerce", "name": "  Electronics  "},
        )
        assert r2.status_code == status.HTTP_409_CONFLICT
        assert "already exists in commerce" in r2.json()["detail"]

        # 3. Same name in DIFFERENT content_type is permitted
        r3 = client.post(
            "/api/categories",
            json={"content_type": "brand", "name": "Electronics"},
        )
        assert r3.status_code == status.HTTP_201_CREATED


def test_create_category_integrity_error_rollback(
    test_app: FastAPI,
    mock_db: MockCategoryDbSession,
) -> None:
    # Simulate DB integrity error on commit (race condition)
    def raise_integrity() -> None:
        raise IntegrityError("duplicate entry", {}, Exception("Duplicate"))

    mock_db.commit = raise_integrity  # type: ignore[assignment]

    with TestClient(test_app) as client:
        res = client.post(
            "/api/categories",
            json={"content_type": "commerce", "name": "Shoes"},
        )
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "already exists in commerce" in res.json()["detail"]


def test_categories_unauthorized_without_token() -> None:
    # Real router mounted on bare FastAPI without overrides
    app = FastAPI()
    app.include_router(category_router)

    with TestClient(app) as client:
        # GET without auth
        res_get = client.get("/api/categories")
        assert res_get.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

        # POST without auth
        res_post = client.post(
            "/api/categories",
            json={"content_type": "commerce", "name": "Hardware"},
        )
        assert res_post.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
