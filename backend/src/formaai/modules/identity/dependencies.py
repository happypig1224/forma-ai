"""FastAPI dependencies for JWT authentication, Redis liveness, and role guards."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from formaai.config import Settings
from formaai.infrastructure.storage import StorageServiceProtocol
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.sessions import SessionManager
from formaai.modules.identity.tokens import (
    TokenClaims,
    TokenError,
    verify_access_token,
)

bearer_scheme = HTTPBearer(auto_error=True)


def get_settings(request: Request) -> Settings:
    """Extract application settings from app state."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_session_manager(request: Request) -> SessionManager:
    """Extract SessionManager from app state."""
    return request.app.state.session_manager  # type: ignore[no-any-return]


def get_storage_service(request: Request) -> StorageServiceProtocol:
    """Extract StorageServiceProtocol implementation from app state."""
    return request.app.state.storage_service  # type: ignore[no-any-return]


def get_db(request: Request) -> Generator[Session]:
    """Yield a database session from application connection pool."""
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> UserAccount:
    """Verify Access JWT, Redis session liveness, and MySQL account state."""
    raw_token = credentials.credentials
    try:
        claims: TokenClaims = verify_access_token(raw_token, settings)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = int(claims.sub)

    # 1. Redis Session liveness & version match (Fail Closed)
    is_alive = sessions.verify_session(
        session_id=claims.sid,
        user_id=user_id,
        version=claims.ver,
    )
    if not is_alive:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. MySQL Latest account state and version check
    user = db.scalar(select(UserAccount).where(UserAccount.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Version mismatch (e.g. revoked all sessions via password change)
    if user.version != claims.ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credentials have been revoked; please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Status check: disabled or deleted accounts cannot authenticate
    if user.status in ("disabled", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Account is {user.status}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    object.__setattr__(user, "_current_session_id", claims.sid)
    return user


def require_admin(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> UserAccount:
    """Guard endpoint for administrator role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


def require_can_create_tasks(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> UserAccount:
    """Guard endpoint: disabling users may not create new tasks."""
    if current_user.status == "disabling":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabling; creating new tasks is prohibited",
        )
    return current_user
