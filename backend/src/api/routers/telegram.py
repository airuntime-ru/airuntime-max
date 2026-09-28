from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.db.models.project import Project
from src.db.models.secret import Secret
from src.db.models.user import User
from src.db.session import get_db
from src.services.secrets import TELEGRAM_BOT_TOKEN_KEY, decrypt_secret, encrypt_secret
from src.services.telegram_profile import (
    TelegramBotProfile,
    TelegramProfileError,
    fetch_bot_profile,
    fetch_bot_settings,
    update_bot_profile_photo,
    update_bot_settings,
)

router = APIRouter(prefix="/projects/{project_id}/telegram", tags=["telegram"])


class TelegramTokenRequest(BaseModel):
    bot_token: str = Field(min_length=20, max_length=256)
    mode: str = Field(default="polling", pattern="^(polling|webhook)$")


class TelegramProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=512)
    short_description: str | None = Field(default=None, max_length=120)


def _profile_response(profile: TelegramBotProfile) -> dict:
    return {
        "username": profile.username,
        "url": profile.public_url,
        "name": profile.name,
        "description": profile.description,
        "short_description": profile.short_description,
    }


def _telegram_project_and_token(
    project_id: str,
    current_user: User,
    db: Session,
) -> tuple[Project, str]:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.type not in ("telegram_bot", "mixed"):
        raise HTTPException(status_code=400, detail="Project is not a Telegram bot")
    secret = (
        db.query(Secret)
        .filter(Secret.project_id == project.id, Secret.key == TELEGRAM_BOT_TOKEN_KEY)
        .first()
    )
    if not secret or not secret.encrypted_value:
        raise HTTPException(
            status_code=400, detail="Токен Telegram-бота ещё не задан в настройках проекта"
        )
    return project, decrypt_secret(secret.encrypted_value)


@router.post("/token")
def save_bot_token(
    project_id: str,
    payload: TelegramTokenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.type not in ("telegram_bot", "mixed"):
        raise HTTPException(status_code=400, detail="Project is not a Telegram bot")
    try:
        profile = fetch_bot_profile(payload.bot_token)
    except TelegramProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = (
        db.query(Secret)
        .filter(Secret.project_id == project.id, Secret.key == TELEGRAM_BOT_TOKEN_KEY)
        .first()
    )
    if existing:
        existing.encrypted_value = encrypt_secret(payload.bot_token)
    else:
        db.add(
            Secret(
                project_id=project.id,
                key=TELEGRAM_BOT_TOKEN_KEY,
                encrypted_value=encrypt_secret(payload.bot_token),
            )
        )
    project.deployment_url = profile.public_url
    project.status = "telegram_ready"
    db.commit()
    return {"status": "saved", "mode": payload.mode, "url": profile.public_url}


@router.get("/profile")
def get_bot_profile(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _, token = _telegram_project_and_token(project_id, current_user, db)
    try:
        return _profile_response(fetch_bot_settings(token))
    except TelegramProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/profile")
def save_bot_profile(
    project_id: str,
    payload: TelegramProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project, token = _telegram_project_and_token(project_id, current_user, db)
    try:
        profile = update_bot_settings(
            token,
            name=payload.name,
            description=payload.description,
            short_description=payload.short_description,
        )
    except TelegramProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.deployment_url = profile.public_url
    db.add(project)
    db.commit()
    return _profile_response(profile)


@router.post("/profile/photo")
async def save_bot_profile_photo(
    project_id: str,
    photo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project, token = _telegram_project_and_token(project_id, current_user, db)
    if photo.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Avatar must be JPG, PNG or WEBP")
    content = await photo.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Avatar must be smaller than 5 MB")
    try:
        profile = update_bot_profile_photo(
            token,
            filename=photo.filename or "bot-avatar.jpg",
            content=content,
            content_type=photo.content_type or "application/octet-stream",
        )
    except TelegramProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.deployment_url = profile.public_url
    db.add(project)
    db.commit()
    return _profile_response(profile)


@router.post("/start")
def start_bot(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.type not in ("telegram_bot", "mixed"):
        raise HTTPException(status_code=400, detail="Project is not a Telegram bot")
    token = (
        db.query(Secret)
        .filter(Secret.project_id == project.id, Secret.key == TELEGRAM_BOT_TOKEN_KEY)
        .first()
    )
    if not token or not token.encrypted_value:
        raise HTTPException(
            status_code=400, detail="Токен Telegram-бота ещё не задан в настройках проекта"
        )
    try:
        profile = fetch_bot_profile(decrypt_secret(token.encrypted_value))
    except TelegramProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.status = "telegram_ready"
    project.deployment_url = profile.public_url
    db.commit()
    return {
        "status": "ready",
        "project_id": project_id,
        "runtime": "telegram-worker",
        "url": profile.public_url,
    }
