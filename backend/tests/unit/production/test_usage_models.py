"""Unit tests for UsageRecord ORM model and database table mappings."""

from sqlalchemy import inspect

from formaai.modules.production.models import UsageRecord


def test_usage_record_model_mapping() -> None:
    mapper = inspect(UsageRecord)
    assert mapper.tables[0].name == "usage_record"

    columns = {c.name for c in mapper.columns}
    expected_cols = {
        "id",
        "user_id",
        "task_id",
        "attempt_id",
        "metric_type",
        "quantity",
        "cost_cents",
        "recorded_at",
    }
    assert expected_cols.issubset(columns)

    rec = UsageRecord(
        user_id=101,
        task_id=1,
        attempt_id=10,
        metric_type="gpu_second",
        quantity=25.0,
        cost_cents=100,
    )
    assert rec.user_id == 101
    assert rec.task_id == 1
    assert rec.attempt_id == 10
    assert rec.metric_type == "gpu_second"
    assert rec.quantity == 25.0
    assert rec.cost_cents == 100
