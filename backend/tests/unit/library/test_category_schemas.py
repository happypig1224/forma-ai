"""Unit tests for category Pydantic schemas contract validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.library.schemas import (
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryResponse,
)


class DummyCategoryORM:
    """Mock object representing Category ORM model."""

    def __init__(
        self,
        id: int,
        content_type: str,
        name: str,
        sort_order: int,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.id = id
        self.content_type = content_type
        self.name = name
        self.sort_order = sort_order
        self.created_at = created_at
        self.updated_at = updated_at


def test_category_create_request_valid() -> None:
    req = CategoryCreateRequest(
        content_type="commerce",
        name="  Electronics & Gadgets  ",
        sort_order=5,
    )
    assert req.content_type == "commerce"
    assert req.name == "Electronics & Gadgets"  # Auto-trimmed
    assert req.sort_order == 5


def test_category_create_request_default_sort_order() -> None:
    req = CategoryCreateRequest(
        content_type="brand",
        name="Fashion Brand",
    )
    assert req.content_type == "brand"
    assert req.name == "Fashion Brand"
    assert req.sort_order is None


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
        "\t\n",
        "a" * 101,
    ],
)
def test_category_create_request_invalid_name(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        CategoryCreateRequest(
            content_type="knowledge",
            name=invalid_name,
        )


def test_category_create_request_invalid_content_type() -> None:
    with pytest.raises(ValidationError):
        CategoryCreateRequest(
            content_type="unsupported_type",  # type: ignore[arg-type]
            name="Valid Name",
        )


def test_category_create_request_negative_sort_order() -> None:
    with pytest.raises(ValidationError):
        CategoryCreateRequest(
            content_type="story",
            name="Sci-Fi",
            sort_order=-1,
        )


def test_category_create_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        CategoryCreateRequest(
            content_type="commerce",
            name="Digital",
            user_id=99,  # type: ignore[call-arg]
        )


def test_category_response_from_attributes() -> None:
    now = datetime.now(UTC)
    mock_cat = DummyCategoryORM(
        id=12,
        content_type="story",
        name="Adventure Stories",
        sort_order=1,
        created_at=now,
        updated_at=now,
    )
    res = CategoryResponse.model_validate(mock_cat)
    assert res.id == 12
    assert res.content_type == "story"
    assert res.name == "Adventure Stories"
    assert res.sort_order == 1
    assert res.created_at == now
    assert res.updated_at == now


def test_category_list_response() -> None:
    now = datetime.now(UTC)
    cat1 = CategoryResponse(
        id=1,
        content_type="brand",
        name="Luxury",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    cat2 = CategoryResponse(
        id=2,
        content_type="brand",
        name="Casual",
        sort_order=1,
        created_at=now,
        updated_at=now,
    )
    list_res = CategoryListResponse(items=[cat1, cat2], total=2)
    assert list_res.total == 2
    assert len(list_res.items) == 2
    assert list_res.items[0].name == "Luxury"
    assert list_res.items[1].name == "Casual"
