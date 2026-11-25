"""Create user_account (identity) and category (library) on MySQL 8.4.

Initial schema, no source rows or existing tables are modified. Do not run on a
nonempty unmanaged database. MySQL DDL can leave partial state on failure:
inspect existing tables before retrying, rather than using IF NOT EXISTS.
Downgrade is deliberately blocked; restore a verified backup for populated DBs.
For disposable test databases, destroy only the explicitly isolated database.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261119_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role", sa.String(16), nullable=False, server_default=sa.text("'user'")
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("email_verified_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "version", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
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
        sa.UniqueConstraint("email", name="uq_user_account_email"),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_user_account_role"),
        sa.CheckConstraint(
            "status IN ('active', 'disabling', 'disabled', 'deleted')",
            name="ck_user_account_status",
        ),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="ck_user_account_deleted_at",
        ),
        sa.CheckConstraint("version >= 0", name="ck_user_account_version"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_table(
        "category",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
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
            name="fk_category_user",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id", "content_type", "name", name="uq_category_user_type_name"
        ),
        sa.CheckConstraint(
            "content_type IN ('commerce', 'brand', 'knowledge', 'story')",
            name="ck_category_content_type",
        ),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_category_name"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_category_user_type_order",
        "category",
        ["user_id", "content_type", "sort_order", "id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade blocked: use an explicitly verified restore"
    )
