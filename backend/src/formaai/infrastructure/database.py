"""SQLAlchemy engine and UTC persistence primitives for MySQL."""

from datetime import UTC, datetime

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Declarative mapping registry; migrations own schema creation."""


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware timestamps as UTC DATETIME(6), return aware UTC values."""

    impl = DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def build_engine(
    url: URL,
    *,
    pool_size: int = 5,
    max_overflow: int = 0,
    pool_timeout: int = 5,
    connect_timeout: int = 3,
    read_timeout: int = 5,
    write_timeout: int = 5,
) -> Engine:
    """Build a lazy engine; configure every new connection to UTC."""
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        connect_args={
            "init_command": "SET time_zone = '+00:00'",
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
            "write_timeout": write_timeout,
        },
    )


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Create a thread-safe Session factory bound to the engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
