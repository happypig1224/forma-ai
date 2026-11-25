"""User-owned categories, reusable materials and assets.

Provides DTO and query functions for cross-module boundary interactions,
ensuring outer modules (like production) do not import internal ORM entities.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from formaai.modules.library.models import Category


@dataclass(frozen=True)
class CategoryDTO:
    """Read-only category data transfer object for cross-module boundaries."""

    id: int
    user_id: int
    content_type: str
    name: str
    sort_order: int


def get_category_dto(db: Session, category_id: int, user_id: int) -> CategoryDTO | None:
    """Retrieve category by ID and user_id without exposing ORM entity."""
    cat = db.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user_id,
        )
    )
    if cat is None:
        return None
    return CategoryDTO(
        id=cat.id,
        user_id=cat.user_id,
        content_type=cat.content_type,
        name=cat.name,
        sort_order=cat.sort_order,
    )
