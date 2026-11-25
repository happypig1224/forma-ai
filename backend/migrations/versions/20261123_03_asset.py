"""Create asset (library) on MySQL 8.4.

Revision ID: 20261123_03
Revises: 20261123_02
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_03"
down_revision = "20261123_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("file_key", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name="fk_asset_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name="fk_asset_category",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_asset_name"),
        sa.CheckConstraint("file_size >= 0", name="ck_asset_size"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_asset_user_cat",
        "asset",
        ["user_id", "category_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_asset_user_mime",
        "asset",
        ["user_id", "mime_type", "id"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrades are deliberately blocked; restore from backup."
    )
