"""Unit tests for material API routes and category reference protection."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from formaai.modules.identity.dependencies import get_current_user, get_db
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Asset, Category, Material
from formaai.modules.library.router import category_router, material_router


class MockMaterialDbSession:
    """In-memory database session mimicking MySQL for Category and Material."""

    def __init__(
        self,
        categories: list[Category] | None = None,
        materials: list[Material] | None = None,
    ) -> None:
        self.categories: list[Category] = categories if categories is not None else []
        self.materials: list[Material] = materials if materials is not None else []
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
def current_user() -> UserAccount:
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
def test_db(
    current_user: UserAccount, other_user: UserAccount
) -> MockMaterialDbSession:
    now = datetime.now(UTC)
    c1 = Category(
        id=1,
        user_id=current_user.id,
        content_type="commerce",
        name="Digital 3C",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    c2 = Category(
        id=2,
        user_id=current_user.id,
        content_type="commerce",
        name="Smart Home",
        sort_order=1,
        created_at=now,
        updated_at=now,
    )
    c_other = Category(
        id=10,
        user_id=other_user.id,
        content_type="commerce",
        name="Other Cat",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )

    m1 = Material(
        id=1,
        user_id=current_user.id,
        category_id=1,
        material_type="research",
        title="Smartphone Trends",
        content="Market share report Q3",
        sources=[{"title": "IDC Report", "url": "https://idc.test"}],
        created_at=now,
        updated_at=now,
    )
    m_other = Material(
        id=99,
        user_id=other_user.id,
        category_id=10,
        material_type="outline",
        title="Other User Outline",
        content="Confidential",
        sources=[],
        created_at=now,
        updated_at=now,
    )

    return MockMaterialDbSession([c1, c2, c_other], [m1, m_other])


def create_client(user: UserAccount | None, db: MockMaterialDbSession) -> TestClient:
    app = FastAPI()
    app.include_router(category_router)
    app.include_router(material_router)

    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_material_routes_unauthorized(test_db: MockMaterialDbSession) -> None:
    client = create_client(None, test_db)
    assert client.get("/api/materials").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        client.post(
            "/api/materials",
            json={
                "category_id": 1,
                "material_type": "research",
                "title": "T",
                "content": "C",
            },
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert client.get("/api/materials/1").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        client.patch("/api/materials/1", json={"title": "New"}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert client.delete("/api/materials/1").status_code == status.HTTP_401_UNAUTHORIZED


def test_create_material_category_not_found(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    client = create_client(current_user, test_db)

    # 1. Non-existent category_id
    res_404 = client.post(
        "/api/materials",
        json={
            "category_id": 999,
            "material_type": "research",
            "title": "Title",
            "content": "Content",
        },
    )
    assert res_404.status_code == status.HTTP_404_NOT_FOUND
    assert res_404.json()["detail"] == "Category not found"

    # 2. Category belonging to other user (id=10)
    res_idor = client.post(
        "/api/materials",
        json={
            "category_id": 10,
            "material_type": "research",
            "title": "Title",
            "content": "Content",
        },
    )
    assert res_idor.status_code == status.HTTP_404_NOT_FOUND
    assert res_idor.json()["detail"] == "Category not found"


def test_create_material_success(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    client = create_client(current_user, test_db)
    payload = {
        "category_id": 1,
        "material_type": "outline",
        "title": "Short Video Hook Script",
        "content": "Hook: Why your battery drains fast.",
        "sources": [
            {
                "title": "Battery Lab",
                "url": "https://lab.test",
                "excerpt": "Charge cycle data",
            }
        ],
    }
    res = client.post("/api/materials", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["category_id"] == 1
    assert data["material_type"] == "outline"
    assert data["title"] == "Short Video Hook Script"
    assert len(data["sources"]) == 1
    assert data["sources"][0]["title"] == "Battery Lab"


def test_list_materials_with_filters(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    client = create_client(current_user, test_db)

    # Add another material for category 2
    client.post(
        "/api/materials",
        json={
            "category_id": 2,
            "material_type": "analysis",
            "title": "Robot Vacuum Feature Breakdown",
            "content": "LiDAR vs VSLAM accuracy comparison",
        },
    )

    # Filter by category_id = 1
    res_cat1 = client.get("/api/materials?category_id=1").json()
    assert res_cat1["total"] == 1
    assert res_cat1["items"][0]["title"] == "Smartphone Trends"

    # Filter by material_type = analysis
    res_type = client.get("/api/materials?material_type=analysis").json()
    assert res_type["total"] == 1
    assert res_type["items"][0]["title"] == "Robot Vacuum Feature Breakdown"

    # Unfiltered: returns both owned materials, excludes other user's material (id=99)
    res_all = client.get("/api/materials").json()
    assert res_all["total"] == 2
    titles = {item["title"] for item in res_all["items"]}
    assert "Smartphone Trends" in titles
    assert "Robot Vacuum Feature Breakdown" in titles
    assert "Other User Outline" not in titles


def test_get_material_detail_and_isolation(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    client = create_client(current_user, test_db)

    # Own material detail
    res = client.get("/api/materials/1")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["title"] == "Smartphone Trends"

    # Other user's material -> 404
    res_other = client.get("/api/materials/99")
    assert res_other.status_code == status.HTTP_404_NOT_FOUND
    assert res_other.json()["detail"] == "Material not found"


def test_patch_material_update_and_reassign_category(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    client = create_client(current_user, test_db)

    # 1. Update title and content
    res_patch = client.patch(
        "/api/materials/1",
        json={"title": "Updated Trends 2026", "content": "Updated content"},
    )
    assert res_patch.status_code == status.HTTP_200_OK
    assert res_patch.json()["title"] == "Updated Trends 2026"

    # 2. Reassign to valid category 2
    res_cat = client.patch("/api/materials/1", json={"category_id": 2})
    assert res_cat.status_code == status.HTTP_200_OK
    assert res_cat.json()["category_id"] == 2

    # 3. Attempt reassign to invalid/other user's category 10 -> 404
    res_fail = client.patch("/api/materials/1", json={"category_id": 10})
    assert res_fail.status_code == status.HTTP_404_NOT_FOUND
    assert res_fail.json()["detail"] == "Target category not found"


def test_delete_material_and_category_protection_lifecycle(
    test_db: MockMaterialDbSession, current_user: UserAccount
) -> None:
    """Verifies category cannot be deleted while referenced by material."""
    client = create_client(current_user, test_db)

    # Category 1 has material 1 attached. Delete category 1 -> 400 Bad Request
    res_del_cat = client.delete("/api/categories/1")
    assert res_del_cat.status_code == status.HTTP_400_BAD_REQUEST
    assert "referenced" in res_del_cat.json()["detail"]

    # Delete material 1
    res_del_mat = client.delete("/api/materials/1")
    assert res_del_mat.status_code == status.HTTP_204_NO_CONTENT

    # Material is gone
    assert client.get("/api/materials/1").status_code == status.HTTP_404_NOT_FOUND

    # Now category 1 is unreferenced: deletion succeeds!
    res_del_cat_unlocked = client.delete("/api/categories/1")
    assert res_del_cat_unlocked.status_code == status.HTTP_204_NO_CONTENT
