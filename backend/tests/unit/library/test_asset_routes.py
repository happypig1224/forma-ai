"""Unit tests for visual asset API routes and referential protection."""

import io
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from formaai.infrastructure.storage import MockStorageService, StorageServiceProtocol
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


class MockAssetDbSession:
    """In-memory database session mimicking MySQL for Asset lifecycle."""

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
        is_asset_query = "asset" in stmt_str
        is_material_query = "material" in stmt_str and not is_asset_query
        is_category_query = (
            "category" in stmt_str and not is_asset_query and not is_material_query
        )

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

        if is_asset_query:
            # Query for asset reference (check_category_in_use)
            if category_id is not None and target_id is None:
                for a in self.assets:
                    if a.category_id == category_id:
                        return a.id
                return None

            # Query for single asset
            for a in self.assets:
                if target_id is not None and a.id != target_id:
                    continue
                if user_id is not None and a.user_id != user_id:
                    continue
                return a
            return None

        if is_material_query:
            if category_id is not None and target_id is None:
                for m in self.materials:
                    if m.category_id == category_id:
                        return m.id
                return None
            return None

        if is_category_query:
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
        mime_type = None

        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = expr.left.name
                val = expr.right.value
                if col == "user_id":
                    user_id = val
                elif col == "category_id":
                    category_id = val
                elif col == "mime_type":
                    mime_type = val

        filtered = [
            a
            for a in self.assets
            if (user_id is None or a.user_id == user_id)
            and (category_id is None or a.category_id == category_id)
            and (mime_type is None or a.mime_type == mime_type)
        ]
        filtered.sort(key=lambda a: (a.created_at, a.id), reverse=True)

        class ScalarsResult:
            def __init__(self, items: list[Asset]) -> None:
                self._items = items

            def all(self) -> list[Asset]:
                return self._items

        return ScalarsResult(filtered)

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
def mock_storage() -> MockStorageService:
    return MockStorageService()


@pytest.fixture
def test_db(current_user: UserAccount, other_user: UserAccount) -> MockAssetDbSession:
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
        name="Fashion",
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

    a1 = Asset(
        id=1,
        user_id=current_user.id,
        category_id=1,
        name="Smartphone Front View",
        file_key="library/100/12/12345678.png",
        mime_type="image/png",
        file_size=2048,
        created_at=now,
        updated_at=now,
    )
    a_other = Asset(
        id=99,
        user_id=other_user.id,
        category_id=10,
        name="Secret Logo",
        file_key="library/200/99/99999999.png",
        mime_type="image/png",
        file_size=1024,
        created_at=now,
        updated_at=now,
    )

    return MockAssetDbSession([c1, c2, c_other], [], [a1, a_other])


