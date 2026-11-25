"""Checks for startup behavior, secret handling and UTC boundaries."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.exc import OperationalError

from formaai.app import create_app
from formaai.config import Settings
from formaai.infrastructure.database import UTCDateTime


def settings() -> Settings:
    """Use synthetic credentials without reading a developer's .env."""
    return Settings(
        _env_file=None,
        mysql_database="formaai_test",
        mysql_user="test",
        mysql_password="synthetic-only",
        redis_url="redis://127.0.0.1:1/0",
        jwt_access_secret="synthetic-access-secret-strictly-32-bytes!",
        jwt_refresh_secret="synthetic-refresh-secret-strictly-32-bytes!",
    )


def test_secrets_are_masked_and_empty_password_is_rejected():
    assert "synthetic-only" not in repr(settings())
    assert "synthetic-access" not in repr(settings())
    assert "synthetic-refresh" not in repr(settings())
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            mysql_database="test",
            mysql_user="test",
            mysql_password="",
            redis_url="redis://localhost/0",
        )


def test_utc_conversion_preserves_instant_and_precision():
    column_type = UTCDateTime()
    source = datetime(2026, 11, 21, 9, 30, 10, 123456, timezone(timedelta(hours=8)))
    stored = column_type.process_bind_param(source, dialect())
    assert stored == datetime(2026, 11, 21, 1, 30, 10, 123456)
    restored = column_type.process_result_value(stored, dialect())
    assert restored == source
    assert restored.tzinfo is UTC


def test_naive_timestamp_is_not_silently_assumed_local_time():
    with pytest.raises(ValueError, match="Timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 11, 21), dialect())


def test_live_is_independent_and_ready_does_not_leak_connection_errors():
    engine = MagicMock()
    cache = MagicMock()
    engine.connect.side_effect = OperationalError(
        "SELECT 1", {}, RuntimeError("sensitive connection details")
    )
    with (
        patch("formaai.app.build_engine", return_value=engine),
        patch("formaai.app.Redis.from_url", return_value=cache),
    ):
        app = create_app(settings())
        engine.connect.assert_not_called()
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            engine.connect.assert_not_called()
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
        cache.close.assert_called_once()
        engine.dispose.assert_called_once()
