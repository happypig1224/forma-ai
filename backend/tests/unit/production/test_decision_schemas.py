"""Unit tests for decision schemas, spec validation, and idempotency payloads."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.production.schemas import (
    DecisionDetails,
    DecisionListResponse,
    DecisionResponse,
    DecisionSubmitRequest,
    ProductionSpec,
)


def test_valid_production_spec() -> None:
    spec = ProductionSpec(
        aspect_ratio="16:9",
        resolution="1080p",
        target_duration_sec=30,
        fps=30,
    )
    assert spec.aspect_ratio == "16:9"
    assert spec.resolution == "1080p"
    assert spec.target_duration_sec == 30
    assert spec.fps == 30


def test_production_spec_validation_boundaries() -> None:
    # Invalid aspect ratio
    with pytest.raises(ValidationError):
        ProductionSpec(
            aspect_ratio="21:9",  # type: ignore[arg-type]
            resolution="1080p",
            target_duration_sec=30,
        )

    # Invalid resolution
    with pytest.raises(ValidationError):
        ProductionSpec(
            aspect_ratio="16:9",
            resolution="8k",  # type: ignore[arg-type]
            target_duration_sec=30,
        )

    # Target duration out of range (< 5s or > 180s)
    with pytest.raises(ValidationError):
        ProductionSpec(
            aspect_ratio="16:9",
            resolution="1080p",
            target_duration_sec=4,
        )

    with pytest.raises(ValidationError):
        ProductionSpec(
            aspect_ratio="16:9",
            resolution="1080p",
            target_duration_sec=181,
        )

    # Extra fields forbidden
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProductionSpec(
            aspect_ratio="16:9",
            resolution="1080p",
            target_duration_sec=30,
            rogue_field=123,  # type: ignore[call-arg]
        )


def test_valid_decision_submit_request() -> None:
    req = DecisionSubmitRequest(
        idempotency_key="dec_key_20261123_001",
        expected_version=0,
        action="lock_spec",
        target_key="tasks/101/1/proposals/script_v1.json",
        details=DecisionDetails(
            selected_script_index=1,
            spec=ProductionSpec(
                aspect_ratio="9:16",
                resolution="1080p",
                target_duration_sec=15,
                fps=60,
            ),
            notes="Vertical mobile fast-paced commercial",
            extra_data={"color_grading": "cinematic"},
        ),
    )
    assert req.idempotency_key == "dec_key_20261123_001"
    assert req.expected_version == 0
    assert req.action == "lock_spec"
    assert req.details.selected_script_index == 1
    assert req.details.spec is not None
    assert req.details.spec.aspect_ratio == "9:16"


def test_decision_submit_request_validation_errors() -> None:
    # Short idempotency key (< 8 chars)
    with pytest.raises(ValidationError):
        DecisionSubmitRequest(
            idempotency_key="short",
            expected_version=0,
            action="select_script",
            target_key="tasks/101/1/snap.json",
            details=DecisionDetails(selected_script_index=0),
        )

    # Blank idempotency key or target key
    with pytest.raises(ValidationError, match="cannot be empty"):
        DecisionSubmitRequest(
            idempotency_key="        ",
            expected_version=0,
            action="select_script",
            target_key="tasks/101/1/snap.json",
            details=DecisionDetails(selected_script_index=0),
        )

    with pytest.raises(ValidationError, match="cannot be empty"):
        DecisionSubmitRequest(
            idempotency_key="valid_token_12345",
            expected_version=0,
            action="select_script",
            target_key="   ",
            details=DecisionDetails(selected_script_index=0),
        )

    # Invalid action
    with pytest.raises(ValidationError):
        DecisionSubmitRequest(
            idempotency_key="valid_token_12345",
            expected_version=0,
            action="unsupported_action",  # type: ignore[arg-type]
            target_key="tasks/101/1/snap.json",
            details=DecisionDetails(),
        )


def test_decision_response_serialization() -> None:
    now = datetime.now(UTC)
    data = {
        "id": 1,
        "task_id": 10,
        "action": "lock_spec",
        "target_key": "tasks/101/10/target.json",
        "details": {
            "selected_script_index": 2,
            "spec": {
                "aspect_ratio": "16:9",
                "resolution": "1080p",
                "target_duration_sec": 45,
                "fps": 30,
            },
        },
        "idempotency_key": "idemp_token_12345",
        "created_at": now,
    }
    resp = DecisionResponse.model_validate(data)
    assert resp.id == 1
    assert resp.task_id == 10
    assert resp.action == "lock_spec"
    assert resp.details["selected_script_index"] == 2

    list_resp = DecisionListResponse(items=[resp], total=1)
    assert list_resp.total == 1
    assert len(list_resp.items) == 1
