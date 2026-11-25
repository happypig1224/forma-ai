"""Unit tests for VideoDecision ORM mapping and table constraints."""

from datetime import UTC, datetime

from formaai.modules.production.models import VideoDecision


def test_video_decision_model_initialization() -> None:
    now = datetime.now(UTC)
    decision = VideoDecision(
        task_id=10,
        actor_user_id=101,
        action="lock_spec",
        target_key="tasks/101/10/proposals.json",
        details={"selected_script_index": 1, "aspect_ratio": "16:9"},
        idempotency_key="idemp_tok_12345678",
        created_at=now,
    )
    assert decision.task_id == 10
    assert decision.actor_user_id == 101
    assert decision.action == "lock_spec"
    assert decision.target_key == "tasks/101/10/proposals.json"
    assert decision.details["aspect_ratio"] == "16:9"
    assert decision.idempotency_key == "idemp_tok_12345678"
    assert decision.created_at == now
    assert decision.__tablename__ == "video_decision"
