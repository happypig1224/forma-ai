"""Unit tests for CategoryUpdateRequest schema contract validation."""

import pytest
from pydantic import ValidationError

from formaai.modules.library.schemas import CategoryUpdateRequest


def test_category_update_name_only() -> None:
    req = CategoryUpdateRequest(name="  New Category Name  ")
    assert req.name == "New Category Name"
    assert req.sort_order is None


def test_category_update_sort_order_only() -> None:
    req = CategoryUpdateRequest(sort_order=15)
    assert req.name is None
    assert req.sort_order == 15


def test_category_update_sort_order_zero() -> None:
    req = CategoryUpdateRequest(sort_order=0)
    assert req.name is None
    assert req.sort_order == 0


def test_category_update_both_fields() -> None:
    req = CategoryUpdateRequest(name="Brand Expansion", sort_order=3)
    assert req.name == "Brand Expansion"
    assert req.sort_order == 3


def test_category_update_empty_payload_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CategoryUpdateRequest()
    assert "At least one of 'name' or 'sort_order' must be provided" in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
        "\t\n",
        "x" * 101,
    ],
)
def test_category_update_invalid_name(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        CategoryUpdateRequest(name=invalid_name)


def test_category_update_negative_sort_order_rejected() -> None:
    with pytest.raises(ValidationError):
        CategoryUpdateRequest(sort_order=-1)


def test_category_update_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        CategoryUpdateRequest(
            name="Valid Name",
            user_id=100,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        CategoryUpdateRequest(
            name="Valid Name",
            content_type="brand",  # type: ignore[call-arg]
        )
