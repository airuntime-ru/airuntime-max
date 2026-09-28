"""Plan change requests.

A paid plan hands out a real token budget, so users cannot switch to one themselves: they file
a request, an admin approves it after payment, and only then is the budget issued.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.plan import Plan
from src.db.models.plan_change_request import PlanChangeRequest
from src.db.models.user import User
from src.services.billing import plan_grant_credits, switch_plan
from src.services.email import send_branded_email
from src.services.email_templates import (
    plan_request_approved_email,
    plan_request_created_email,
    plan_request_rejected_email,
)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CANCELLED = "cancelled"

OPEN_STATUSES = (PENDING,)


def _billing_url() -> str:
    return f"{settings.resolved_frontend_url.rstrip('/')}/app/profile"


def get_pending_request(db: Session, user: User) -> PlanChangeRequest | None:
    return (
        db.query(PlanChangeRequest)
        .filter(
            PlanChangeRequest.user_id == user.id,
            PlanChangeRequest.status.in_(OPEN_STATUSES),
        )
        .order_by(PlanChangeRequest.created_at.desc())
        .first()
    )


def list_requests(db: Session, user: User, *, limit: int = 20) -> list[PlanChangeRequest]:
    return (
        db.query(PlanChangeRequest)
        .filter(PlanChangeRequest.user_id == user.id)
        .order_by(PlanChangeRequest.created_at.desc())
        .limit(limit)
        .all()
    )


class PlanRequestError(Exception):
    """Domain-level rejection with a user-facing message."""


def create_request(
    db: Session, user: User, plan: Plan, *, note: str | None = None
) -> PlanChangeRequest:
    if user.plan_id and user.plan_id == plan.id:
        raise PlanRequestError("Этот тариф уже активен")
    if get_pending_request(db, user) is not None:
        raise PlanRequestError("Заявка уже на рассмотрении — дождитесь ответа или отмените её")

    request = PlanChangeRequest(
        user_id=user.id,
        from_plan_id=user.plan_id,
        to_plan_id=plan.id,
        status=PENDING,
        note=(note or "").strip() or None,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    content = plan_request_created_email(
        plan_name=plan.name,
        price_rub=plan.price_rub,
        budget_rub=plan.monthly_budget_rub,
        billing_url=_billing_url(),
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )
    return request


def cancel_request(db: Session, user: User, request_id: uuid.UUID) -> PlanChangeRequest:
    request = (
        db.query(PlanChangeRequest)
        .filter(PlanChangeRequest.id == request_id, PlanChangeRequest.user_id == user.id)
        .first()
    )
    if request is None:
        raise PlanRequestError("Заявка не найдена")
    if request.status != PENDING:
        raise PlanRequestError("Отменить можно только заявку на рассмотрении")
    request.status = CANCELLED
    request.resolved_at = datetime.now(UTC)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def _apply_approval(db: Session, request: PlanChangeRequest) -> PlanChangeRequest:
    """Issue the plan grant for an already-approved request. Idempotent via `applied_at`."""
    if request.applied_at is not None:
        return request
    user = db.get(User, request.user_id)
    plan = db.get(Plan, request.to_plan_id)
    if user is None or plan is None:
        raise PlanRequestError("Пользователь или тариф не найден")

    switch_plan(db, user, plan)

    request.applied_at = datetime.now(UTC)
    db.add(request)
    db.commit()
    db.refresh(request)

    content = plan_request_approved_email(
        plan_name=plan.name,
        budget_rub=plan.monthly_budget_rub,
        credits=plan_grant_credits(plan),
        period_end=user.billing_period_end,
        billing_url=_billing_url(),
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )
    return request


def apply_approved_requests(db: Session) -> int:
    """Grant plans for approvals the Django admin marked but could not execute itself.

    The admin app talks to the same database but cannot import the billing service, so it only
    flips `status`; this sweep does the money part. Returns how many were applied.
    """
    pending = (
        db.query(PlanChangeRequest)
        .filter(
            PlanChangeRequest.status == APPROVED,
            PlanChangeRequest.applied_at.is_(None),
        )
        .all()
    )
    applied = 0
    for request in pending:
        try:
            _apply_approval(db, request)
            applied += 1
        except PlanRequestError:
            # Orphaned user/plan - leave the row for an operator instead of blocking the sweep.
            db.rollback()
    return applied


def approve_request(
    db: Session,
    request: PlanChangeRequest,
    *,
    resolved_by: str | None = None,
    admin_note: str | None = None,
) -> PlanChangeRequest:
    """Approve and immediately grant. Idempotent: a non-pending request is left untouched."""
    if request.status != PENDING:
        return request

    request.status = APPROVED
    request.resolved_at = datetime.now(UTC)
    request.resolved_by = resolved_by
    request.admin_note = admin_note
    db.add(request)
    db.commit()
    db.refresh(request)

    return _apply_approval(db, request)


def reject_request(
    db: Session,
    request: PlanChangeRequest,
    *,
    resolved_by: str | None = None,
    admin_note: str | None = None,
) -> PlanChangeRequest:
    if request.status != PENDING:
        return request
    user = db.get(User, request.user_id)
    plan = db.get(Plan, request.to_plan_id)

    request.status = REJECTED
    request.resolved_at = datetime.now(UTC)
    request.resolved_by = resolved_by
    request.admin_note = admin_note
    db.add(request)
    db.commit()
    db.refresh(request)

    if user is not None and plan is not None:
        content = plan_request_rejected_email(
            plan_name=plan.name,
            admin_note=admin_note,
            billing_url=_billing_url(),
        )
        send_branded_email(
            to=user.email, subject=content.subject, plain=content.plain, html=content.html
        )
    return request
