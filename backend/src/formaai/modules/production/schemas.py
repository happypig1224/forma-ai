"""Pydantic schemas for video production task creation, queries, and transitions."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from formaai.modules.production.models import (
    DecisionAction,
    TaskContentType,
    TaskStage,
    TaskStatus,
)


class TaskInitialInput(BaseModel):
    """Immutable snapshot payload fixed at task creation."""

    model_config = ConfigDict(extra="forbid")

    theme: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Core theme, proposition or topic of the video",
    )
    target_market: str | None = Field(
        default=None,
        max_length=200,
        description="Target audience or marketplace geography",
    )
    material_ids: list[int] = Field(
        default_factory=list,
        description="Reusable library material IDs to reference",
    )
    asset_ids: list[int] = Field(
        default_factory=list,
        description="Reusable library visual asset IDs to constrain/reference",
    )
    allow_ai_expansion: bool = Field(
        default=True,
        description="Whether AI may synthesize elements or strictly use assets",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional initial creator notes or style directives",
    )

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Theme cannot be empty or whitespace only")
        return clean

    @field_validator("target_market", "notes")
    @classmethod
    def clean_optional_strings(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        return clean if clean else None


class TaskCreateRequest(BaseModel):
    """Request payload to create an isolated production workspace."""

    model_config = ConfigDict(extra="forbid")

    category_id: int = Field(..., gt=0, description="Parent category ID")
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Descriptive task project name",
    )
    content_type: TaskContentType = Field(
        ..., description="Content type must match the parent category"
    )
    initial_input: TaskInitialInput = Field(
        ..., description="Initial creative inputs to snapshot"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Task name cannot be empty or whitespace only")
        return clean


class TaskResponse(BaseModel):
    """Public task envelope hiding internal user_id."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    name: str
    content_type: TaskContentType
    stage: TaskStage
    status: TaskStatus
    input_key: str
    checkpoint_key: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class TaskListResponse(BaseModel):
    """Paginated collection of production tasks."""

    model_config = ConfigDict(from_attributes=True)

    items: list[TaskResponse]
    total: int


class TaskStateTransitionRequest(BaseModel):
    """Optimistic concurrency payload for state change operations."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ..., ge=0, description="Expected task version for optimistic lock"
    )
    reason: str | None = Field(
        default=None, max_length=200, description="Optional transition reason"
    )


AspectRatio = Literal["16:9", "9:16", "1:1", "4:3", "3:4"]
Resolution = Literal["720p", "1080p", "2k", "4k"]


class ProductionSpec(BaseModel):
    """Locked technical output specification for subsequent production stages."""

    model_config = ConfigDict(extra="forbid")

    aspect_ratio: AspectRatio = Field(default="16:9", description="Video aspect ratio")
    resolution: Resolution = Field(
        default="1080p", description="Rendered video resolution"
    )
    target_duration_sec: int = Field(
        ...,
        ge=5,
        le=180,
        description="Target duration in seconds (5s - 180s)",
    )
    fps: int = Field(
        default=30,
        ge=24,
        le=60,
        description="Target video frame rate",
    )


class DecisionDetails(BaseModel):
    """Structured action details for user decisions and confirmations."""

    model_config = ConfigDict(extra="forbid")

    selected_script_index: int | None = Field(
        default=None,
        ge=0,
        description="Chosen script candidate index (0-based)",
    )
    spec: ProductionSpec | None = Field(
        default=None,
        description="Production specification locked alongside script",
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Optional decision note or directive",
    )
    extra_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage-specific arbitrary payload",
    )


class DecisionSubmitRequest(BaseModel):
    """Request payload to submit a confirmed decision with idempotency token."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="Unique client idempotency token for deduplication",
    )
    expected_version: int = Field(
        ...,
        ge=0,
        description="Task version expected for optimistic concurrency control",
    )
    action: DecisionAction = Field(
        ...,
        description="Specific confirmation action being performed",
    )
    target_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Reference key of the candidate snapshot being confirmed",
    )
    details: DecisionDetails = Field(
        ...,
        description="Action-specific decision payload",
    )

    @field_validator("idempotency_key", "target_key")
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Field cannot be empty or whitespace only")
        return clean


class DecisionResponse(BaseModel):
    """Auditable decision record envelope."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    action: DecisionAction
    target_key: str
    details: dict[str, Any]
    idempotency_key: str
    created_at: datetime


class DecisionListResponse(BaseModel):
    """Collection of decision records for a given task."""

    model_config = ConfigDict(from_attributes=True)

    items: list[DecisionResponse]
    total: int


ShotAttemptOperation = Literal["generate", "redo", "inpaint"]
AttemptStatus = Literal["pending", "running", "succeeded", "failed"]


class AttemptResponse(BaseModel):
    """Artifact candidate rendering attempt envelope."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    shot_id: int
    attempt_no: int
    operation: ShotAttemptOperation
    status: AttemptStatus
    input_key: str
    result_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AttemptListResponse(BaseModel):
    """Collection of attempts for a shot."""

    model_config = ConfigDict(from_attributes=True)

    items: list[AttemptResponse]
    total: int


