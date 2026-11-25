"""Create usage_record (production) on MySQL 8.4.

Revision ID: 20261123_07
Revises: 20261123_06
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_07"
down_revision = "20261123_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("metric_type", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "cost_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "recorded_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name="fk_usage_record_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["video_task.id"],
            name="fk_usage_record_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["video_attempt.id"],
            name="fk_usage_record_attempt",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "metric_type IN ('gpu_second', 'storage_byte', 'api_call')",
            name="ck_usage_record_metric",
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_usage_record_quantity"),
        sa.CheckConstraint("cost_cents >= 0", name="ck_usage_record_cost"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_usage_record_user",
        "usage_record",
        ["user_id", "recorded_at"],
    )
    op.create_index(
        "ix_usage_record_task",
        "usage_record",
        ["task_id", "recorded_at"],
    )
    op.create_index(
        "ix_usage_record_attempt",
        "usage_record",
        ["attempt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_record_attempt", table_name="usage_record")
    op.drop_index("ix_usage_record_task", table_name="usage_record")
    op.drop_index("ix_usage_record_user", table_name="usage_record")
    op.drop_table("usage_record")
