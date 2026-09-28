import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.auth import (
    AuthMessageResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RequestCodeRequest,
    RequestCodeResponse,
    ResetPasswordRequest,
    TokenPairResponse,
    VerifyCodeRequest,
    VerifyEmailRequest,
)
from src.core.config import settings
from src.core.rate_limit import hit_email_send_rate_limit
from src.core.security import (
    create_access_token,
    create_purpose_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from src.db.models.refresh_token import RefreshToken
from src.db.models.user import User
from src.db.session import get_db
from src.services.billing import assign_default_plan
from src.services.email import send_branded_email
from src.services.email_templates import (
    login_code_email,
    password_reset_email,
)
from src.services.email_templates import (
    verify_email as verify_email_template,
)
from src.services.otp import otp_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _send_email_background(*, to: str, subject: str, plain: str, html: str) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                send_branded_email,
                to=to,
                subject=subject,
                plain=plain,
                html=html,
            ),
            timeout=20,
        )
    except Exception:
        logger.exception("Background email delivery failed for to=%s subject=%s", to, subject[:80])


def _store_refresh_token(db: Session, user_id: str, refresh_token: str) -> None:
    refresh_row = RefreshToken(
        user_id=user_id,
        token_hash=hash_password(refresh_token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(refresh_row)
    db.commit()


def _issue_verify_email(db: Session, user: User) -> None:
    token = create_purpose_token(str(user.id), "email_verify", timedelta(hours=24))
    verify_url = f"{settings.resolved_frontend_url}/auth/verify?token={token}"
    content = verify_email_template(verify_url=verify_url)
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )


def _issue_tokens(db: Session, user: User) -> TokenPairResponse:
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    _store_refresh_token(db, user.id, refresh)
    return TokenPairResponse(access_token=access, refresh_token=refresh)


@router.post("/request-code", response_model=RequestCodeResponse)
async def request_code(payload: RequestCodeRequest) -> RequestCodeResponse:
    hit_email_send_rate_limit(payload.email)
    code = otp_service.issue_code()
    ttl_seconds = settings.otp_expire_minutes * 60
    otp_service.store(payload.email, code, ttl_seconds)
    content = login_code_email(code=code, minutes=settings.otp_expire_minutes)
    asyncio.create_task(
        _send_email_background(
            to=payload.email,
            subject=content.subject,
            plain=content.plain,
            html=content.html,
        )
    )
    dev_code = code if settings.debug else None
    return RequestCodeResponse(
        message="Если почта зарегистрирована, код отправлен",
        dev_code=dev_code,
    )


@router.post("/verify-code", response_model=TokenPairResponse)
def verify_code(payload: VerifyCodeRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    if not otp_service.verify(payload.email, payload.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный или просроченный код"
        )

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        user = User(
            email=payload.email,
            password_hash=None,
            is_verified=True,
        )
        assign_default_plan(db, user)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.is_verified = True
        db.commit()

    return _issue_tokens(db, user)


@router.post("/register", response_model=TokenPairResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")

    password_hash = hash_password(payload.password) if payload.password else None
    user = User(
        email=payload.email,
        password_hash=password_hash,
    )
    assign_default_plan(db, user)
    db.add(user)
    db.commit()
    db.refresh(user)
    _issue_verify_email(db, user)
    return _issue_tokens(db, user)


@router.post("/login", response_model=TokenPairResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not payload.password or not user.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenPairResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPairResponse:
    try:
        token_data = jwt.decode(
            payload.refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = token_data.get("sub")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    active_tokens = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .all()
    )
    matched: RefreshToken | None = None
    for token in active_tokens:
        if verify_password(payload.refresh_token, token.token_hash):
            if token.expires_at < datetime.now(UTC):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired"
                )
            matched = token
            break
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked"
        )
    matched.revoked_at = datetime.now(UTC)
    db.commit()
    new_refresh = create_refresh_token(str(user.id))
    _store_refresh_token(db, user.id, new_refresh)
    return TokenPairResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=new_refresh,
    )


@router.post("/logout", response_model=AuthMessageResponse)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> AuthMessageResponse:
    try:
        token_data = jwt.decode(
            payload.refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    user_id = token_data.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    rows = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        if verify_password(payload.refresh_token, row.token_hash):
            row.revoked_at = datetime.now(UTC)
    db.commit()
    return AuthMessageResponse(message="Logged out")


@router.post("/forgot-password", response_model=AuthMessageResponse)
async def forgot_password(
    payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> AuthMessageResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user:
        hit_email_send_rate_limit(user.email)
        token = create_purpose_token(str(user.id), "password_reset", timedelta(minutes=15))
        reset_url = f"{settings.resolved_frontend_url}/auth/reset?token={token}"
        content = password_reset_email(reset_url=reset_url)
        asyncio.create_task(
            _send_email_background(
                to=user.email,
                subject=content.subject,
                plain=content.plain,
                html=content.html,
            )
        )
    return AuthMessageResponse(
        message="Если аккаунт существует, инструкции по сбросу пароля отправлены на почту"
    )


@router.post("/verify-email", response_model=AuthMessageResponse)
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)) -> AuthMessageResponse:
    try:
        token_data = jwt.decode(
            payload.token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if token_data.get("type") != "email_verify":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = token_data.get("sub")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_verified = True
    db.commit()
    return AuthMessageResponse(message="Email verified")


@router.post("/reset-password", response_model=AuthMessageResponse)
def reset_password(
    payload: ResetPasswordRequest, db: Session = Depends(get_db)
) -> AuthMessageResponse:
    try:
        token_data = jwt.decode(
            payload.token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if token_data.get("type") != "password_reset":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = token_data.get("sub")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return AuthMessageResponse(message="Password updated")


@router.get("/me", response_model=MeResponse)
def me(token: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        id=str(token.id),
        email=token.email,
        is_verified=token.is_verified,
        credits_balance=token.credits_balance,
        onboarding_completed=token.onboarding_completed,
    )


@router.post("/me/onboarding-complete", response_model=MeResponse)
def complete_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    if not user.onboarding_completed:
        user.onboarding_completed = True
        db.commit()
        db.refresh(user)
    return MeResponse(
        id=str(user.id),
        email=user.email,
        is_verified=user.is_verified,
        credits_balance=user.credits_balance,
        onboarding_completed=user.onboarding_completed,
    )
