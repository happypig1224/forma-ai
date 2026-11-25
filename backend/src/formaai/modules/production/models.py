"""Video production domain models: video_task and state machine primitives."""

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from formaai.infrastructure.database import Base, UTCDateTime

TaskContentType = Literal["commerce", "brand", "knowledge", "story"]

TaskStage = Literal[
    "created",
    "research",
    "script_proposal",
    "script_confirmed",
    "keyframes",
    "shot_video",
    "composition",
    "finished",
]

TaskStatus = Literal[
    "pending",
    "running",
    "waiting_confirmation",
    "paused",
    "waiting_retry",
    "success",
    "failed",
]


class VideoTask(Base):
    """Independent video production workspace and lifecycle state machine."""

    __tablename__ = "video_task"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('commerce', 'brand', 'knowledge', 'story')",
            name="ck_video_task_content_type",
        ),
        CheckConstraint(
            "stage IN ('created', 'research', 'script_proposal', 'script_confirmed', "
            "'keyframes', 'shot_video', 'composition', 'finished')",
            name="ck_video_task_stage",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_confirmation', 'paused', "
            "'waiting_retry', 'success', 'failed')",
            name="ck_video_task_status",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_video_task_name"),
        Index("ix_video_task_user_cat", "user_id", "category_id"),
        Index("ix_video_task_user_status", "user_id", "status"),
        Index("ix_video_task_user_stage", "user_id", "stage"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.id", name="fk_video_task_user", ondelete="RESTRICT"),
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("category.id", name="fk_video_task_category", ondelete="RESTRICT"),
    )
    name: Mapped[str] = mapped_column(String(128))
    content_type: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(
        String(32), server_default=text("'created'"), default="created"
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'pending'"), default="pending"
    )
    input_key: Mapped[str] = mapped_column(String(255))
    checkpoint_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


DecisionAction = Literal[
    "skip_research",
    "select_script",
    "lock_spec",
    "confirm_storyboard",
    "confirm_keyframes",
    "trigger_composition",
]


class VideoDecision(Base):
    """User confirmation decision and specification lock audit record."""

    __tablename__ = "video_decision"
    __table_args__ = (
        CheckConstraint(
            "action IN ('skip_research', 'select_script', 'lock_spec', "
            "'confirm_storyboard', 'confirm_keyframes', 'trigger_composition')",
            name="ck_video_decision_action",
        ),
        UniqueConstraint(
            "task_id", "idempotency_key", name="uq_decision_task_idempotency"
        ),
        Index("ix_video_decision_task_created", "task_id", "created_at"),
        Index("ix_video_decision_actor", "actor_user_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("video_task.id", name="fk_video_decision_task", ondelete="RESTRICT"),
    )
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "user_account.id", name="fk_video_decision_actor", ondelete="RESTRICT"
        ),
    )
    action: Mapped[str] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )


ShotAttemptOperation = Literal["generate", "redo", "inpaint"]
AttemptStatus = Literal["pending", "running", "succeeded", "failed"]


class VideoShot(Base):
    """Atomic scene or camera cut within a video task."""

    __tablename__ = "video_shot"
    __table_args__ = (
        UniqueConstraint("task_id", "shot_no", name="uq_shot_task_shot_no"),
        Index("ix_video_shot_task_id", "task_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("video_task.id", name="fk_video_shot_task", ondelete="CASCADE"),
    )
    shot_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(128))
    duration_sec: Mapped[float] = mapped_column(Numeric(4, 1, asdecimal=False))
    selected_attempt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    needs_review: Mapped[bool] = mapped_column(
        Boolean, server_default=text("0"), default=False
    )
    version: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        onupdate=text("UTC_TIMESTAMP(6)"),
    )


class VideoAttempt(Base):
    """Specific rendering generation attempt or redo for a video shot."""

    __tablename__ = "video_attempt"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('generate', 'redo', 'inpaint')",
            name="ck_video_attempt_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_video_attempt_status",
        ),
        UniqueConstraint("shot_id", "attempt_no", name="uq_attempt_shot_attempt_no"),
        Index("ix_video_attempt_task_id", "task_id"),
        Index("ix_video_attempt_shot_id", "shot_id"),
        Index("ix_video_attempt_idempotency", "shot_id", "idempotency_key"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("video_task.id", name="fk_video_attempt_task", ondelete="CASCADE"),
    )
    shot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("video_shot.id", name="fk_video_attempt_shot", ondelete="CASCADE"),
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'pending'"), default="pending"
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_key: Mapped[str] = mapped_column(String(512))
    result_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


UsageMetricType = Literal["gpu_second", "storage_byte", "api_call"]


class UsageRecord(Base):
    """Audit billing record tracking GPU compute, storage, and API consumption."""

    __tablename__ = "usage_record"
    __table_args__ = (
        CheckConstraint(
            "metric_type IN ('gpu_second', 'storage_byte', 'api_call')",
            name="ck_usage_record_metric",
        ),
        CheckConstraint("quantity >= 0", name="ck_usage_record_quantity"),
        CheckConstraint("cost_cents >= 0", name="ck_usage_record_cost"),
        Index("ix_usage_record_user", "user_id", "recorded_at"),
        Index("ix_usage_record_task", "task_id", "recorded_at"),
        Index("ix_usage_record_attempt", "attempt_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_as_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.id", name="fk_usage_record_user", ondelete="CASCADE"),
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("video_task.id", name="fk_usage_record_task", ondelete="CASCADE"),
    )
    attempt_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "video_attempt.id", name="fk_usage_record_attempt", ondelete="SET NULL"
        ),
        nullable=True,
    )
    metric_type: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(Numeric(12, 4, asdecimal=False))
    cost_cents: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), default=0
    )
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )
