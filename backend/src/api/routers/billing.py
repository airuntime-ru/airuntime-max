import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.core.config import settings
from src.db.models.credit_ledger import CreditLedgerEntry
from src.db.models.credit_topup import CreditTopUp
from src.db.models.plan import Plan
from src.db.models.plan_change_request import PlanChangeRequest
from src.db.models.user import User
from src.db.session import get_db
from src.services.billing import (
    list_ledger,
    mark_topup_paid,
    plan_grant_credits,
    request_topup,
    usage_credits_to_rub,
)
from src.services.plan_requests import (
    PlanRequestError,
    cancel_request,
    create_request,
    get_pending_request,
    list_requests,
)
from src.services.robokassa import (
    amounts_match,
    extract_shp,
    parse_inv_id,
    payment_url_for_invoice,
    result_signature,
    signatures_match,
    success_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

LedgerDirectionParam = Literal["all", "credit", "debit"]


class TopUpRequest(BaseModel):
    credits: int = Field(gt=0, le=10_000_000)


class PlanChangeRequestPayload(BaseModel):
    plan_id: str
    note: str | None = Field(default=None, max_length=1000)


def _plan_response(plan: Plan) -> dict:
    return {
        "id": str(plan.id),
        "key": plan.key,
        "name": plan.name,
        "description": plan.description,
        "monthly_budget_rub": plan.monthly_budget_rub,
        # Derived, not stored: the UI shows rubles but the chat still meters in credits.
        "monthly_credits": plan_grant_credits(plan),
        "max_concurrent_projects": plan.max_concurrent_projects,
        "max_projects": plan.max_projects,
        "price_rub": plan.price_rub,
        "grant_renews": plan.grant_renews,
        "allowed_models": list(plan.allowed_models) if plan.allowed_models else None,
    }


def _plan_request_response(row: PlanChangeRequest, plans: dict[str, Plan]) -> dict:
    to_plan = plans.get(str(row.to_plan_id))
    from_plan = plans.get(str(row.from_plan_id)) if row.from_plan_id else None
    return {
        "id": str(row.id),
        "status": row.status,
        "note": row.note,
        "admin_note": row.admin_note,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
        "to_plan_id": str(row.to_plan_id),
        "to_plan_name": to_plan.name if to_plan else None,
        "from_plan_name": from_plan.name if from_plan else None,
    }


def _topup_response(invoice: CreditTopUp, *, email: str | None = None) -> dict:
    return {
        "id": str(invoice.id),
        "credits": invoice.credits,
        "amount_rub": invoice.amount_rub,
        "status": invoice.status,
        "created_at": invoice.created_at,
        "paid_at": invoice.paid_at,
        "payment_url": payment_url_for_invoice(invoice, email=email),
    }


def _ledger_response(entry: CreditLedgerEntry) -> dict:
    cost_rub = float(usage_credits_to_rub(entry.amount)) if entry.reason == "chat_message" else None
    return {
        "id": str(entry.id),
        "amount": entry.amount,
        "reason": entry.reason,
        "project_id": str(entry.project_id) if entry.project_id else None,
        "project_name": entry.project_name,
        "provider": entry.provider,
        "model": entry.model,
        "input_tokens": entry.input_tokens,
        "cached_input_tokens": entry.cached_input_tokens,
        "cache_write_input_tokens": entry.cache_write_input_tokens,
        "output_tokens": entry.output_tokens,
        "provider_cost_usd": (
            entry.provider_cost_usd_micros / 1_000_000
            if entry.provider_cost_usd_micros is not None
            else None
        ),
        "cost_rub": cost_rub,
        "created_at": entry.created_at,
    }


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Plan).filter(Plan.is_active.is_(True)).order_by(Plan.sort_order).all()
    return [_plan_response(row) for row in rows]


