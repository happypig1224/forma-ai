"""Unit tests for VideoTask ORM mapping and entity attributes."""

from datetime import UTC, datetime

from formaai.modules.production.models import VideoTask


def test_video_task_model_initialization() -> None:
    now = datetime.now(UTC)
    task = VideoTask(
        user_id=101,
        category_id=1,
        name="Campaign Promo",
        content_type="brand",
        stage="created",
        status="pending",
        input_key="tasks/101/1/input_v1.json",
        checkpoint_key=None,
        version=0,
        created_at=now,
        updated_at=now,
        finished_at=None,
    )
    assert task.user_id == 101
    assert task.category_id == 1
    assert task.name == "Campaign Promo"
    assert task.content_type == "brand"
    assert task.stage == "created"
    assert task.status == "pending"
    assert task.input_key == "tasks/101/1/input_v1.json"
    assert task.checkpoint_key is None
    assert task.version == 0
    assert task.finished_at is None
    assert task.__tablename__ == "video_task"
