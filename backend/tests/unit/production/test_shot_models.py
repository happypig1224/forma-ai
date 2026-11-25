"""Unit tests for VideoShot and VideoAttempt ORM models."""

from sqlalchemy import inspect

from formaai.modules.production.models import VideoAttempt, VideoShot


def test_video_shot_model_mapping() -> None:
    mapper = inspect(VideoShot)
    assert mapper.tables[0].name == "video_shot"

    columns = {c.name for c in mapper.columns}
    expected_cols = {
        "id",
        "task_id",
        "shot_no",
        "title",
        "duration_sec",
        "selected_attempt_id",
        "needs_review",
        "version",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(columns)

    shot = VideoShot(
        task_id=1,
        shot_no=1,
        title="Opening Scene",
        duration_sec=4.0,
        selected_attempt_id=None,
        needs_review=False,
        version=0,
    )
    assert shot.task_id == 1
    assert shot.shot_no == 1
    assert shot.duration_sec == 4.0
    assert shot.needs_review is False
    assert shot.version == 0
    assert shot.selected_attempt_id is None


def test_video_attempt_model_mapping() -> None:
    mapper = inspect(VideoAttempt)
    assert mapper.tables[0].name == "video_attempt"

    columns = {c.name for c in mapper.columns}
    expected_cols = {
        "id",
        "task_id",
        "shot_id",
        "attempt_no",
        "operation",
        "status",
        "idempotency_key",
        "input_key",
        "result_key",
        "error_code",
        "error_message",
        "created_at",
        "started_at",
        "finished_at",
    }
    assert expected_cols.issubset(columns)

    attempt = VideoAttempt(
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="redo",
        status="pending",
        input_key="tasks/1/shots/2/attempt_1_input.json",
        result_key=None,
    )
    assert attempt.task_id == 1
    assert attempt.shot_id == 2
    assert attempt.attempt_no == 1
    assert attempt.operation == "redo"
    assert attempt.status == "pending"
    assert attempt.result_key is None