@router.get("/me")
def get_my_billing(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    plan = db.get(Plan, current_user.plan_id) if current_user.plan_id else None
    pending = get_pending_request(db, current_user)
    return {
        "credits_balance": current_user.credits_balance,
        "balance_rub": float(usage_credits_to_rub(current_user.credits_balance)),
        "billing_period_start": current_user.billing_period_start,
        "billing_period_end": current_user.billing_period_end,
        "plan": _plan_response(plan) if plan else None,
        "pending_plan_request": (
            _plan_request_response(pending, _plan_index(db)) if pending else None
        ),
        "robokassa_enabled": settings.robokassa_enabled,
    }


@router.post("/topups")
def create_topup(
    payload: TopUpRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    invoice = request_topup(db, current_user, payload.credits)
    return _topup_response(invoice, email=current_user.email)


@router.get("/topups")
def list_topups(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    rows = (
        db.query(CreditTopUp)
        .filter(CreditTopUp.user_id == current_user.id)
        .order_by(CreditTopUp.created_at.desc())
        .all()
    )
    return [_topup_response(row, email=current_user.email) for row in rows]


async def _robokassa_payload(request: Request) -> dict[str, str]:
    payload = {key: value for key, value in request.query_params.items()}
    if request.method == "POST":
        form = await request.form()
        for key, value in form.items():
            payload[str(key)] = str(value)
    return payload


def _profile_redirect(status: str, method: str) -> RedirectResponse:
    url = f"{settings.resolved_frontend_url.rstrip('/')}/app/profile?payment={status}"
    code = 303 if method == "POST" else 302
    return RedirectResponse(url, status_code=code)


@router.api_route("/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    """Robokassa Result URL: server-to-server notice that money was captured.

    Must answer with the exact body `OK{InvId}` or Robokassa will retry.
    """
    if not settings.robokassa_password2:
        logger.error("Robokassa Result URL called but ROBOKASSA_PASSWORD2 is not set")
        return PlainTextResponse("bad request", status_code=400)

    payload = await _robokassa_payload(request)
    out_sum = (payload.get("OutSum") or "").strip()
    inv_id = parse_inv_id(payload.get("InvId"))
    if not out_sum or inv_id is None:
        logger.warning("Robokassa result missing OutSum/InvId")
        return PlainTextResponse("bad request", status_code=400)

    expected = result_signature(out_sum=out_sum, inv_id=inv_id, shp=extract_shp(payload))
    if not signatures_match(expected, payload.get("SignatureValue")):
        logger.warning("Robokassa result inv_id=%s invalid signature", inv_id)
        return PlainTextResponse("bad signature", status_code=400)

    invoice = db.query(CreditTopUp).filter(CreditTopUp.inv_id == inv_id).first()
    if invoice is None:
        logger.warning("Robokassa result inv_id=%s invoice not found", inv_id)
        return PlainTextResponse("bad request", status_code=400)
    if invoice.status == "cancelled":
        logger.warning("Robokassa result inv_id=%s invoice cancelled", inv_id)
        return PlainTextResponse("bad request", status_code=400)
    if not amounts_match(out_sum, invoice.amount_rub):
        logger.warning(
            "Robokassa result inv_id=%s amount mismatch out_sum=%s expected=%s",
            inv_id,
            out_sum,
            invoice.amount_rub,
        )
        return PlainTextResponse("bad request", status_code=400)

    mark_topup_paid(db, invoice)
    db.commit()
    logger.info("Robokassa result inv_id=%s credited", inv_id)
    return PlainTextResponse(f"OK{inv_id}")


@router.api_route("/robokassa/success", methods=["GET", "POST"])
async def robokassa_success(request: Request) -> RedirectResponse:
    payload = await _robokassa_payload(request)
    out_sum = (payload.get("OutSum") or "").strip()
    inv_id = parse_inv_id(payload.get("InvId"))
    if settings.robokassa_password1 and out_sum and inv_id is not None:
        expected = success_signature(out_sum=out_sum, inv_id=inv_id, shp=extract_shp(payload))
        if not signatures_match(expected, payload.get("SignatureValue")):
            logger.warning("Robokassa success inv_id=%s invalid signature", inv_id)
            return _profile_redirect("fail", request.method)
    return _profile_redirect("success", request.method)


@router.api_route("/robokassa/fail", methods=["GET", "POST"])
async def robokassa_fail(request: Request) -> RedirectResponse:
    return _profile_redirect("fail", request.method)


def _plan_index(db: Session) -> dict[str, Plan]:
    return {str(row.id): row for row in db.query(Plan).all()}


@router.post("/plan-requests")
def create_plan_request(
    payload: PlanChangeRequestPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """File a plan change request. Paid plans are granted only after an admin approves."""
    plan = db.query(Plan).filter(Plan.id == payload.plan_id, Plan.is_active.is_(True)).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    try:
        request = create_request(db, current_user, plan, note=payload.note)
    except PlanRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _plan_request_response(request, _plan_index(db))


@router.get("/plan-requests")
def get_plan_requests(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    plans = _plan_index(db)
    return [_plan_request_response(row, plans) for row in list_requests(db, current_user)]


@router.post("/plan-requests/{request_id}/cancel")
def cancel_plan_request(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        parsed = uuid.UUID(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Заявка не найдена") from exc
    try:
        request = cancel_request(db, current_user, parsed)
    except PlanRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _plan_request_response(request, _plan_index(db))


@router.get("/usage")
def get_usage_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    direction: LedgerDirectionParam = Query(default="all"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_ledger(db, current_user, limit=limit, offset=offset, direction=direction)
    return {
        "items": [_ledger_response(row) for row in rows],
        "total": total,
    }
