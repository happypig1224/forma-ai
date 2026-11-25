"""Authentication endpoints for session refresh, logout, register, login, and me."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from formaai.config import Settings
from formaai.modules.identity.dependencies import (
    get_current_user,
    get_db,
    get_session_manager,
    get_settings,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.identity.passwords import hash_password, verify_password
from formaai.modules.identity.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from formaai.modules.identity.sessions import RotationResult, SessionManager
from formaai.modules.identity.tokens import (
    TokenError,
    compute_refresh_digest,
    create_token_pair,
    verify_refresh_token,
)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger("formaai.auth")


def _get_cookie_name(settings: Settings) -> str:
    """Return __Host- prefix in production, formaai_refresh in dev/test."""
    return (
        "__Host-formaai_refresh"
        if settings.app_env == "production"
        else "formaai_refresh"
    )


def _validate_csrf_and_origin(request: Request, settings: Settings) -> None:
    """Enforce non-simple requests with X-CSRF-Protection: 1 and strict Origin check."""
    csrf_header = request.headers.get("x-csrf-protection")
    if csrf_header != "1":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid X-CSRF-Protection header",
        )

    origin = request.headers.get("origin")
    if settings.cors_origins:
        if not origin or origin not in settings.cors_origins:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Untrusted or missing request origin",
            )


def _set_refresh_cookie(
    response: Response, refresh_token: str, settings: Settings
) -> None:
    """Set hardened HttpOnly Cookie for Refresh Token."""
    cookie_name = _get_cookie_name(settings)
    is_prod = settings.app_env == "production"
    response.set_cookie(
        key=cookie_name,
        value=refresh_token,
        max_age=settings.jwt_refresh_ttl_seconds,
        httponly=True,
        secure=is_prod,
        samesite="strict",
        path="/",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Clear Refresh Token Cookie with matching security attributes."""
    cookie_name = _get_cookie_name(settings)
    is_prod = settings.app_env == "production"
    response.delete_cookie(
        key=cookie_name,
        httponly=True,
        secure=is_prod,
        samesite="strict",
        path="/",
    )


def _issue_session_tokens(
    response: Response,
    user: UserAccount,
    settings: Settings,
    sessions: SessionManager,
) -> TokenResponse:
    """Issue dual JWTs, register session in Redis, and set refresh cookie."""
    pair = create_token_pair(
        user_id=user.id,
        version=user.version,
        settings=settings,
    )
    sessions.create_session(
        session_id=pair.session_id,
        user_id=user.id,
        version=user.version,
        refresh_digest=pair.refresh_digest,
    )
    _set_refresh_cookie(response, pair.refresh_token, settings)
    return TokenResponse(
        access_token=pair.access_token,
        token_type="bearer",
        expires_in=pair.access_expires_in,
    )


@auth_router.post("/refresh", response_model=TokenResponse)
def refresh_token_endpoint(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Rotate Refresh Token under RFC 9700 replay detection semantics."""
    _validate_csrf_and_origin(request, settings)

    cookie_name = _get_cookie_name(settings)
    raw_refresh = request.cookies.get(cookie_name)
    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token cookie",
        )

    try:
        claims = verify_refresh_token(raw_refresh, settings)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        user_id = int(claims.sub)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user id in token",
        ) from err

    user = db.scalar(select(UserAccount).where(UserAccount.id == user_id))
    if user is None:
        sessions.revoke_session(claims.sid, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found",
        )

    if user.version != claims.ver or user.status != "active":
        sessions.revoke_session(claims.sid, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account status or version invalid",
        )

    pair = create_token_pair(
        user_id=user.id,
        version=user.version,
        settings=settings,
        session_id=claims.sid,
    )

    old_digest = compute_refresh_digest(raw_refresh)
    rotation = sessions.rotate_session(
        session_id=claims.sid,
        user_id=user.id,
        version=user.version,
        old_digest=old_digest,
        new_digest=pair.refresh_digest,
    )

    if rotation == RotationResult.REPLAY_DETECTED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token replayed; session family revoked",
        )

    if rotation != RotationResult.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    _set_refresh_cookie(response, pair.refresh_token, settings)
    return TokenResponse(
        access_token=pair.access_token,
        token_type="bearer",
        expires_in=pair.access_expires_in,
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
) -> Response:
    """Terminate the current single device session and clear refresh cookie."""
    _validate_csrf_and_origin(request, settings)
    cookie_name = _get_cookie_name(settings)
    raw_refresh = request.cookies.get(cookie_name)
    if raw_refresh:
        try:
            claims = verify_refresh_token(raw_refresh, settings)
            user_id = int(claims.sub)
            sessions.revoke_session(claims.sid, user_id=user_id)
        except TokenError, ValueError:
            pass

    _clear_refresh_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    request: Request,
    response: Response,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Invalidate all active sessions across all devices for this account."""
    _validate_csrf_and_origin(request, settings)

    db.execute(
        update(UserAccount)
        .where(UserAccount.id == current_user.id)
        .values(version=UserAccount.version + 1)
    )
    db.commit()

    sid = getattr(current_user, "_current_session_id", None)
    if sid:
        sessions.revoke_session(sid, user_id=current_user.id)

    _clear_refresh_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    response: Response,
    body: RegisterRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Register a new user account with email and password."""
    email = str(body.email).lower().strip()
    existing = db.scalar(select(UserAccount).where(UserAccount.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    now = datetime.now(UTC)
    pwd_hash = hash_password(body.password)
    user = UserAccount(
        email=email,
        password_hash=pwd_hash,
        role="user",
        status="active",
        version=0,
        created_at=now,
        updated_at=now,
        email_verified_at=now,
    )
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc

    return _issue_session_tokens(
        response=response,
        user=user,
        settings=settings,
        sessions=sessions,
    )


@auth_router.post("/login", response_model=TokenResponse)
def login(
    response: Response,
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionManager, Depends(get_session_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate user with email and password, issuing dual tokens."""
    email = str(body.email).lower().strip()
    user = db.scalar(select(UserAccount).where(UserAccount.email == email))
    if user is None:
        logger.warning("LOGIN_FAIL: user not found: %s", email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(body.password, user.password_hash):
        logger.warning(
            "LOGIN_FAIL: password mismatch for: %s (input len: %d)",
            email,
            len(body.password),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.status in ("disabled", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Account is {user.status}",
        )

    return _issue_session_tokens(
        response=response,
        user=user,
        settings=settings,
        sessions=sessions,
    )


@auth_router.get("/me", response_model=UserResponse)
def get_me(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)
