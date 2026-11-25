"""Unit tests for Material Pydantic schemas contract validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.library.schemas import (
    MaterialCreateRequest,
    MaterialListResponse,
    MaterialResponse,
    MaterialUpdateRequest,
    SourceItem,
)


def test_source_item_valid() -> None:
    now = datetime.now(UTC)
    item = SourceItem(
        title="  Market Research Report 2026  ",
        url="https://example.com/report",
        excerpt="Growing trend in digital commerce.",
        accessed_at=now,
    )
    assert item.title == "Market Research Report 2026"
    assert item.url == "https://example.com/report"
    assert item.excerpt == "Growing trend in digital commerce."
    assert item.accessed_at == now


@pytest.mark.parametrize("invalid_title", ["", "   ", "\t\n", "x" * 201])
def test_source_item_invalid_title(invalid_title: str) -> None:
    with pytest.raises(ValidationError):
        SourceItem(title=invalid_title)


def test_source_item_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        SourceItem(title="Valid", score=10)  # type: ignore[call-arg]


def test_material_create_request_valid() -> None:
    req = MaterialCreateRequest(
        category_id=1,
        material_type="research",
        title="  Digital 3C Trends  ",
        content="  Comprehensive analysis of Q3 consumer electronics.  ",
        sources=[SourceItem(title="TechNews", url="https://technews.example.com")],
    )
    assert req.category_id == 1
    assert req.material_type == "research"
    assert req.title == "Digital 3C Trends"
    assert req.content == "Comprehensive analysis of Q3 consumer electronics."
    assert len(req.sources) == 1
    assert req.sources[0].title == "TechNews"


def test_material_create_request_default_sources() -> None:
    req = MaterialCreateRequest(
        category_id=2,
        material_type="outline",
        title="Short Video Script Outline",
        content="Scene 1: Introduction",
    )
    assert req.sources == []


@pytest.mark.parametrize("invalid_type", ["unknown", "blog", "commerce", "video"])
def test_material_create_request_invalid_type(invalid_type: str) -> None:
    with pytest.raises(ValidationError):
        MaterialCreateRequest(
            category_id=1,
            material_type=invalid_type,  # type: ignore[arg-type]
            title="Valid Title",
            content="Valid Content",
        )


@pytest.mark.parametrize("bad_str", ["", "   ", "\t\n"])
def test_material_create_request_empty_strings_rejected(bad_str: str) -> None:
    with pytest.raises(ValidationError):
        MaterialCreateRequest(
            category_id=1,
            material_type="analysis",
            title=bad_str,
            content="Valid Content",
        )
    with pytest.raises(ValidationError):
        MaterialCreateRequest(
            category_id=1,
            material_type="analysis",
            title="Valid Title",
            content=bad_str,
        )


def test_material_create_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        MaterialCreateRequest(
            category_id=1,
            material_type="research",
            title="Valid",
            content="Valid",
            user_id=101,  # type: ignore[call-arg]
        )


def test_material_update_request_single_fields() -> None:
    # Update category_id only
    u1 = MaterialUpdateRequest(category_id=10)
    assert u1.category_id == 10
    assert u1.title is None

    # Update title only (with strip)
    u2 = MaterialUpdateRequest(title="  New Title  ")
    assert u2.title == "New Title"

    # Update content only
    u3 = MaterialUpdateRequest(content="New Content")
    assert u3.content == "New Content"

    # Update sources only
    u4 = MaterialUpdateRequest(sources=[SourceItem(title="S1")])
    assert u4.sources is not None
    assert len(u4.sources) == 1


def test_material_update_request_empty_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MaterialUpdateRequest()
    assert (
        "At least one of 'category_id', 'title', 'content', or 'sources'"
        " must be provided"
    ) in str(exc_info.value)


def test_material_update_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        MaterialUpdateRequest(title="Valid", material_type="research")  # type: ignore[call-arg]


class DummyMaterialORM:
    """Mock object mimicking Material ORM instance."""

    def __init__(self) -> None:
        self.id = 100
        self.category_id = 5
        self.material_type = "research"
        self.title = "ORM Material"
        self.content = "ORM Content"
        self.sources = [{"title": "Source 1", "url": "https://s1.test"}]
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


def test_material_response_from_attributes() -> None:
    dummy = DummyMaterialORM()
    res = MaterialResponse.model_validate(dummy)
    assert res.id == 100
    assert res.category_id == 5
    assert res.material_type == "research"
    assert res.title == "ORM Material"
    assert len(res.sources) == 1
    assert res.sources[0].title == "Source 1"

    list_res = MaterialListResponse(items=[res], total=1)
    assert list_res.total == 1
    assert len(list_res.items) == 1
