"""Configuration precedence, validation, redaction and actual app wiring."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from formaai.__main__ import main
from formaai.app import create_app
from formaai.config import ConfigurationError, Settings, load_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Never read a developer's .env or inherit their service credentials."""
    for key in list(os.environ):
        if key.startswith("FORMAAI_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORMAAI_MYSQL_DATABASE", "config_test")
    monkeypatch.setenv("FORMAAI_MYSQL_USER", "test")
    monkeypatch.setenv("FORMAAI_MYSQL_PASSWORD", "synthetic-secret")
    monkeypatch.setenv("FORMAAI_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(
        "FORMAAI_JWT_ACCESS_SECRET", "synthetic-access-secret-strictly-32-bytes!"
    )
    monkeypatch.setenv(
        "FORMAAI_JWT_REFRESH_SECRET", "synthetic-refresh-secret-strictly-32-bytes!"
    )
    # Track keys which the CLI itself is allowed to override.
    for key in ("HOST", "PORT", "RELOAD", "LOG_LEVEL"):
        monkeypatch.setenv(
            f"FORMAAI_{key}",
            {
                "HOST": "127.0.0.1",
                "PORT": "8000",
                "RELOAD": "false",
                "LOG_LEVEL": "info",
            }[key],
        )


def test_explicit_overrides_environment_then_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("FORMAAI_PORT")
    (tmp_path / ".env").write_text("FORMAAI_PORT=8100\n", encoding="utf-8")
    assert load_settings().port == 8100
    monkeypatch.setenv("FORMAAI_PORT", "8200")
    assert load_settings().port == 8200
    assert load_settings({"port": 8300}).port == 8300


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": 0},
        {"mysql_pool_size": 0},
        {"mysql_max_overflow": -1},
        {"redis_max_connections": 0},
        {"redis_socket_timeout": 0},
        {"mysql_user": "   "},
        {"mysql_password": ""},
        {"jwt_access_secret": "short-secret"},
        {"jwt_refresh_secret": "short-secret"},
        {
            "jwt_access_secret": "identical-secret-strictly-32-bytes!",
            "jwt_refresh_secret": "identical-secret-strictly-32-bytes!",
        },
        {"jwt_access_ttl_seconds": 10},
        {"jwt_refresh_ttl_seconds": 100},
        {"app_env": "production", "reload": True},
    ],
)
def test_invalid_configuration_fails_before_connections(overrides):
    with pytest.raises(ConfigurationError):
        load_settings(overrides)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/0",
        "redis:///0",
        "redis://localhost/-1",
        "redis://localhost:70000/0",
        "redis://localhost/abc",
        "redis://localhost/0?socket_timeout=0",
    ],
)
def test_invalid_redis_urls_are_rejected(url):
    with pytest.raises(ConfigurationError):
        load_settings({"redis_url": url})


def test_redis_url_errors_do_not_echo_secret():
    bad_url = "redis://:do-not-echo-this@localhost/not-a-database"
    with pytest.raises(ValidationError) as raw:
        Settings(redis_url=bad_url)
    assert "do-not-echo-this" not in str(raw.value)
    with pytest.raises(ConfigurationError) as redacted:
        load_settings({"redis_url": bad_url})
    assert "redis_url" in str(redacted.value)
    assert "do-not-echo-this" not in str(redacted.value)


def test_bad_json_and_misspelled_dotenv_fail_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMAAI_CORS_ORIGINS", "[invalid-sensitive-content")
    with pytest.raises(ConfigurationError) as failure:
        load_settings()
    assert "invalid-sensitive-content" not in str(failure.value)
    monkeypatch.delenv("FORMAAI_CORS_ORIGINS")
    (tmp_path / ".env").write_text(
        "FORMAAI_MYSQL_PASWORD=do-not-echo-this\n", encoding="utf-8"
    )
    with pytest.raises(ConfigurationError) as failure:
        load_settings()
    assert "do-not-echo-this" not in str(failure.value)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*",
        "https://example.test/path",
        "https://user:secret@example.test",
    ],
)
def test_cors_requires_explicit_origins(origin):
    with pytest.raises(ConfigurationError):
        load_settings({"cors_origins": [origin]})


def test_app_uses_production_docs_cors_and_connection_settings():
    settings = load_settings(
        {
            "app_env": "production",
            "cors_origins": ["http://localhost:5173/"],
            "mysql_pool_size": 3,
            "mysql_connect_timeout": 7,
            "redis_max_connections": 4,
            "redis_socket_timeout": 6,
        }
    )
    engine, cache = MagicMock(), MagicMock()
    with (
        patch("formaai.app.build_engine", return_value=engine) as build,
        patch("formaai.app.Redis.from_url", return_value=cache) as redis,
    ):
        with TestClient(create_app(settings)) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/health/live").status_code == 200
            allowed = client.options(
                "/health/live",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert allowed.status_code == 200
            assert (
                allowed.headers["access-control-allow-origin"]
                == "http://localhost:5173"
            )
            denied = client.options(
                "/health/live",
                headers={
                    "Origin": "https://other.test",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert denied.status_code == 400
            assert "access-control-allow-origin" not in denied.headers
        assert build.call_args.kwargs["pool_size"] == 3
        assert build.call_args.kwargs["connect_timeout"] == 7
        assert redis.call_args.kwargs["max_connections"] == 4
        assert redis.call_args.kwargs["socket_timeout"] == 6
        engine.connect.assert_not_called()


def test_cli_overrides_are_visible_to_factory(monkeypatch):
    monkeypatch.setenv("FORMAAI_APP_ENV", "production")
    monkeypatch.setenv("FORMAAI_RELOAD", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        ["formaai", "--port", "8123", "--no-reload", "--log-level", "warning"],
    )
    with patch("formaai.__main__.uvicorn.run") as run:
        main()
    assert run.call_args.kwargs["port"] == 8123
    assert run.call_args.kwargs["reload"] is False
    assert run.call_args.kwargs["log_level"] == "warning"
    assert load_settings().reload is False
