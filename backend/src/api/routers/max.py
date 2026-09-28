"""HTTP surface for the MAX integration: the bot webhook and the mini app's API.

Two different trust models live here, and conflating them would be the security bug of
this feature:

- ``/max/webhook/{secret}`` is called by MAX. Deliveries are not signed, so the secret in
  the path is the whole authentication; it must never appear in a log line or a response.
- ``/max/miniapp/*`` is called by the mini app running inside a customer's MAX client.
  Identity comes exclusively from the ``X-Max-Init-Data`` header, verified against the bot
  token. No endpoint here accepts a user id from the body.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.max_platform import (
    LEAD_CONFIRMED,
    LEAD_DECLINED,
    LEAD_DONE,
    LEAD_NEW,
    SERVICE_DISABLED,
    SERVICE_LIVE,
    MaxLead,
    MaxOwner,
    MaxService,
)
from src.db.session import get_db
from src.services.max import bot as max_bot
from src.services.max import storefronts
from src.services.max.briefing import BriefingError
from src.services.max.init_data import InitDataError, MaxLaunchContext, verify_init_data
from src.services.max.schema import ServiceConfig
from src.services.max.slots import scheduled_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/max", tags=["max"])

MAX_LEADS_PAGE = 50


# --------------------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------------------


def require_launch_context(
    x_max_init_data: str = Header(default="", alias="X-Max-Init-Data"),
) -> MaxLaunchContext:
    """The mini app's only identity. Everything downstream trusts this and nothing else."""
    try:
        return verify_init_data(
            x_max_init_data,
            settings.max_bot_token or "",
            max_age_seconds=settings.max_init_data_max_age_seconds,
        )
    except InitDataError as exc:
        # The message is safe to surface: it says the data is bad, never why the secret
        # did not match.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------
# Webhook
# --------------------------------------------------------------------------------------


@router.post("/webhook/{secret}", status_code=status.HTTP_200_OK)
async def max_webhook(secret: str, request: Request, db: Session = Depends(get_db)) -> dict:
    configured = (settings.max_webhook_secret or "").strip()
    if not configured or secret != configured:
        # 404 rather than 403: an unconfigured or mistyped endpoint should look like it
        # does not exist, not like a guarded door.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed update"
        ) from None
    if not isinstance(update, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed update")

    try:
        await max_bot.handle_update(db, update)
    except Exception:
        # Always ack. MAX redelivers on non-2xx, and a handler bug would turn one broken
        # message into a retry storm against our own API.
        db.rollback()
        logger.exception("max_webhook_failed type=%s", update.get("update_type"))
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Mini app: customer side
# --------------------------------------------------------------------------------------


class LeadItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=20)


class LeadRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    item_title: str = Field(default="", max_length=255)
    items: list[LeadItemIn] = Field(default_factory=list, max_length=12)
    slot_label: str = Field(default="", max_length=64)
    customer_name: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=32)
    comment: str = Field(default="", max_length=1000)


def _service_payload(service: MaxService, *, my_leads: list[MaxLead] | None = None) -> dict:
    config = ServiceConfig.model_validate(json.loads(service.config_json))
    payload = {
        "slug": service.slug,
        "status": service.status,
        "config": config.model_dump(),
    }
    if my_leads is not None:
        payload["my_leads"] = [_customer_lead_payload(lead) for lead in my_leads]
    return payload


def _customer_lead_payload(lead: MaxLead) -> dict:
    return {
        "id": str(lead.id),
        "item_title": lead.item_title,
        "slot_label": lead.slot_label,
        "status": lead.status,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "scheduled_at": scheduled_iso(lead.slot_label, lead.created_at),
    }


def _owner_lead_payload(lead: MaxLead, title: str, service_slug: str) -> dict:
    return {
        "id": str(lead.id),
        "service_title": title,
        "service_slug": service_slug,
        "customer_name": lead.customer_name,
        "phone": lead.phone,
        "item_title": lead.item_title,
        "slot_label": lead.slot_label,
        "comment": lead.comment,
        "status": lead.status,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "scheduled_at": scheduled_iso(lead.slot_label, lead.created_at),
        "chat_url": storefronts.customer_chat_url(lead.max_user_id),
    }


