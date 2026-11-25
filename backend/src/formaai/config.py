"""Project settings with environment overrides and redacted startup errors."""

import os
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError
from sqlalchemy import URL


class Settings(BaseSettings):
    """Load deployment configuration from the environment or local .env."""

    model_config = SettingsConfigDict(
        env_prefix="FORMAAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "testing", "production"] = "development"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    log_level: Literal["critical", "error", "warning", "info", "debug", "trace"] = (
        "info"
    )
    docs_enabled: bool | None = None
    cors_origins: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = False
    storage_dir: Path = Field(default=Path("data/storage"))

    mysql_host: str = Field(default="127.0.0.1", min_length=1)
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = Field(min_length=1)
    mysql_user: str = Field(min_length=1)
    mysql_password: SecretStr = Field(min_length=1)
    mysql_pool_size: int = Field(default=5, ge=1, le=100)
    mysql_max_overflow: int = Field(default=0, ge=0, le=100)
    mysql_pool_timeout: int = Field(default=5, ge=1, le=300)
    mysql_connect_timeout: int = Field(default=3, ge=1, le=60)
    mysql_read_timeout: int = Field(default=5, ge=1, le=300)
    mysql_write_timeout: int = Field(default=5, ge=1, le=300)
    redis_url: SecretStr
    redis_max_connections: int = Field(default=10, ge=1, le=100)
    redis_connect_timeout: float = Field(default=3, gt=0, le=60)
    redis_socket_timeout: float = Field(default=3, gt=0, le=60)
    jwt_access_secret: SecretStr = Field(min_length=32)
    jwt_refresh_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = Field(default="formaai", min_length=1)
    jwt_audience: str = Field(default="formaai-client", min_length=1)
    jwt_access_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    jwt_refresh_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)

    # 302.AI Gateway & Tri-Modal High Availability Settings
    ai302_api_key: SecretStr = Field(default=SecretStr(""))
    ai302_base_url: str = Field(default="https://api.302.ai/v1")

    # 1. 大语言模型 (LLM) 主备高可用
    ai302_llm_primary_model: str = Field(default="deepseek-chat")
    ai302_llm_fallback_model: str = Field(default="gpt-4o")

    # 2. 图片生成模型 (T2I/I2I) 主备高可用
    ai302_image_primary_model: str = Field(default="doubao-seedream-5-0-260128")
    ai302_image_fallback_model: str = Field(default="gpt-image-1.5-t2i")

    # 3. 视频生成模型 (T2V/I2V) 主备高可用
    ai302_video_primary_model: str = Field(default="minimax-hailuo-02")
    ai302_video_fallback_model: str = Field(default="official-kling-v3")

    # 通用重试与超时控制
    ai302_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    ai302_max_retries: int = Field(default=3, ge=1, le=10)

    # 兼容历史别名
    ai302_primary_model: str = Field(default="deepseek-chat")
    ai302_fallback_model: str = Field(default="gpt-4o")

    @field_validator(
        "host",
        "mysql_host",
        "mysql_database",
        "mysql_user",
        "jwt_issuer",
        "jwt_audience",
    )
    @classmethod
    def strip_required_names(cls, value: str) -> str:
        """Reject whitespace-only identifiers without touching passwords."""
        value = value.strip()
        if not value:
            raise ValueError("A nonblank value is required")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        """Accept TCP Redis URLs; connection overrides belong in typed settings."""
        try:
            parsed = urlsplit(value.get_secret_value())
            valid = (
                parsed.scheme in {"redis", "rediss"}
                and bool(parsed.hostname)
                and not any(char.isspace() for char in value.get_secret_value())
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and (
                    parsed.path in {"", "/"}
                    or parsed.path[1:].isascii()
                    and parsed.path[1:].isdigit()
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Expected redis(s)://host[:port][/database] without query")
        return value

    @field_validator("jwt_access_secret", "jwt_refresh_secret")
    @classmethod
    def validate_jwt_secret_bytes(cls, value: SecretStr) -> SecretStr:
        """Enforce at least 32 UTF-8 bytes (256 bits) for HS256 security."""
        raw = value.get_secret_value()
        if len(raw.encode("utf-8")) < 32:
            raise ValueError("JWT secret must be at least 32 bytes (256 bits)")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        """Require explicit HTTP origins; no wildcard or URL credentials."""
        origins = []
        for value in values:
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and "*" not in parsed.netloc
                    and not any(char.isspace() for char in value)
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.path in {"", "/"}
                    and not parsed.query
                    and not parsed.fragment
                    and (parsed.port is None or 1 <= parsed.port <= 65535)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("CORS requires explicit HTTP(S) origins without paths")
            origins.append(value.rstrip("/"))
        return list(dict.fromkeys(origins))

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        """Prevent development reload from being enabled in production."""
        if self.app_env == "production" and self.reload:
            raise ValueError("Production must not enable reload")
        if (
            self.jwt_access_secret.get_secret_value()
            == self.jwt_refresh_secret.get_secret_value()
        ):
            raise ValueError("JWT access and refresh secrets must be distinct")
        if not self.ai302_api_key.get_secret_value():
            env_key = os.environ.get("AI302_KEY", "").strip()
            if env_key:
                self.ai302_api_key = SecretStr(env_key)
        return self

    @property
    def serve_docs(self) -> bool:
        """Disable docs by default in production, allow explicit configuration."""
        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.app_env != "production"

    @property
    def database_url(self) -> URL:
        """Build a URL without manual credential interpolation."""
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )


class ConfigurationError(RuntimeError):
    """Invalid settings without exposing secret input or exception context."""


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    """Apply explicit overrides > environment > current-directory .env > defaults."""
    try:
        return Settings(**(overrides or {}))
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"]) or "settings"
                for error in exc.errors(include_input=False, include_context=False)
            }
        )
        raise ConfigurationError(
            "Invalid configuration fields: " + ", ".join(fields)
        ) from None
    except SettingsError:
        raise ConfigurationError(
            "Cannot parse configuration; check environment and .env value formats"
        ) from None
