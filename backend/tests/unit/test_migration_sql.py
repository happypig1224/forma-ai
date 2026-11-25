"""Offline MySQL compilation; this is not a live database acceptance test."""

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL


def test_initial_migration_compiles_for_mysql_without_opening_connections():
    output = StringIO()
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = URL.create("mysql+pymysql", database="test")
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE user_account" in sql
    assert "CREATE TABLE category" in sql
    assert "CREATE TABLE material" in sql
    assert "CREATE TABLE asset" in sql
    assert "CREATE TABLE video_task" in sql
    assert "CREATE TABLE video_decision" in sql
    assert "CREATE TABLE video_shot" in sql
    assert "CREATE TABLE video_attempt" in sql
    assert "CREATE TABLE usage_record" in sql
    assert "fk_material_user" in sql
    assert "fk_material_category" in sql
    assert "fk_asset_user" in sql
    assert "fk_asset_category" in sql
    assert "fk_video_task_user" in sql
    assert "fk_video_task_category" in sql
    assert "fk_video_decision_task" in sql
    assert "fk_video_decision_actor" in sql
    assert "fk_video_shot_task" in sql
    assert "fk_video_attempt_task" in sql
    assert "fk_video_attempt_shot" in sql
    assert "fk_usage_record_user" in sql
    assert "fk_usage_record_task" in sql
    assert "fk_usage_record_attempt" in sql
    assert "ck_material_type" in sql
    assert "ck_asset_name" in sql
    assert "ck_asset_size" in sql
    assert "ck_video_task_content_type" in sql
    assert "ck_video_task_stage" in sql
    assert "ck_video_task_status" in sql
    assert "ck_video_task_name" in sql
    assert "ck_video_decision_action" in sql
    assert "ck_video_attempt_operation" in sql
    assert "ck_video_attempt_status" in sql
    assert "ck_usage_record_metric" in sql
    assert "ck_usage_record_quantity" in sql
    assert "ck_usage_record_cost" in sql
    assert "uq_decision_task_idempotency" in sql
    assert "uq_shot_task_shot_no" in sql
    assert "uq_attempt_shot_attempt_no" in sql
    assert "ix_video_shot_task_id" in sql
    assert "ix_video_attempt_shot_id" in sql
    assert "ix_video_attempt_idempotency" in sql
    assert "ix_usage_record_user" in sql
    assert "ix_usage_record_task" in sql
    assert "ix_usage_record_attempt" in sql
    assert "ix_material_user_cat" in sql
    assert "ix_material_user_type" in sql
    assert "ix_asset_user_cat" in sql
    assert "ix_asset_user_mime" in sql
    assert "ix_video_task_user_cat" in sql
    assert "ix_video_task_user_status" in sql
    assert "ix_video_task_user_stage" in sql
    assert "ix_video_decision_task_created" in sql
    assert "ix_video_decision_actor" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "uq_user_account_email" in sql
    assert "uq_category_user_type_name" in sql
    assert "DATETIME(6)" in sql
    assert "CREATE TABLE identity_session" not in sql
    assert "CREATE TABLE identity_email_verification" not in sql
