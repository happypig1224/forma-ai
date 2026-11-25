"""Pydantic schemas for category and material management in library domain."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContentType = Literal["commerce", "brand", "knowledge", "story"]
MaterialType = Literal["research", "outline", "analysis"]


# ---------------------------------------------------------------------------
# Category Schemas
# ---------------------------------------------------------------------------


class CategoryCreateRequest(BaseModel):
    """Payload to create a new user-private category."""

    model_config = ConfigDict(extra="forbid")

    content_type: ContentType
    name: str = Field(min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Category name cannot be empty or pure whitespace")
        return trimmed


class CategoryUpdateRequest(BaseModel):
    """Payload to update an existing user-private category."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Category name cannot be empty or pure whitespace")
        return trimmed

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> CategoryUpdateRequest:
        if self.name is None and self.sort_order is None:
            raise ValueError("At least one of 'name' or 'sort_order' must be provided")
        return self


class CategoryResponse(BaseModel):
    """Public representation of a user-private category."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    content_type: ContentType
    name: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class CategoryListResponse(BaseModel):
    """Envelope for category list queries."""

    model_config = ConfigDict(extra="forbid")

    items: list[CategoryResponse]
    total: int


# ---------------------------------------------------------------------------
# Material Schemas
# ---------------------------------------------------------------------------


class SourceItem(BaseModel):
    """Structured citation source entry."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2048)
    excerpt: str | None = Field(default=None, max_length=2000)
    accessed_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Source title cannot be empty or pure whitespace")
        return trimmed


class MaterialCreateRequest(BaseModel):
    """Payload to create a new material entry."""

    model_config = ConfigDict(extra="forbid")

    category_id: int
    material_type: MaterialType
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    sources: list[SourceItem] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Material title cannot be empty or pure whitespace")
        return trimmed

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Material content cannot be empty or pure whitespace")
        return trimmed


class MaterialUpdateRequest(BaseModel):
    """Payload to update an existing material entry."""

    model_config = ConfigDict(extra="forbid")

    category_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    sources: list[SourceItem] | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Material title cannot be empty or pure whitespace")
        return trimmed

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Material content cannot be empty or pure whitespace")
        return trimmed

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> MaterialUpdateRequest:
        if (
            self.category_id is None
            and self.title is None
            and self.content is None
            and self.sources is None
        ):
            raise ValueError(
                "At least one of 'category_id', 'title', 'content', or 'sources' "
                "must be provided"
            )
        return self


class MaterialResponse(BaseModel):
    """Public representation of a material entry."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    category_id: int
    material_type: MaterialType
    title: str
    content: str
    sources: list[SourceItem]
    created_at: datetime
    updated_at: datetime


class MaterialListResponse(BaseModel):
    """Envelope for material list queries."""

    model_config = ConfigDict(extra="forbid")

    items: list[MaterialResponse]
    total: int


# ---------------------------------------------------------------------------
# Asset Schemas
# ---------------------------------------------------------------------------


class AssetCopyRequest(BaseModel):
    """Payload to copy an existing asset to another category."""

    model_config = ConfigDict(extra="forbid")

    target_category_id: int
    new_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("new_name")
    @classmethod
    def validate_new_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Asset new_name cannot be empty or pure whitespace")
        return trimmed


class AssetResponse(BaseModel):
    """Public representation of a visual asset."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    category_id: int
    name: str
    file_key: str
    mime_type: str
    file_size: int
    created_at: datetime
    updated_at: datetime


class AssetListResponse(BaseModel):
    """Envelope for asset list queries."""

    model_config = ConfigDict(extra="forbid")

    items: list[AssetResponse]
    total: int
