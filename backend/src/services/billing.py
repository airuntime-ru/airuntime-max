from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.credit_ledger import CreditLedgerEntry
from src.db.models.credit_topup import CreditTopUp
from src.db.models.plan import Plan
from src.db.models.project import Project
from src.db.models.user import User
from src.services.email import send_branded_email
from src.services.email_templates import (
    credits_exhausted_email,
    credits_topup_paid_email,
    invoice_created_email,
    low_credits_email,
    period_ending_email,
    period_renewed_email,
)

BILLING_PERIOD_DAYS = 30
LOW_CREDITS_THRESHOLD_RATIO = 0.1
PERIOD_ENDING_WARNING_DAYS = 3
LedgerDirection = str  # "all" | "credit" | "debit"


def _billing_url() -> str:
    # /app/settings is a redirect stub - billing actually lives on the profile page.
    return f"{settings.resolved_frontend_url.rstrip('/')}/app/profile"


def plan_grant_credits(plan: Plan) -> int:
    """Credits issued for one billing period of `plan`.

    Plans store a budget in rubles; credits are the internal unit derived from it.
    """
    return max(0, plan.monthly_budget_rub) * settings.billing_credits_per_rub


def get_default_plan(db: Session) -> Plan | None:
    return db.query(Plan).filter(Plan.is_default.is_(True), Plan.is_active.is_(True)).first()


def assign_default_plan(db: Session, user: User) -> None:
    plan = get_default_plan(db)
    if not plan:
        return
    now = datetime.now(UTC)
    grant = plan_grant_credits(plan)
    user.plan_id = plan.id
    user.credits_balance = grant
    user.billing_period_start = now
    user.billing_period_end = now + timedelta(days=BILLING_PERIOD_DAYS)
    if grant > 0:
        # Called while the User row is still new, so its id only exists after a flush - the
        # ledger FK would otherwise go in as NULL.
        db.add(user)
        db.flush()
        record_ledger_entry(db, user, amount=grant, reason="signup_grant")


def concurrent_project_limit(db: Session, user: User, *, fallback: int) -> int:
    if not user.plan_id:
        return fallback
    plan = db.get(Plan, user.plan_id)
    return plan.max_concurrent_projects if plan else fallback


def total_project_limit(db: Session, user: User) -> int | None:
    """Total projects a user may own, from their plan.

    None means the plan imposes no limit - either the account has no plan at all (a
    misconfiguration; blocking them at one project would be worse than the old behaviour) or
    the plan opts out with a non-positive value. Callers then fall back to the global ceiling.
    """
    if not user.plan_id:
        return None
    plan = db.get(Plan, user.plan_id)
    if plan is None or plan.max_projects <= 0:
        return None
    return plan.max_projects


def allowed_platform_models(db: Session, user: User) -> list[str] | None:
    """Models this user may run on the *platform* key, or None for the whole catalog.

    BYOK bypasses this entirely - own key, own money, own choice of model.
    """
    if not user.plan_id:
        return None
    plan = db.get(Plan, user.plan_id)
    if not plan or not plan.allowed_models:
        return None
    return [str(item) for item in plan.allowed_models]


def record_ledger_entry(
    db: Session,
    user: User,
    *,
    amount: int,
    reason: str,
    project_id: uuid.UUID | None = None,
    project_name: str | None = None,
) -> None:
    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            project_id=project_id,
            project_name=project_name,
            amount=amount,
            reason=reason,
        )
    )


def record_usage(
    db: Session,
    user: User,
    *,
    project_id: uuid.UUID,
    amount: int,
    project_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_write_input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_cost_usd_micros: int | None = None,
    markup_percent: int | None = None,
) -> None:
    """Deduct credits for a chat turn and log it. `amount` is the positive cost - the balance
    change and ledger entry are both negative.

    `project_name` is snapshotted onto the ledger row so history stays readable after the
    project is deleted. If omitted, the current project name is loaded from the DB.
    """
    if project_name is None:
        project = db.get(Project, project_id)
        project_name = project.name if project else None
    user.credits_balance = max(0, user.credits_balance - amount)
    db.add(user)
    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            project_id=project_id,
            project_name=project_name,
            amount=-amount,
            reason="chat_message",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            output_tokens=output_tokens,
            provider_cost_usd_micros=provider_cost_usd_micros,
            markup_percent=markup_percent,
        )
    )


def record_byok_usage(
    db: Session,
    user: User,
    *,
    project_id: uuid.UUID,
    project_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_write_input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_cost_usd_micros: int | None = None,
) -> None:
    """Log a turn the user paid their own provider for.

    Balance is untouched (amount 0) but tokens and provider cost are still recorded, so
    analytics can separate BYOK traffic from traffic the platform actually pays for.
    """
    if project_name is None:
        project = db.get(Project, project_id)
        project_name = project.name if project else None
    db.add(
        CreditLedgerEntry(
            user_id=user.id,
            project_id=project_id,
            project_name=project_name,
            amount=0,
            reason="byok_usage",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            output_tokens=output_tokens,
            provider_cost_usd_micros=provider_cost_usd_micros,
            markup_percent=0,
        )
    )


def switch_plan(db: Session, user: User, plan: Plan) -> User:
    """Apply a plan change. Grants the plan's budget and starts a fresh billing period.

    Not reachable from the public API on purpose: paid plans hand out a real token budget, so
    the only callers are admin approval of a PlanChangeRequest and internal/seed tooling.
    """
    now = datetime.now(UTC)
    grant = plan_grant_credits(plan)
    user.plan_id = plan.id
    user.credits_balance = grant
    user.billing_period_start = now
    user.billing_period_end = now + timedelta(days=BILLING_PERIOD_DAYS)
    user.low_credits_notified_at = None
    user.period_ending_notified_at = None
    db.add(user)
    record_ledger_entry(db, user, amount=grant, reason="plan_change")
    db.commit()
    db.refresh(user)
    return user


def list_ledger(
    db: Session,
    user: User,
    *,
    limit: int = 20,
    offset: int = 0,
    direction: LedgerDirection = "all",
) -> tuple[list[CreditLedgerEntry], int]:
    """Return a page of ledger entries plus the filtered total count.

    `direction`:
      - ``all`` — no amount filter
      - ``credit`` — начисления (amount > 0)
      - ``debit`` — списания (amount < 0)
    """
    query = db.query(CreditLedgerEntry).filter(CreditLedgerEntry.user_id == user.id)
    if direction == "credit":
        query = query.filter(CreditLedgerEntry.amount > 0)
    elif direction == "debit":
        query = query.filter(CreditLedgerEntry.amount < 0)
    total = query.count()
    rows = (
        query.order_by(CreditLedgerEntry.created_at.desc(), CreditLedgerEntry.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def credits_to_rub(credits: int) -> int:
    return max(1, math.ceil(credits / settings.billing_credits_per_rub))


def usage_credits_to_rub(credits: int) -> Decimal:
    """Exact display value for usage; unlike invoice pricing it is never rounded up to 1 ₽."""
    return Decimal(abs(credits)) / Decimal(settings.billing_credits_per_rub)


def summarize_project_generation_usage(db: Session, project_id: uuid.UUID) -> dict:
    """Totals for the project overview: tokens, platform charge, and generation wall time.

    Money and tokens come from the credit ledger (platform key + BYOK rows). Duration is the
    sum of orchestration run wall-clock intervals (finished or still running).
    """
    from sqlalchemy import case, func

    from src.db.models.orchestration_run import OrchestrationRun

    ledger = (
        db.query(
            func.coalesce(
                func.sum(
                    case(
                        (CreditLedgerEntry.amount < 0, -CreditLedgerEntry.amount),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(CreditLedgerEntry.input_tokens), 0),
            func.coalesce(func.sum(CreditLedgerEntry.cached_input_tokens), 0),
            func.coalesce(func.sum(CreditLedgerEntry.cache_write_input_tokens), 0),
            func.coalesce(func.sum(CreditLedgerEntry.output_tokens), 0),
            func.count(CreditLedgerEntry.id),
        )
        .filter(
            CreditLedgerEntry.project_id == project_id,
            CreditLedgerEntry.reason.in_(("chat_message", "byok_usage")),
        )
        .one()
    )
    credits_spent = int(ledger[0] or 0)
    input_tokens = int(ledger[1] or 0)
    cached_input_tokens = int(ledger[2] or 0)
    cache_write_input_tokens = int(ledger[3] or 0)
    output_tokens = int(ledger[4] or 0)
    charge_events = int(ledger[5] or 0)

    now = datetime.now(UTC)
    duration_row = (
        db.query(
            func.coalesce(
                func.sum(
                    func.extract(
                        "epoch",
                        func.coalesce(OrchestrationRun.finished_at, now)
                        - OrchestrationRun.started_at,
                    )
                ),
                0,
            ),
            func.count(OrchestrationRun.id),
        )
        .filter(
            OrchestrationRun.project_id == project_id,
            OrchestrationRun.started_at.is_not(None),
        )
        .one()
    )
    generation_seconds = max(0.0, float(duration_row[0] or 0))
    runs_count = int(duration_row[1] or 0)

    return {
        "credits_spent": credits_spent,
        "cost_rub": float(usage_credits_to_rub(credits_spent)),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "generation_seconds": round(generation_seconds, 1),
        "runs_count": runs_count,
        "charge_events": charge_events,
    }


def request_topup(db: Session, user: User, credits: int) -> CreditTopUp:
    invoice = CreditTopUp(
        user_id=user.id,
        credits=credits,
        amount_rub=credits_to_rub(credits),
        status="pending",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    content = invoice_created_email(
        invoice_id=str(invoice.id),
        credits=invoice.credits,
        amount_rub=invoice.amount_rub,
        created_at=invoice.created_at,
        billing_url=_billing_url(),
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )
    return invoice


def credit_paid_topup(db: Session, invoice: CreditTopUp, *, now: datetime) -> None:
    """Grant credits for a paid invoice. Idempotent: no-op if already credited or unpaid."""
    if invoice.status != "paid" or invoice.credited_at is not None:
        return
    user = db.get(User, invoice.user_id)
    if not user:
        return
    user.credits_balance += invoice.credits
    invoice.credited_at = now
    db.add(user)
    db.add(invoice)
    record_ledger_entry(db, user, amount=invoice.credits, reason="topup")
    content = credits_topup_paid_email(
        credits=invoice.credits,
        amount_rub=invoice.amount_rub,
        new_balance=user.credits_balance,
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )


def mark_topup_paid(db: Session, invoice: CreditTopUp, *, now: datetime | None = None) -> None:
    """Mark an invoice paid and credit immediately. Safe to call repeatedly."""
    if invoice.status == "cancelled":
        raise ValueError("cancelled invoice cannot be marked paid")
    moment = now or datetime.now(UTC)
    if invoice.status != "paid":
        invoice.status = "paid"
        invoice.paid_at = moment
        db.add(invoice)
    credit_paid_topup(db, invoice, now=moment)


def _renew_period_if_due(db: Session, user: User, *, now: datetime) -> None:
    if not user.billing_period_end or user.billing_period_end > now:
        return
    plan = db.get(Plan, user.plan_id) if user.plan_id else None
    if not plan:
        return
    if not plan.grant_renews:
        # One-shot grant (free tier). Roll the period forward so period-based UI stays sane,
        # but never re-issue the budget - otherwise every signup is an unbounded monthly bill.
        user.billing_period_start = now
        user.billing_period_end = now + timedelta(days=BILLING_PERIOD_DAYS)
        db.add(user)
        return
    grant = plan_grant_credits(plan)
    user.credits_balance = grant
    user.billing_period_start = now
    user.billing_period_end = now + timedelta(days=BILLING_PERIOD_DAYS)
    user.low_credits_notified_at = None
    user.period_ending_notified_at = None
    db.add(user)
    record_ledger_entry(db, user, amount=grant, reason="period_renewal")
    content = period_renewed_email(
        plan_name=plan.name,
        credits=grant,
        period_end=user.billing_period_end,
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )


def _notify_low_credits_if_due(db: Session, user: User, *, now: datetime) -> None:
    if not user.plan_id:
        return
    plan = db.get(Plan, user.plan_id)
    grant = plan_grant_credits(plan) if plan else 0
    if not plan or grant <= 0:
        return
    threshold = grant * LOW_CREDITS_THRESHOLD_RATIO
    if user.credits_balance > threshold:
        return
    if user.low_credits_notified_at is not None:
        return
    user.low_credits_notified_at = now
    db.add(user)
    if user.credits_balance <= 0:
        content = credits_exhausted_email(billing_url=_billing_url())
    else:
        content = low_credits_email(
            credits_balance=user.credits_balance, billing_url=_billing_url()
        )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )


def _notify_period_ending_if_due(db: Session, user: User, *, now: datetime) -> None:
    if not user.billing_period_end:
        return
    days_left = (user.billing_period_end - now).total_seconds() / 86400
    if days_left > PERIOD_ENDING_WARNING_DAYS or days_left < 0:
        return
    if user.period_ending_notified_at is not None:
        return
    user.period_ending_notified_at = now
    db.add(user)
    plan = db.get(Plan, user.plan_id) if user.plan_id else None
    content = period_ending_email(
        period_end=user.billing_period_end,
        plan_name=plan.name if plan else None,
        credits_balance=user.credits_balance,
        billing_url=_billing_url(),
    )
    send_branded_email(
        to=user.email, subject=content.subject, plain=content.plain, html=content.html
    )


def _credit_paid_topups(db: Session, *, now: datetime) -> None:
    pending = (
        db.query(CreditTopUp)
        .filter(CreditTopUp.status == "paid", CreditTopUp.credited_at.is_(None))
        .all()
    )
    for invoice in pending:
        credit_paid_topup(db, invoice, now=now)


def run_billing_maintenance(db: Session) -> None:
    """Periodic sweep: renew expired billing periods, send low-credit/period-ending warnings,
    and credit any top-up invoices an admin has marked paid. Safe to call repeatedly."""
    now = datetime.now(UTC)
    # Deferred work the Django admin marked but could not execute itself.
    from src.services.plan_requests import apply_approved_requests

    apply_approved_requests(db)
    _credit_paid_topups(db, now=now)
    users = db.query(User).filter(User.plan_id.isnot(None)).all()
    for user in users:
        _renew_period_if_due(db, user, now=now)
        _notify_low_credits_if_due(db, user, now=now)
        _notify_period_ending_if_due(db, user, now=now)
    db.commit()
