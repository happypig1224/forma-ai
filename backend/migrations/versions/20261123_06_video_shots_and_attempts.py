"""Create video_shot and video_attempt (production) on MySQL 8.4.

Revision ID: 20261123_06
Revises: 20261123_05
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_06"
down_revision = "20261123_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_shot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("shot_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("duration_sec", sa.Numeric(4, 1), nullable=False),
        sa.Column("selected_attempt_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["video_task.id"],
            name="fk_video_shot_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", "shot_no", name="uq_shot_task_shot_no"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_video_shot_task_id",
        "video_shot",
        ["task_id"],
    )

    op.create_table(
        "video_attempt",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("shot_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("input_key", sa.String(512), nullable=False),
        sa.Column("result_key", sa.String(512), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["video_task.id"],
            name="fk_video_attempt_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["shot_id"],
            ["video_shot.id"],
            name="fk_video_attempt_shot",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "operation IN ('generate', 'redo', 'inpaint')",
            name="ck_video_attempt_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_video_attempt_status",
        ),
        sa.UniqueConstraint(
            "shot_id", "attempt_no", name="uq_attempt_shot_attempt_no"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_video_attempt_task_id",
        "video_attempt",
        ["task_id"],
    )
    op.create_index(
        "ix_video_attempt_shot_id",
        "video_attempt",
        ["shot_id"],
    )
    op.create_index(
        "ix_video_attempt_idempotency",
        "video_attempt",
        ["shot_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_attempt_idempotency", table_name="video_attempt")
    op.drop_index("ix_video_attempt_shot_id", table_name="video_attempt")
    op.drop_index("ix_video_attempt_task_id", table_name="video_attempt")
    op.drop_table("video_attempt")

    op.drop_index("ix_video_shot_task_id", table_name="video_shot")
    op.drop_table("video_shot")
