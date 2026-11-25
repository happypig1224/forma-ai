"""Unit tests for shot and attempt domain schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.production.schemas import (
    AttemptListResponse,
    AttemptResponse,
    ShotListResponse,
    ShotRedoRequest,
    ShotResponse,
    ShotReviewRequest,
    ShotSelectRequest,
)


def test_attempt_response_serialization() -> None:
    now = datetime.now(UTC)
    attempt = AttemptResponse(
        id=10,
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="redo",
        status="succeeded",
        input_key="tasks/101/1/shots/2/attempt_1_input.json",
        result_key="tasks/101/1/shots/2/attempt_1_result.mp4",
        error_code=None,
        error_message=None,
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    assert attempt.id == 10
    assert attempt.operation == "redo"
    assert attempt.status == "succeeded"

    listing = AttemptListResponse(items=[attempt], total=1)
    assert len(listing.items) == 1
    assert listing.total == 1


def test_shot_response_serialization() -> None:
    now = datetime.now(UTC)
    attempt = AttemptResponse(
        id=10,
        task_id=1,
        shot_id=2,
        attempt_no=1,
        operation="generate",
        status="succeeded",
        input_key="tasks/101/1/shots/2/attempt_1_input.json",
        result_key="tasks/101/1/shots/2/attempt_1_result.mp4",
        error_code=None,
        error_message=None,
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    shot = ShotResponse(
        id=2,
        task_id=1,
        shot_no=1,
        title="Hero shot opening door",
        duration_sec=3.5,
        selected_attempt_id=10,
        selected_attempt=attempt,
        needs_review=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    assert shot.duration_sec == 3.5
    assert shot.selected_attempt is not None
    assert shot.selected_attempt.id == 10

    shot_list = ShotListResponse(items=[shot], total=1)
    assert shot_list.total == 1
    assert shot_list.items[0].shot_no == 1


def test_shot_redo_request_validation() -> None:
    req = ShotRedoRequest(
        idempotency_key="  idemp_key_test_1234  ",
        prompt="  A close-up shot of a robotic hand   ",
        negative_prompt="blurry, distorted",
        reference_image_key="library/101/char_ref.png",
    )
    assert req.idempotency_key == "idemp_key_test_1234"
    assert req.prompt == "A close-up shot of a robotic hand"
    assert req.negative_prompt == "blurry, distorted"
    assert req.reference_image_key == "library/101/char_ref.png"


def test_shot_redo_request_rejects_empty_and_extras() -> None:
    with pytest.raises(ValidationError):
        ShotRedoRequest(
            idempotency_key="   ",
            prompt="valid prompt",
        )

    with pytest.raises(ValidationError):
        ShotRedoRequest(
            idempotency_key="valid_token_123",
            prompt="   ",
        )

    with pytest.raises(ValidationError):
        ShotRedoRequest(
            idempotency_key="valid_token_123",
            prompt="valid prompt",
            extra_field="unsupported",  # type: ignore[call-arg]
        )


def test_shot_select_request_validation() -> None:
    req = ShotSelectRequest(attempt_id=5, expected_version=2)
    assert req.attempt_id == 5
    assert req.expected_version == 2

    with pytest.raises(ValidationError):
        ShotSelectRequest(attempt_id=0, expected_version=2)

    with pytest.raises(ValidationError):
        ShotSelectRequest(attempt_id=5, expected_version=-1)

    with pytest.raises(ValidationError):
        ShotSelectRequest(attempt_id=5, expected_version=2, extra="foo")  # type: ignore[call-arg]


def test_shot_review_request_validation() -> None:
    req = ShotReviewRequest(expected_version=0)
    assert req.expected_version == 0

    with pytest.raises(ValidationError):
        ShotReviewRequest(expected_version=-1)

    with pytest.raises(ValidationError):
        ShotReviewRequest(expected_version=0, extra="bar")  # type: ignore[call-arg]
