"""Create video_task (production) on MySQL 8.4.

Revision ID: 20261123_04
Revises: 20261123_03
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_04"
down_revision = "20261123_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_task",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("content_type", sa.String(16), nullable=False),
        sa.Column(
            "stage",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("input_key", sa.String(255), nullable=False),
        sa.Column("checkpoint_key", sa.String(255), nullable=True),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
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
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name="fk_video_task_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name="fk_video_task_category",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "content_type IN ('commerce', 'brand', 'knowledge', 'story')",
            name="ck_video_task_content_type",
        ),
        sa.CheckConstraint(
            "stage IN ('created', 'research', 'script_proposal', 'script_confirmed', "
            "'keyframes', 'shot_video', 'composition', 'finished')",
            name="ck_video_task_stage",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_confirmation', 'paused', "
            "'waiting_retry', 'success', 'failed')",
            name="ck_video_task_status",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(name)) > 0",
            name="ck_video_task_name",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_video_task_user_cat",
        "video_task",
        ["user_id", "category_id"],
    )
    op.create_index(
        "ix_video_task_user_status",
        "video_task",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_video_task_user_stage",
        "video_task",
        ["user_id", "stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_task_user_stage", table_name="video_task")
    op.drop_index("ix_video_task_user_status", table_name="video_task")
    op.drop_index("ix_video_task_user_cat", table_name="video_task")
    op.drop_table("video_task")
