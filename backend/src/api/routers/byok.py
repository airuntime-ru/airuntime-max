from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.db.models.user import User
from src.db.session import get_db
from src.services.byok import (
    SUPPORTED_PROVIDERS,
    ByokError,
    credential_response,
    delete_credential,
    list_credentials,
    revalidate_credential,
    upsert_credential,
)

router = APIRouter(prefix="/byok", tags=["byok"])


class CredentialPayload(BaseModel):
    provider: str
    api_key: str = Field(min_length=8, max_length=500)


@router.get("")
def list_my_credentials(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    return {
        "supported": list(SUPPORTED_PROVIDERS),
        "items": [credential_response(row) for row in list_credentials(db, current_user)],
    }


@router.put("")
def save_credential(
    payload: CredentialPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Store a provider key. The response never echoes it back - only the last four digits."""
    try:
        row = upsert_credential(db, current_user, payload.provider, payload.api_key)
    except ByokError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return credential_response(row)


@router.post("/{provider}/test")
def test_credential(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = revalidate_credential(db, current_user, provider)
    except ByokError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return credential_response(row)


@router.delete("/{provider}")
def remove_credential(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        removed = delete_credential(db, current_user, provider)
    except ByokError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    return {"deleted": True}
