"""Unit tests for visual asset Pydantic schemas contract validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.library.schemas import (
    AssetCopyRequest,
    AssetListResponse,
    AssetResponse,
)


def test_asset_copy_request_valid() -> None:
    req = AssetCopyRequest(
        target_category_id=5,
        new_name="  Brand Expansion Logo  ",
    )
    assert req.target_category_id == 5
    assert req.new_name == "Brand Expansion Logo"


def test_asset_copy_request_default_name() -> None:
    req = AssetCopyRequest(target_category_id=10)
    assert req.target_category_id == 10
    assert req.new_name is None


@pytest.mark.parametrize("invalid_name", ["", "   ", "\t\n", "x" * 201])
def test_asset_copy_request_invalid_name(invalid_name: str) -> None:
    with pytest.raises(ValidationError):
        AssetCopyRequest(target_category_id=1, new_name=invalid_name)


def test_asset_copy_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        AssetCopyRequest(target_category_id=1, user_id=101)  # type: ignore[call-arg]


class DummyAssetORM:
    """Mock object mimicking Asset ORM instance."""

    def __init__(self) -> None:
        self.id = 50
        self.category_id = 3
        self.name = "Hero Banner"
        self.file_key = "library/101/ab/abcdef123456.png"
        self.mime_type = "image/png"
        self.file_size = 1048576
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


def test_asset_response_from_attributes() -> None:
    dummy = DummyAssetORM()
    res = AssetResponse.model_validate(dummy)
    assert res.id == 50
    assert res.category_id == 3
    assert res.name == "Hero Banner"
    assert res.file_key == "library/101/ab/abcdef123456.png"
    assert res.mime_type == "image/png"
    assert res.file_size == 1048576

    list_res = AssetListResponse(items=[res], total=1)
    assert list_res.total == 1
    assert len(list_res.items) == 1