class ShotResponse(BaseModel):
    """Public shot envelope with selected attempt details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    shot_no: int
    title: str
    duration_sec: float
    selected_attempt_id: int | None = None
    selected_attempt: AttemptResponse | None = None
    needs_review: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ShotListResponse(BaseModel):
    """Ordered collection of shots belonging to a video task."""

    model_config = ConfigDict(from_attributes=True)

    items: list[ShotResponse]
    total: int


class ShotRedoRequest(BaseModel):
    """Client request to trigger a single shot redo."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="Unique client idempotency token for deduplication",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Redo generation prompt describing visual and action",
    )
    negative_prompt: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional negative prompt for redo generation",
    )
    reference_image_key: str | None = Field(
        default=None,
        max_length=512,
        description="Optional reference image asset key in user storage",
    )

    @field_validator("idempotency_key", "prompt")
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Field cannot be empty or whitespace only")
        return clean


class ShotSelectRequest(BaseModel):
    """Payload to select an attempt for a shot and trigger downstream review."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: int = Field(
        ...,
        gt=0,
        description="ID of the completed attempt to set as the active shot candidate",
    )
    expected_version: int = Field(
        ...,
        ge=0,
        description="Shot version expected for optimistic concurrency control",
    )


class ShotReviewRequest(BaseModel):
    """Payload to clear downstream review mark on a confirmed shot."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(
        ...,
        ge=0,
        description="Shot version expected for optimistic concurrency control",
    )


class TaskComposeRequest(BaseModel):
    """Payload to trigger video composition from confirmed shots."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="Unique client idempotency token for deduplication",
    )
    expected_version: int = Field(
        ...,
        ge=0,
        description="Expected task version for optimistic concurrency control",
    )

    @field_validator("idempotency_key")
    @classmethod
    def clean_idempotency_key(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Idempotency key cannot be empty or whitespace only")
        return clean


class TaskCompleteRequest(BaseModel):
    """Payload to mark video composition as finished with resulting asset key."""

    model_config = ConfigDict(extra="forbid")

    final_video_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Storage key of the rendered composition video",
    )
    expected_version: int = Field(
        ...,
        ge=0,
        description="Expected task version for optimistic concurrency control",
    )

    @field_validator("final_video_key")
    @classmethod
    def clean_final_video_key(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Final video key cannot be empty or whitespace only")
        return clean


class TaskCancelRequest(BaseModel):
    """Payload to cancel an in-progress video task."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(
        ...,
        ge=0,
        description="Expected task version for optimistic concurrency control",
    )
    reason: str | None = Field(
        default=None,
        max_length=255,
        description="Optional reason for cancelling the task",
    )


class TaskCleanupRequest(BaseModel):
    """Payload to configure temporary artifact reclamation for a task."""

    model_config = ConfigDict(extra="forbid")

    keep_selected_only: bool = Field(
        default=True,
        description="Whether to reclaim unselected attempt temporary files",
    )


class TaskCleanupResponse(BaseModel):
    """Result envelope of reclaimed derivative artifacts."""

    model_config = ConfigDict(from_attributes=True)

    task_id: int
    reclaimed_files_count: int
    cleaned_at: datetime


UsageMetricType = Literal["gpu_second", "storage_byte", "api_call"]


class UsageCreateRequest(BaseModel):
    """Client/worker report payload for compute or resource consumption."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: int | None = Field(
        default=None,
        gt=0,
        description="Optional attempt ID for shot generation",
    )
    metric_type: UsageMetricType = Field(
        ...,
        description="Type of usage metric recorded",
    )
    quantity: float = Field(
        ...,
        ge=0,
        description="Quantity of metric consumed",
    )
    cost_cents: int = Field(
        default=0,
        ge=0,
        description="Cost converted to cents or credit balance",
    )


class UsageResponse(BaseModel):
    """Auditable usage billing record envelope."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    task_id: int
    attempt_id: int | None = None
    metric_type: UsageMetricType
    quantity: float
    cost_cents: int
    recorded_at: datetime


class UsageSummary(BaseModel):
    """Aggregated consumption metrics for a video production task."""

    model_config = ConfigDict(from_attributes=True)

    total_gpu_seconds: float
    total_storage_bytes: float
    total_api_calls: float
    total_cost_cents: int


class UsageListResponse(BaseModel):
    """Collection of task usage audit records and aggregated totals."""

    model_config = ConfigDict(from_attributes=True)

    items: list[UsageResponse]
    total: int
    summary: UsageSummary
