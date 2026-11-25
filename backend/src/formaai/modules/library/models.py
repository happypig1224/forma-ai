"""Library domain models: category, material, and asset."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from formaai.infrastructure.database import Base, UTCDateTime


class Category(Base):
    """A single-level category owned by one user within one content type."""

    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "content_type", "name", name="uq_category_user_type_name"
        ),
        CheckConstraint(
            "content_type IN ('commerce', 'brand', 'knowledge', 'story')",
            name="ck_category_content_type",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_category_name"),
        Index(
            "ix_category_user_type_order",
            "user_id",
            "content_type",
            "sort_order",
            "id",
        ),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.id", name="fk_category_user", ondelete="RESTRICT"),
    )
    content_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )


class Material(Base):
    """Reusable text research, summary outline, or video feature analysis."""

    __tablename__ = "material"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('research', 'outline', 'analysis')",
            name="ck_material_type",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="ck_material_title"),
        Index("ix_material_user_cat", "user_id", "category_id", "id"),
        Index("ix_material_user_type", "user_id", "material_type", "id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.id", name="fk_material_user", ondelete="RESTRICT"),
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("category.id", name="fk_material_category", ondelete="RESTRICT"),
    )
    title: Mapped[str] = mapped_column(String(200))
    material_type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )


class Asset(Base):
    """Reusable visual asset (image, video, logo) owned by a user in one category."""

    __tablename__ = "asset"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_asset_name"),
        CheckConstraint("file_size >= 0", name="ck_asset_size"),
        Index("ix_asset_user_cat", "user_id", "category_id", "id"),
        Index("ix_asset_user_mime", "user_id", "mime_type", "id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.id", name="fk_asset_user", ondelete="RESTRICT"),
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("category.id", name="fk_asset_category", ondelete="RESTRICT"),
    )
    name: Mapped[str] = mapped_column(String(200))
    file_key: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )
