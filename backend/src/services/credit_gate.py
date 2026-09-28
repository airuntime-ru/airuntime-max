"""What to tell a user who has run out of credits (Epic C2).

A bare 402 leaves them stuck: the balance is empty and nothing on screen says what to do next.
This builds a structured payload naming the three real ways forward, so the UI can render
buttons instead of an error string.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.db.models.user import User
from src.services.byok import SUPPORTED_PROVIDERS, has_valid_key
from src.services.plan_requests import get_pending_request

CODE_OUT_OF_CREDITS = "out_of_credits"


def out_of_credits_detail(db: Session, user: User) -> dict:
    """Structured 402 body: a message plus the actions actually available to this account."""
    has_byok = any(has_valid_key(db, user, provider) for provider in SUPPORTED_PROVIDERS)
    pending_request = get_pending_request(db, user)

    actions = [
        {
            "id": "topup",
            "label": "Пополнить баланс",
            "href": "/app/profile",
        }
    ]
    if not has_byok:
        actions.append(
            {
                "id": "byok",
                "label": "Подключить свой API-ключ",
                "href": "/app/profile#byok",
            }
        )
    if pending_request is None:
        actions.append(
            {
                "id": "plan",
                "label": "Подать заявку на тариф",
                "href": "/app/profile",
            }
        )

    return {
        "code": CODE_OUT_OF_CREDITS,
        "message": "Закончились кредиты — работа агента приостановлена.",
        "balance": user.credits_balance,
        "has_byok": has_byok,
        "pending_plan_request": pending_request is not None,
        "actions": actions,
    }
