"""Unit tests for usage billing and quota consumption schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.production.schemas import (
    UsageCreateRequest,
    UsageListResponse,
    UsageResponse,
    UsageSummary,
)


def test_usage_create_request_validation() -> None:
    req_attempt = UsageCreateRequest(
        attempt_id=12,
        metric_type="gpu_second",
        quantity=34.5,
        cost_cents=150,
    )
    assert req_attempt.attempt_id == 12
    assert req_attempt.metric_type == "gpu_second"
    assert req_attempt.quantity == 34.5
    assert req_attempt.cost_cents == 150

    req_task_level = UsageCreateRequest(
        metric_type="api_call",
        quantity=3.0,
    )
    assert req_task_level.attempt_id is None
    assert req_task_level.cost_cents == 0

    # Negative quantity rejected
    with pytest.raises(ValidationError):
        UsageCreateRequest(metric_type="gpu_second", quantity=-1.0)

    # Negative cost rejected
    with pytest.raises(ValidationError):
        UsageCreateRequest(metric_type="gpu_second", quantity=10.0, cost_cents=-5)

    # Invalid metric type rejected
    with pytest.raises(ValidationError):
        UsageCreateRequest(
            metric_type="invalid_metric",  # type: ignore[arg-type]
            quantity=10.0,
        )

    # Attempt ID <= 0 rejected
    with pytest.raises(ValidationError):
        UsageCreateRequest(
            attempt_id=0,
            metric_type="storage_byte",
            quantity=1024.0,
        )


def test_usage_response_and_summary_serialization() -> None:
    now = datetime.now(UTC)
    record1 = UsageResponse(
        id=1,
        user_id=101,
        task_id=1,
        attempt_id=10,
        metric_type="gpu_second",
        quantity=15.5,
        cost_cents=50,
        recorded_at=now,
    )
    record2 = UsageResponse(
        id=2,
        user_id=101,
        task_id=1,
        attempt_id=None,
        metric_type="api_call",
        quantity=2.0,
        cost_cents=20,
        recorded_at=now,
    )

    summary = UsageSummary(
        total_gpu_seconds=15.5,
        total_storage_bytes=0.0,
        total_api_calls=2.0,
        total_cost_cents=70,
    )

    list_resp = UsageListResponse(
        items=[record1, record2],
        total=2,
        summary=summary,
    )
    assert list_resp.total == 2
    assert list_resp.summary.total_cost_cents == 70
    assert list_resp.items[0].metric_type == "gpu_second"
    assert list_resp.items[1].attempt_id is None
