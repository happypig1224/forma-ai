"""Run explicit migrations against configured MySQL, never on app startup."""

from alembic import context

from formaai.config import load_settings
from formaai.infrastructure.database import Base, build_engine
from formaai.modules.identity.models import UserAccount  # noqa: F401
from formaai.modules.library.models import Asset, Category, Material  # noqa: F401
from formaai.modules.production.models import (  # noqa: F401
    UsageRecord,
    VideoAttempt,
    VideoDecision,
    VideoShot,
    VideoTask,
)

target_metadata = Base.metadata


def render_item(item_type, item, autogen_context):
    """Keep generated migrations independent of application custom types."""
    from formaai.infrastructure.database import UTCDateTime

    if item_type == "type" and isinstance(item, UTCDateTime):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "mysql.DATETIME(fsp=6)"
    return False


if context.is_offline_mode():
    context.configure(
        url=context.config.attributes.get("database_url")
        or load_settings().database_url,
        target_metadata=target_metadata,
        compare_server_default=True,
        literal_binds=True,
        render_item=render_item,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_server_default=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        settings = load_settings()
        engine = build_engine(
            settings.database_url,
            pool_size=settings.mysql_pool_size,
            max_overflow=settings.mysql_max_overflow,
            pool_timeout=settings.mysql_pool_timeout,
            connect_timeout=settings.mysql_connect_timeout,
            read_timeout=settings.mysql_read_timeout,
            write_timeout=settings.mysql_write_timeout,
        )
        try:
            with engine.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_server_default=True,
                    render_item=render_item,
                )
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            engine.dispose()