@router.get("/miniapp/service/{slug}")
def get_service(
    slug: str,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    """The storefront a customer sees. Verified launch data is required even though the
    content is public — it is what proves the request came from inside MAX.

    An unpublished storefront is still shown to its own owner, so they can check a change
    before letting customers see it."""
    service = db.query(MaxService).filter(MaxService.slug == slug).first()
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    if service.status != SERVICE_LIVE:
        owner = db.query(MaxOwner).filter(MaxOwner.id == service.owner_id).first()
        if owner is None or owner.max_user_id != launch.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    history = (
        db.query(MaxLead)
        .filter(MaxLead.service_id == service.id, MaxLead.max_user_id == launch.user_id)
        .order_by(MaxLead.created_at.desc())
        .limit(20)
        .all()
    )
    return _service_payload(service, my_leads=history)


@router.post("/miniapp/lead", status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    service = db.query(MaxService).filter(MaxService.slug == payload.slug).first()
    if service is None or service.status != SERVICE_LIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    config = ServiceConfig.model_validate(json.loads(service.config_json))
    # The item and slot must be ones this storefront actually offers: otherwise the owner's
    # chat becomes a place anyone can write arbitrary text into.
    titles = {item.title for item in config.items}
    requested = payload.items or (
        [LeadItemIn(title=payload.item_title)] if payload.item_title else []
    )
    quantities: dict[str, int] = {}
    for item in requested:
        if item.title not in titles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown item"
            )
        quantities[item.title] = quantities.get(item.title, 0) + item.quantity
        if quantities[item.title] > 20:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Too many items"
            )
    if not config.allow_multiple_items and (
        len(quantities) > 1 or any(quantity != 1 for quantity in quantities.values())
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This storefront accepts one item at a time",
        )
    item_title = ", ".join(
        f"{quantity}× {title}" if config.allow_multiple_items else title
        for title, quantity in quantities.items()
    )
    if len(item_title) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Слишком много позиций в одном заказе",
        )
    if payload.slot_label and payload.slot_label not in set(config.slots):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown slot")

    lead = MaxLead(
        service_id=service.id,
        max_user_id=launch.user_id,
        customer_name=(payload.customer_name or launch.display_name)[:255],
        phone=payload.phone.strip() or None,
        item_title=item_title,
        slot_label=payload.slot_label[:64],
        comment=payload.comment.strip(),
        status=LEAD_NEW,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Delivery is best-effort on purpose: the lead is already durable, and a MAX API hiccup
    # must not turn into a 500 that makes the customer submit twice.
    try:
        max_bot.notify_owner_of_lead(db, lead)
    except Exception:
        logger.warning("max_lead_notify_failed lead_id=%s", lead.id, exc_info=True)

    return {
        "id": str(lead.id),
        "status": lead.status,
        "success_message": config.success_message,
        "item_title": lead.item_title,
    }


# --------------------------------------------------------------------------------------
# Mini app: owner side
# --------------------------------------------------------------------------------------


def _owner_or_404(db: Session, launch: MaxLaunchContext) -> MaxOwner:
    owner = db.query(MaxOwner).filter(MaxOwner.max_user_id == launch.user_id).first()
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner not found")
    return owner


def _owned_service_or_404(db: Session, owner: MaxOwner, slug: str) -> MaxService:
    """Another owner's storefront answers exactly like a missing one."""
    service = (
        db.query(MaxService)
        .filter(MaxService.slug == slug, MaxService.owner_id == owner.id)
        .first()
    )
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


def _owner_service_payload(service: MaxService, new_leads: int, lead_count: int) -> dict:
    return {
        **_service_payload(service),
        "link": settings.build_max_service_link(service.slug),
        "new_leads": new_leads,
        "lead_count": lead_count,
    }


def _new_leads(db: Session, service: MaxService) -> int:
    return (
        db.query(MaxLead)
        .filter(MaxLead.service_id == service.id, MaxLead.status == LEAD_NEW)
        .count()
    )


def _lead_count(db: Session, service: MaxService) -> int:
    return db.query(MaxLead).filter(MaxLead.service_id == service.id).count()


# A storefront needs something to be built from. The old chat wizard turned "привет" into
# a landing page called "Ваш бизнес" with nothing on it; this is the floor that stops it.
MIN_BRIEF_LENGTH = 12


class AttachedFile(BaseModel):
    filename: str = Field(default="file", max_length=200)
    content_type: str = Field(default="", max_length=100)
    data_base64: str = Field(min_length=8, max_length=1_400_000)


class CreateServiceRequest(BaseModel):
    brief: str = Field(default="", max_length=2000)
    site_url: str = Field(default="", max_length=500)
    files: list[AttachedFile] = Field(default_factory=list, max_length=4)


@router.post("/miniapp/owner/services", status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: CreateServiceRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    """One description in, a published storefront out - the product in one request.

    Takes as long as the model does (usually 5-10 s, up to three attempts); the mini app
    shows progress for it. The owner row is created here if this is someone's first
    storefront: the mini app, not the chat, is now where people start."""
    brief = payload.brief.strip()
    site_url = payload.site_url.strip()
    files = [item.model_dump() for item in payload.files]
    if len(brief) < MIN_BRIEF_LENGTH and not site_url and not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Опишите чуть подробнее: чем вы занимаетесь и что предлагаете клиентам",
        )
    owner = storefronts.ensure_owner(
        db,
        user_id=launch.user_id,
        first_name=launch.first_name,
        last_name=launch.last_name,
        username=launch.username,
    )
    db.commit()
    existing = db.query(MaxService).filter(MaxService.owner_id == owner.id).count()
    if existing >= storefronts.MAX_SERVICES_PER_OWNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Можно держать до {storefronts.MAX_SERVICES_PER_OWNER} сервисов — "
                "удалите ненужную, чтобы создать новую"
            ),
        )
    try:
        service, used_llm = await storefronts.create_storefront(
            db, owner, brief, site_url=site_url, files=files
        )
    except BriefingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {**_owner_service_payload(service, 0, 0), "used_llm": used_llm}


class EditServiceRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=1000)


class CatalogItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=280)
    price_rub: int | None = Field(default=None, ge=0, le=10_000_000)


class PatchServiceRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    tagline: str | None = Field(default=None, max_length=160)
    about: str | None = Field(default=None, max_length=600)
    items: list[CatalogItemIn] | None = Field(default=None, max_length=24)
    allow_multiple_items: bool | None = None


@router.post("/miniapp/owner/services/{slug}/edit")
async def edit_service(
    slug: str,
    payload: EditServiceRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    owner = _owner_or_404(db, launch)
    service = _owned_service_or_404(db, owner, slug)
    changed = await storefronts.edit_storefront(db, service, payload.instruction.strip())
    db.refresh(service)
    return {
        **_owner_service_payload(service, _new_leads(db, service), _lead_count(db, service)),
        "changed": changed,
    }


@router.patch("/miniapp/owner/services/{slug}")
def patch_service(
    slug: str,
    payload: PatchServiceRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    owner = _owner_or_404(db, launch)
    service = _owned_service_or_404(db, owner, slug)
    storefronts.update_catalog(
        db,
        service,
        title=payload.title,
        tagline=payload.tagline,
        about=payload.about,
        items=None if payload.items is None else [item.model_dump() for item in payload.items],
        allow_multiple_items=payload.allow_multiple_items,
    )
    db.refresh(service)
    return _owner_service_payload(service, _new_leads(db, service), _lead_count(db, service))


class ServiceStatusRequest(BaseModel):
    status: str = Field(pattern=f"^({SERVICE_LIVE}|{SERVICE_DISABLED})$")


@router.post("/miniapp/owner/services/{slug}/status")
def set_service_status(
    slug: str,
    payload: ServiceStatusRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    owner = _owner_or_404(db, launch)
    service = _owned_service_or_404(db, owner, slug)
    service.status = payload.status
    db.commit()
    return {"slug": service.slug, "status": service.status}


@router.delete("/miniapp/owner/services/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    slug: str,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> Response:
    owner = _owner_or_404(db, launch)
    service = _owned_service_or_404(db, owner, slug)
    # Explicit rather than trusting ON DELETE CASCADE: SQLite, which the test suite can run
    # on, does not enforce foreign keys unless asked to.
    db.query(MaxLead).filter(MaxLead.service_id == service.id).delete(synchronize_session=False)
    db.delete(service)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/miniapp/owner/overview")
def owner_overview(
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    """Services plus their pending-lead counts — the owner's home screen in one request."""
    owner = db.query(MaxOwner).filter(MaxOwner.max_user_id == launch.user_id).first()
    if owner is None:
        # Not an error: someone who opened the mini app before ever writing to the bot.
        return {"owner": None, "services": []}

    services = (
        db.query(MaxService)
        .filter(MaxService.owner_id == owner.id)
        .order_by(MaxService.created_at.desc())
        .all()
    )
    service_ids = [service.id for service in services]
    new_counts: dict[uuid.UUID, int] = {}
    total_counts: dict[uuid.UUID, int] = {}
    if service_ids:
        rows = (
            db.query(MaxLead.service_id, MaxLead.status)
            .filter(MaxLead.service_id.in_(service_ids))
            .all()
        )
        for service_id, status in rows:
            total_counts[service_id] = total_counts.get(service_id, 0) + 1
            if status == LEAD_NEW:
                new_counts[service_id] = new_counts.get(service_id, 0) + 1

    return {
        "owner": {"name": owner.name, "username": owner.username},
        "services": [
            _owner_service_payload(
                service, new_counts.get(service.id, 0), total_counts.get(service.id, 0)
            )
            for service in services
        ],
    }


@router.get("/miniapp/owner/leads")
def owner_leads(
    slug: str | None = None,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    owner = _owner_or_404(db, launch)
    query = (
        db.query(MaxLead, MaxService.title, MaxService.slug)
        .join(MaxService, MaxService.id == MaxLead.service_id)
        .filter(MaxService.owner_id == owner.id)
    )
    if slug:
        query = query.filter(MaxService.slug == slug)
    rows = query.order_by(MaxLead.created_at.desc()).limit(MAX_LEADS_PAGE).all()

    return {
        "leads": [
            _owner_lead_payload(lead, title, service_slug) for lead, title, service_slug in rows
        ]
    }


class LeadStatusRequest(BaseModel):
    status: str = Field(pattern=f"^({LEAD_CONFIRMED}|{LEAD_DECLINED}|{LEAD_DONE}|{LEAD_NEW})$")


@router.post("/miniapp/owner/leads/{lead_id}/status")
def set_lead_status(
    lead_id: uuid.UUID,
    payload: LeadStatusRequest,
    db: Session = Depends(get_db),
    launch: MaxLaunchContext = Depends(require_launch_context),
) -> dict:
    owner = _owner_or_404(db, launch)
    lead = (
        db.query(MaxLead)
        .join(MaxService, MaxService.id == MaxLead.service_id)
        .filter(MaxLead.id == lead_id, MaxService.owner_id == owner.id)
        .first()
    )
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    previous = lead.status
    lead.status = payload.status
    db.commit()

    # The customer booked inside MAX, so the answer goes there too - once, on the change,
    # not again every time the owner taps the same button.
    if payload.status != previous and payload.status in (LEAD_CONFIRMED, LEAD_DECLINED):
        try:
            max_bot.notify_customer_of_decision(
                db, lead, confirmed=payload.status == LEAD_CONFIRMED
            )
        except Exception:
            logger.warning("max_customer_notify_failed lead_id=%s", lead.id, exc_info=True)
    return {"id": str(lead.id), "status": lead.status}
