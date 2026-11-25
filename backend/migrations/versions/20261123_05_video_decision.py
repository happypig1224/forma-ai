"""Create video_decision (production) on MySQL 8.4.

Revision ID: 20261123_05
Revises: 20261123_04
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261123_05"
down_revision = "20261123_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_decision",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["video_task.id"],
            name="fk_video_decision_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user_account.id"],
            name="fk_video_decision_actor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "action IN ('skip_research', 'select_script', 'lock_spec', "
            "'confirm_storyboard', 'confirm_keyframes', 'trigger_composition')",
            name="ck_video_decision_action",
        ),
        sa.UniqueConstraint(
            "task_id", "idempotency_key", name="uq_decision_task_idempotency"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_as_ci",
    )
    op.create_index(
        "ix_video_decision_task_created",
        "video_decision",
        ["task_id", "created_at"],
    )
    op.create_index(
        "ix_video_decision_actor",
        "video_decision",
        ["actor_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_decision_actor", table_name="video_decision")
    op.drop_index("ix_video_decision_task_created", table_name="video_decision")
    op.drop_table("video_decision")
