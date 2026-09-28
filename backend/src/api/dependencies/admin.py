from fastapi import Depends, HTTPException, status

from src.api.dependencies.auth import get_current_user
from src.db.models.user import User


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return current_user
