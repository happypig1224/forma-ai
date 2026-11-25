"""Unit tests for production task schemas and input validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from formaai.modules.production.schemas import (
    TaskCreateRequest,
    TaskInitialInput,
    TaskListResponse,
    TaskResponse,
    TaskStateTransitionRequest,
)


def test_valid_task_initial_input() -> None:
    inp = TaskInitialInput(
        theme="  Eco-friendly wireless earbuds launch  ",
        target_market="North America, Gen-Z",
        material_ids=[1, 2],
        asset_ids=[10, 11],
        allow_ai_expansion=False,
        notes="High energy, sleek typography",
    )
    assert inp.theme == "Eco-friendly wireless earbuds launch"
    assert inp.target_market == "North America, Gen-Z"
    assert inp.material_ids == [1, 2]
    assert inp.asset_ids == [10, 11]
    assert inp.allow_ai_expansion is False
    assert inp.notes == "High energy, sleek typography"


def test_task_initial_input_whitespace_and_empty_validation() -> None:
    with pytest.raises(ValidationError, match="Theme cannot be empty"):
        TaskInitialInput(theme="   ")

    with pytest.raises(ValidationError):
        TaskInitialInput(theme="")

    # Optional fields stripped to None if blank
    inp = TaskInitialInput(theme="Valid theme", target_market="   ", notes="   ")
    assert inp.target_market is None
    assert inp.notes is None


def test_valid_task_create_request() -> None:
    req = TaskCreateRequest(
        category_id=1,
        name="  Summer Campaign Video  ",
        content_type="brand",
        initial_input=TaskInitialInput(
            theme="Brand heritage and future vision",
            allow_ai_expansion=True,
        ),
    )
    assert req.category_id == 1
    assert req.name == "Summer Campaign Video"
    assert req.content_type == "brand"
    assert req.initial_input.theme == "Brand heritage and future vision"
    assert req.initial_input.allow_ai_expansion is True


def test_task_create_request_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskCreateRequest.model_validate(
            {
                "category_id": 1,
                "name": "Task 1",
                "content_type": "commerce",
                "initial_input": {"theme": "Product ad"},
                "malicious_extra": "hack",
            }
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskInitialInput.model_validate(
            {
                "theme": "Valid theme",
                "unexpected_field": 123,
            }
        )


def test_task_create_request_invalid_name_and_category() -> None:
    # Blank name
    with pytest.raises(ValidationError, match="Task name cannot be empty"):
        TaskCreateRequest(
            category_id=1,
            name="   ",
            content_type="brand",
            initial_input=TaskInitialInput(theme="Theme"),
        )

    # Non-positive category_id
    with pytest.raises(ValidationError):
        TaskCreateRequest(
            category_id=0,
            name="Valid Name",
            content_type="brand",
            initial_input=TaskInitialInput(theme="Theme"),
        )


def test_task_create_request_invalid_content_type() -> None:
    with pytest.raises(ValidationError):
        TaskCreateRequest(
            category_id=1,
            name="Valid Name",
            content_type="invalid_type",  # type: ignore[arg-type]
            initial_input=TaskInitialInput(theme="Theme"),
        )


def test_task_response_serialization() -> None:
    now = datetime.now(UTC)
    data = {
        "id": 1001,
        "category_id": 42,
        "name": "New Video Project",
        "content_type": "knowledge",
        "stage": "created",
        "status": "pending",
        "input_key": "tasks/1/1001/input_v1.json",
        "checkpoint_key": None,
        "version": 0,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }
    resp = TaskResponse.model_validate(data)
    assert resp.id == 1001
    assert resp.stage == "created"
    assert resp.status == "pending"
    assert resp.checkpoint_key is None
    assert resp.finished_at is None

    # Verify TaskListResponse
    list_resp = TaskListResponse(items=[resp], total=1)
    assert list_resp.total == 1
    assert len(list_resp.items) == 1
    assert list_resp.items[0].name == "New Video Project"


def test_task_state_transition_request() -> None:
    req = TaskStateTransitionRequest(version=3, reason="Paused by user")
    assert req.version == 3
    assert req.reason == "Paused by user"

    # Negative version rejected
    with pytest.raises(ValidationError):
        TaskStateTransitionRequest(version=-1)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        TaskStateTransitionRequest.model_validate({"version": 1, "rogue_field": "val"})
