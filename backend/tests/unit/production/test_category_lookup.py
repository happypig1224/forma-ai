"""Unit tests for library cross-module CategoryDTO query function."""

from datetime import UTC, datetime
from typing import Any

from formaai.modules.library import CategoryDTO, get_category_dto
from formaai.modules.library.models import Category


class MockCategoryLookupSession:
    """Mock DB session for category lookup testing."""

    def __init__(self, categories: list[Category]) -> None:
        self.categories = categories

    def scalar(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return None
        clauses = (
            whereclause.clauses if hasattr(whereclause, "clauses") else [whereclause]
        )
        cat_id = None
        user_id = None
        for expr in clauses:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col = getattr(expr.left, "name", None)
                val = getattr(expr.right, "value", None)
                if col == "id":
                    cat_id = val
                elif col == "user_id":
                    user_id = val

        for c in self.categories:
            if cat_id is not None and c.id != cat_id:
                continue
            if user_id is not None and c.user_id != user_id:
                continue
            return c
        return None


def test_get_category_dto_found_and_isolated() -> None:
    now = datetime.now(UTC)
    alice_cat = Category(
        id=1,
        user_id=101,
        content_type="brand",
        name="Alice Brand",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    bob_cat = Category(
        id=2,
        user_id=102,
        content_type="commerce",
        name="Bob Commerce",
        sort_order=1,
        created_at=now,
        updated_at=now,
    )
    session: Any = MockCategoryLookupSession([alice_cat, bob_cat])

    # 1. Successful lookup by owner
    dto = get_category_dto(session, category_id=1, user_id=101)
    assert dto is not None
    assert isinstance(dto, CategoryDTO)
    assert dto.id == 1
    assert dto.user_id == 101
    assert dto.content_type == "brand"
    assert dto.name == "Alice Brand"

    # 2. Cross-tenant lookup returns None
    assert get_category_dto(session, category_id=1, user_id=102) is None
    assert get_category_dto(session, category_id=2, user_id=101) is None

    # 3. Non-existent category returns None
    assert get_category_dto(session, category_id=999, user_id=101) is None
