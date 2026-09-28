from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.db.models.project import Project
from src.db.models.secret import Secret
from src.db.models.user import User
from src.db.session import get_db
from src.services.project_runtime import RunningProjectLimitError, start_project_runtime
from src.services.secrets import (
    TELEGRAM_BOT_TOKEN_KEY,
    all_secrets_filled,
    encrypt_secret,
    ensure_secret_placeholder,
    looks_like_telegram_token,
)
from src.services.telegram_profile import TelegramProfileError, fetch_bot_profile

router = APIRouter(prefix="/projects/{project_id}/secrets", tags=["secrets"])


class SecretCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=280)


class SecretValueRequest(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


def _secret_response(row: Secret, *, url: str | None = None) -> dict:
    response = {
        "id": str(row.id),
        "key": row.key,
        "reason": row.reason,
        "has_value": row.encrypted_value is not None,
        "created_at": row.created_at,
    }
    if url:
        response["url"] = url
    return response


def _get_owned_project(project_id: str, current_user: User, db: Session) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _validate_secret_value(project: Project, key: str, value: str) -> str | None:
    """Raise HTTPException if the value is recognizably invalid for this key.

    Returns a Telegram public URL when the value could be verified as a live bot token, so the
    caller can refresh project.deployment_url immediately.
    """
    if key != TELEGRAM_BOT_TOKEN_KEY:
        return None
    if not looks_like_telegram_token(value):
        raise HTTPException(
            status_code=400,
            detail="Это не похоже на токен Telegram-бота. Формат: цифры, двоеточие, затем "
            "буквенно-цифровая строка - его выдаёт @BotFather после /newbot.",
        )
    try:
        profile = fetch_bot_profile(value)
    except TelegramProfileError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram отклонил этот токен: {exc}. Проверьте, что скопировали его полностью.",
        ) from exc
    if project.type == "telegram_bot":
        project.deployment_url = profile.public_url
    return profile.public_url


@router.post("")
def create_secret_placeholder(
    project_id: str,
    payload: SecretCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Reserve a secret slot (key + reason, no value yet). Value is filled in separately via
    PATCH, once the user has it - this endpoint never accepts a value itself."""
    project = _get_owned_project(project_id, current_user, db)
    secret, _created = ensure_secret_placeholder(db, project, payload.key, payload.reason)
    return _secret_response(secret)


@router.get("")
def list_secrets(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    project = _get_owned_project(project_id, current_user, db)
    rows = db.query(Secret).filter(Secret.project_id == project.id).all()
    return [_secret_response(row) for row in rows]


@router.patch("/{secret_id}")
def set_secret_value(
    project_id: str,
    secret_id: UUID,
    payload: SecretValueRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = _get_owned_project(project_id, current_user, db)
    secret = db.get(Secret, secret_id)
    if not secret or str(secret.project_id) != project_id:
        raise HTTPException(status_code=404, detail="Secret not found")
    url = _validate_secret_value(project, secret.key, payload.value)
    secret.encrypted_value = encrypt_secret(payload.value)
    db.add(secret)
    db.add(project)
    db.commit()

    # If this was the last missing secret and the project was only stuck waiting for
    # configuration, resume the deployment automatically instead of leaving the user to guess
    # they need to go back to chat and say something to unstick it.
    if project.status == "needs_configuration" and all_secrets_filled(db, project):
        try:
            start_project_runtime(db, project)
        except RunningProjectLimitError:
            project.status = "ready"
            db.add(project)
            db.commit()
        except ValueError:
            pass

    return _secret_response(secret, url=url)


@router.delete("/{secret_id}")
def delete_secret(
    project_id: str,
    secret_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_project(project_id, current_user, db)
    secret = db.get(Secret, secret_id)
    if not secret or str(secret.project_id) != project_id:
        raise HTTPException(status_code=404, detail="Secret not found")
    db.delete(secret)
    db.commit()
    return {"status": "deleted"}