def create_client(
    user: UserAccount | None,
    db: MockAssetDbSession,
    storage: StorageServiceProtocol,
) -> TestClient:
    app = FastAPI()
    app.include_router(category_router)
    app.include_router(material_router)
    app.include_router(asset_router)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage_service] = lambda: storage
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_asset_routes_unauthorized(
    test_db: MockAssetDbSession, mock_storage: MockStorageService
) -> None:
    client = create_client(None, test_db, mock_storage)
    assert client.get("/api/assets").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get("/api/assets/1").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        client.post("/api/assets/1/copy", json={"target_category_id": 2}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert client.delete("/api/assets/1").status_code == status.HTTP_401_UNAUTHORIZED


def test_upload_asset_category_not_found(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)

    file_content = b"fake image bytes"
    files = {"file": ("banner.png", io.BytesIO(file_content), "image/png")}

    # Non-existent category
    res = client.post("/api/assets/upload", data={"category_id": 999}, files=files)
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Category not found"

    # Other user's category (id=10)
    files2 = {"file": ("banner.png", io.BytesIO(file_content), "image/png")}
    res_idor = client.post("/api/assets/upload", data={"category_id": 10}, files=files2)
    assert res_idor.status_code == status.HTTP_404_NOT_FOUND
    assert res_idor.json()["detail"] == "Category not found"


def test_upload_asset_empty_file_rejected(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)
    empty_files = {"file": ("empty.png", io.BytesIO(b""), "image/png")}
    res = client.post("/api/assets/upload", data={"category_id": 1}, files=empty_files)
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "empty" in res.json()["detail"]


def test_upload_asset_success(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)
    file_bytes = b"real high-res product photo bytes"
    files = {"file": ("hero.jpg", io.BytesIO(file_bytes), "image/jpeg")}
    res = client.post(
        "/api/assets/upload",
        data={"category_id": 1, "name": "Hero Banner Custom"},
        files=files,
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["category_id"] == 1
    assert data["name"] == "Hero Banner Custom"
    assert data["mime_type"] == "image/jpeg"
    assert data["file_size"] == len(file_bytes)
    assert data["file_key"].startswith("library/100/")


def test_copy_asset_to_category_success(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)

    # Copy Asset 1 from Category 1 to Category 2
    res_copy = client.post(
        "/api/assets/1/copy",
        json={"target_category_id": 2, "new_name": "Reused in Fashion"},
    )
    assert res_copy.status_code == status.HTTP_201_CREATED
    data = res_copy.json()
    assert data["id"] != 1  # Distinct new entity ID
    assert data["category_id"] == 2  # Belongs to Category 2
    assert data["name"] == "Reused in Fashion"
    assert data["file_key"] == "library/100/12/12345678.png"  # Reuses stable file


def test_copy_asset_target_category_not_found(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)
    # Target category 10 belongs to other user
    res_fail = client.post(
        "/api/assets/1/copy",
        json={"target_category_id": 10},
    )
    assert res_fail.status_code == status.HTTP_404_NOT_FOUND
    assert res_fail.json()["detail"] == "Target category not found"


def test_copy_asset_source_not_found(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)
    # Asset 99 belongs to other user
    res = client.post(
        "/api/assets/99/copy",
        json={"target_category_id": 1},
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert res.json()["detail"] == "Asset not found"


def test_list_assets_with_filtering(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)

    # Filter by category_id = 1
    res_cat1 = client.get("/api/assets?category_id=1").json()
    assert res_cat1["total"] == 1
    assert res_cat1["items"][0]["name"] == "Smartphone Front View"

    # Filter by mime_type
    res_mime = client.get("/api/assets?mime_type=image/png").json()
    assert res_mime["total"] == 1

    # Unfiltered: does NOT include other user's asset (id=99)
    res_all = client.get("/api/assets").json()
    assert res_all["total"] == 1
    assert res_all["items"][0]["id"] == 1


def test_get_asset_detail_and_isolation(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)
    # Own asset
    assert client.get("/api/assets/1").status_code == status.HTTP_200_OK

    # Other user's asset -> 404
    assert client.get("/api/assets/99").status_code == status.HTTP_404_NOT_FOUND


def test_delete_asset_and_category_protection(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    client = create_client(current_user, test_db, mock_storage)

    # Category 1 has Asset 1 attached: DELETE category is blocked (400)
    res_del_cat = client.delete("/api/categories/1")
    assert res_del_cat.status_code == status.HTTP_400_BAD_REQUEST
    assert "referenced" in res_del_cat.json()["detail"]

    # Delete Asset 1
    res_del_ass = client.delete("/api/assets/1")
    assert res_del_ass.status_code == status.HTTP_204_NO_CONTENT

    # Asset 1 is gone
    assert client.get("/api/assets/1").status_code == status.HTTP_404_NOT_FOUND

    # Now Category 1 has zero references: DELETE category succeeds (204)
    res_del_cat_ok = client.delete("/api/categories/1")
    assert res_del_cat_ok.status_code == status.HTTP_204_NO_CONTENT


def test_get_asset_file_content_and_isolation(
    test_db: MockAssetDbSession,
    current_user: UserAccount,
    mock_storage: MockStorageService,
) -> None:
    # Save dummy file content for asset 1 (file_key: library/100/12/12345678.png)
    mock_storage.files["library/100/12/12345678.png"] = b"\x89PNG\r\n\x1a\n\x00\x00_fake_png_data"
    client = create_client(current_user, test_db, mock_storage)

    # 1. Own asset file -> 200 OK with correct media_type
    res = client.get("/api/assets/1/file")
    assert res.status_code == status.HTTP_200_OK
    assert res.headers["content-type"] == "image/png"
    assert res.content == b"\x89PNG\r\n\x1a\n\x00\x00_fake_png_data"

    # 2. Other user's asset file -> 404
    assert client.get("/api/assets/99/file").status_code == status.HTTP_404_NOT_FOUND

    # 3. Non-existent asset -> 404
    assert client.get("/api/assets/999/file").status_code == status.HTTP_404_NOT_FOUND

