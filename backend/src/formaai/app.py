"""Minimal application factory and dependency health checks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from formaai.config import Settings, load_settings
from formaai.infrastructure.database import build_engine, build_sessionmaker
from formaai.infrastructure.storage import LocalStorageService
from formaai.modules.identity.router import auth_router
from formaai.modules.identity.sessions import SessionManager
from formaai.modules.library.router import (
    asset_router,
    category_router,
    material_router,
)
from formaai.modules.production.router import task_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the app without running migrations or opening connections."""
    settings = settings or load_settings()
    engine = build_engine(
        settings.database_url,
        pool_size=settings.mysql_pool_size,
        max_overflow=settings.mysql_max_overflow,
        pool_timeout=settings.mysql_pool_timeout,
        connect_timeout=settings.mysql_connect_timeout,
        read_timeout=settings.mysql_read_timeout,
        write_timeout=settings.mysql_write_timeout,
    )
    cache = Redis.from_url(
        settings.redis_url.get_secret_value(),
        max_connections=settings.redis_max_connections,
        socket_connect_timeout=settings.redis_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await run_in_threadpool(cache.close)
            await run_in_threadpool(engine.dispose)

    app = FastAPI(
        title="FormaAI",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.serve_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.serve_docs else None,
    )
    session_factory = build_sessionmaker(engine)
    session_manager = SessionManager(
        cache,
        app_env=settings.app_env,
        idle_ttl_seconds=settings.jwt_refresh_ttl_seconds,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.cache = cache
    app.state.session_manager = session_manager
    app.state.storage_service = LocalStorageService(settings.storage_dir)

    app.include_router(auth_router)
    app.include_router(category_router)
    app.include_router(material_router)
    app.include_router(asset_router)
    app.include_router(task_router)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Protection"],
        )

    @app.exception_handler(RedisError)
    def redis_error_handler(request: Request, exc: RedisError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "缓存与会话服务暂不可用，请稍后重试"},
        )

    @app.exception_handler(SQLAlchemyError)
    def db_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "数据库服务暂不可用，请稍后重试"},
        )

    @app.get("/health", include_in_schema=False)
    def health_root() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            cache.ping()
        except (SQLAlchemyError, RedisError):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app
