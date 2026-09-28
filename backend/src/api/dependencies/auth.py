from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.user import User
from src.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if payload.get("type") != "access" or user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc
    try:
        user_uuid = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise credentials_exception from exc
    user = db.get(User, user_uuid)
    if user is None:
        raise credentials_exception
    return user
