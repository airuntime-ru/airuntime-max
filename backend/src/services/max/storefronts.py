"""Storefronts and their owners: everything done to them, whoever asks.

The mini app's owner API creates, edits, publishes and deletes storefronts through this
module; the bot only uses it to remember which chat an owner talks to it from. It used to
live inside the bot, back when storefronts were made in the chat - and that coupling is
what had to go once they moved into the mini app.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.max_platform import SERVICE_LIVE, MaxOwner, MaxService
from src.services.max.briefing import BriefingError, decode_attachments, fetch_site_context
from src.services.max.generator import apply_edit, generate_config
from src.services.max.schema import ServiceConfig, normalise, slugify

MAX_SERVICES_PER_OWNER = 10


def ensure_owner(
    db: Session,
    *,
    user_id: int,
    first_name: str = "",
    last_name: str = "",
    username: str = "",
    chat_id: int | None = None,
) -> MaxOwner:
    """The owner row for a MAX user, created on first sight and refreshed on every visit.

    ``chat_id`` is the dialog with the bot. It is only known once the person has started
    the bot or opened the mini app from that dialog, and it is where lead notifications
    go; without it they are sent by user id instead.
    """
    owner = db.query(MaxOwner).filter(MaxOwner.max_user_id == user_id).first()
    if owner is None:
        owner = MaxOwner(max_user_id=user_id)
        db.add(owner)
    name = f"{first_name} {last_name}".strip()
    if name:
        owner.name = name[:255]
    if username:
        owner.username = username[:255]
    if chat_id:
        owner.max_chat_id = chat_id
    db.flush()
    return owner


def unique_slug(db: Session, title: str) -> str:
    base = slugify(title)
    candidate = base
    # Four hex characters is enough to disambiguate within one owner's handful of services
    # while keeping the deep link short enough to read off a printed QR code.
    while db.query(MaxService).filter(MaxService.slug == candidate).first() is not None:
        candidate = f"{base[:34]}-{secrets.token_hex(2)}"
    return candidate


def load_config(service: MaxService) -> ServiceConfig:
    return ServiceConfig.model_validate(json.loads(service.config_json))


def store_config(service: MaxService, config: ServiceConfig) -> None:
    service.config_json = json.dumps(config.model_dump(), ensure_ascii=False)
    service.title = config.title[:255]
    service.kind = config.kind


def service_link(service: MaxService) -> str:
    return settings.build_max_service_link(service.slug)


def customer_chat_url(user_id: int | None) -> str:
    """Open the customer's MAX dialog from the owner's mini app.

    MAX documents ``max://user/<id>`` as the mention deeplink when the person has no
    public username. ``openMaxLink`` only keeps ``https://max.ru/…`` inside the client,
    so the https form is what the button uses; the scheme form is the fallback.
    """
    if not user_id or user_id <= 0:
        return ""
    return f"https://max.ru/id{int(user_id)}"


def _compose_prompt(brief: str, site_text: str, file_notes: str) -> str:
    owner_brief = brief.strip()
    parts: list[str] = []
    if owner_brief:
        parts.append(f"ГЛАВНЫЙ ЗАПРОС ВЛАДЕЛЬЦА — задача и фокус витрины:\n{owner_brief}")
    if site_text:
        parts.append(
            "МАТЕРИАЛЫ САЙТА — источник фактов, цен, фото и языка бренда; "
            "они не расширяют задачу владельца сами по себе:\n"
            f"{site_text}"
        )
    if file_notes:
        parts.append(f"ПРИЛОЖЕННЫЕ МАТЕРИАЛЫ ВЛАДЕЛЬЦА:\n{file_notes}")
    # Long restaurant and marketplace pages can be thousands of tokens. Repeating the
    # actual request at the end keeps a broad source catalogue from silently becoming the
    # task. This constrains *what* the storefront sells, not how the art director designs it.
    if owner_brief and (site_text or file_notes):
        parts.append(
            "ФИНАЛЬНАЯ ПРОВЕРКА ФОКУСА:\n"
            f"Собери именно «{owner_brief}». Если материалы шире этого запроса, выбери "
            "только релевантные позиции; не добавляй соседние категории ради разнообразия. "
            "Свободу проявляй в арт-дирекции, композиции и тексте."
        )
    return "\n\n".join(parts)


def _owner_hero_image(images: list[object]) -> str:
    """Persist the first owner image for the renderer, never a model-provided URL."""
    if not images:
        return ""
    image = images[0]
    mime = str(getattr(image, "content_type", "") or "").lower()
    payload = str(getattr(image, "data_base64", "") or "")
    if mime not in {"image/jpeg", "image/png", "image/webp"} or not payload:
        return ""
    try:
        base64.b64decode(payload, validate=True)
    except ValueError:
        return ""
    return f"data:{mime};base64,{payload}"


async def create_storefront(
    db: Session,
    owner: MaxOwner,
    brief: str,
    *,
    site_url: str = "",
    files: list[dict[str, str]] | None = None,
) -> tuple[MaxService, bool]:
    """One description in, a published storefront out. Returns ``(service, used_llm)``.

    Published straight away on purpose: the owner is looking at it in the next second, and
    "draft until you find the publish button" would be a step between them and the link
    the whole product exists to hand them.
    """
    site_text = ""
    site_images: list[object] = []
    if site_url.strip():
        site_text, site_images = await asyncio.to_thread(fetch_site_context, site_url)
    file_images, file_notes = decode_attachments(files or [])
    # Explicit uploads are stronger art direction than a site's generic social preview.
    images = [*file_images, *site_images]
    prompt = _compose_prompt(brief, site_text, file_notes)
    if not prompt.strip():
        raise BriefingError("Опишите бизнес, укажите сайт или приложите файл")
    config, used_llm = await generate_config(prompt, images=images)
    hero_image = _owner_hero_image(images)
    if hero_image:
        config = config.model_copy(update={"hero_image": hero_image})
    service = MaxService(
        owner_id=owner.id,
        slug=unique_slug(db, config.title),
        kind=config.kind,
        title=config.title[:255],
        status=SERVICE_LIVE,
        config_json="{}",
        prompt=prompt[:4000],
    )
    store_config(service, config)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service, used_llm


async def edit_storefront(db: Session, service: MaxService, instruction: str) -> bool:
    """Apply a free-form change ("добавь маникюр 1500"). The slug never changes, so every
    link and QR code already handed out keeps working."""
    updated, changed = await apply_edit(load_config(service), instruction)
    if changed:
        store_config(service, updated)
        db.commit()
    return changed


def update_catalog(
    db: Session,
    service: MaxService,
    *,
    title: str | None = None,
    tagline: str | None = None,
    about: str | None = None,
    items: list[dict[str, object]] | None = None,
    allow_multiple_items: bool | None = None,
) -> ServiceConfig:
    """Edit the catalog in place - title, tagline, about, items - without calling the model.

    The owner of a café needs to fix a price in two taps, not by writing a prompt that
    might or might not be applied.
    """
    data = load_config(service).model_dump()
    if title is not None:
        cleaned = title.strip()
        if cleaned:
            data["title"] = cleaned[:120]
    if tagline is not None:
        data["tagline"] = tagline.strip()[:160]
    if about is not None:
        data["about"] = about.strip()[:600]
    if items is not None:
        data["items"] = items
    if allow_multiple_items is not None:
        data["allow_multiple_items"] = allow_multiple_items
    updated = normalise(ServiceConfig.model_validate(data))
    store_config(service, updated)
    db.commit()
    return updated
