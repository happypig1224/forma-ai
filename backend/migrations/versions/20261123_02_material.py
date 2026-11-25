"""Create material (library) on MySQL 8.4.

Revision ID: 20261123_02
Revises: 20261119_01
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_02"
down_revision = "20261119_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("material_type", sa.String(16), nullable=False),
        sa.Column("content", mysql.LONGTEXT(), nullable=False),
        sa.Column("sources", mysql.JSON(), nullable=False),
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
            name="fk_material_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name="fk_material_category",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "material_type IN ('research', 'outline', 'analysis')",
            name="ck_material_type",
        ),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="ck_material_title"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_material_user_cat",
        "material",
        ["user_id", "category_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_material_user_type",
        "material",
        ["user_id", "material_type", "id"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrades are deliberately blocked; restore from backup."
    )
