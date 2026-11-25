"""Unit tests for composition, completion, cancellation, and cleanup schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.production.schemas import (
    TaskCancelRequest,
    TaskCleanupRequest,
    TaskCleanupResponse,
    TaskCompleteRequest,
    TaskComposeRequest,
)


def test_task_compose_request_validation() -> None:
    req = TaskComposeRequest(
        idempotency_key="  compose_tok_12345678  ",
        expected_version=3,
    )
    assert req.idempotency_key == "compose_tok_12345678"
    assert req.expected_version == 3

    # Reject whitespace or empty
    with pytest.raises(ValidationError):
        TaskComposeRequest(idempotency_key="   ", expected_version=0)

    # Reject too short
    with pytest.raises(ValidationError):
        TaskComposeRequest(idempotency_key="short", expected_version=0)

    # Reject negative version
    with pytest.raises(ValidationError):
        TaskComposeRequest(idempotency_key="compose_tok_12345678", expected_version=-1)

    # Reject extra fields
    with pytest.raises(ValidationError):
        TaskComposeRequest(
            idempotency_key="compose_tok_12345678",
            expected_version=0,
            extra="foo",  # type: ignore[call-arg]
        )


def test_task_complete_request_validation() -> None:
    req = TaskCompleteRequest(
        final_video_key="  library/101/tasks/1/final_output.mp4  ",
        expected_version=4,
    )
    assert req.final_video_key == "library/101/tasks/1/final_output.mp4"
    assert req.expected_version == 4

    # Reject whitespace or empty
    with pytest.raises(ValidationError):
        TaskCompleteRequest(final_video_key="   ", expected_version=4)

    # Reject negative version
    with pytest.raises(ValidationError):
        TaskCompleteRequest(
            final_video_key="library/101/tasks/1/final_output.mp4",
            expected_version=-1,
        )

    # Reject extra fields
    with pytest.raises(ValidationError):
        TaskCompleteRequest(
            final_video_key="library/101/tasks/1/final_output.mp4",
            expected_version=0,
            extra="bar",  # type: ignore[call-arg]
        )


def test_task_cancel_request_validation() -> None:
    req_with_reason = TaskCancelRequest(
        expected_version=2,
        reason="User requested abort",
    )
    assert req_with_reason.expected_version == 2
    assert req_with_reason.reason == "User requested abort"

    req_no_reason = TaskCancelRequest(expected_version=0)
    assert req_no_reason.expected_version == 0
    assert req_no_reason.reason is None

    # Reject negative version
    with pytest.raises(ValidationError):
        TaskCancelRequest(expected_version=-1)

    # Reject extra fields
    with pytest.raises(ValidationError):
        TaskCancelRequest(
            expected_version=0,
            extra="baz",  # type: ignore[call-arg]
        )


def test_task_cleanup_request_and_response() -> None:
    req_default = TaskCleanupRequest()
    assert req_default.keep_selected_only is True

    req_custom = TaskCleanupRequest(keep_selected_only=False)
    assert req_custom.keep_selected_only is False

    with pytest.raises(ValidationError):
        TaskCleanupRequest(invalid_param=True)  # type: ignore[call-arg]

    now = datetime.now(UTC)
    resp = TaskCleanupResponse(
        task_id=1,
        reclaimed_files_count=12,
        cleaned_at=now,
    )
    assert resp.task_id == 1
    assert resp.reclaimed_files_count == 12
    assert resp.cleaned_at == now
