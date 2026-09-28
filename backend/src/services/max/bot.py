"""The MAX bot: a front door and a messenger, nothing more.

Everything the owner does - describing the business, editing, publishing, working through
leads - happens in the mini app. The chat wizard that used to live here was the wrong
shape for it: every message became a storefront, "привет" included, and there was no way
to see what you had made. So the bot answers anything with one message and one button that
opens the app, and speaks first only when there is news: a new lead for the owner, an
answer for the customer. Every message it sends is that same shape - a few lines and a
button into the mini app.

Handlers make no model calls and touch the database only to remember an owner's chat, so
a webhook delivery is acknowledged in well under the 30 seconds MAX allows before it
starts retrying.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from sqlalchemy.orm import Session

from src.core.config import settings
from src.db.models.max_platform import SERVICE_LIVE, MaxLead, MaxOwner, MaxService
from src.services.max.client import MaxApiError, MaxBotClient, button_link, button_open_app
from src.services.max.storefronts import customer_chat_url, ensure_owner, load_config

logger = logging.getLogger(__name__)

# One command: the bot has nothing else to offer, and a menu of commands that all answer
# "open the app" would be noise.
BOT_COMMANDS = [{"name": "start", "description": "Открыть AIRuntime"}]

OPEN_APP = "Открыть AIRuntime"

# A no-break space before every em dash: otherwise it wraps to the start of a line on a
# phone, which in Russian typography reads as a mistake.
WELCOME = (
    "<h1>AIRuntime</h1>\n"
    "Запись к вам — прямо в MAX.\n\n"
    "Откройте приложение и опишите бизнес — у клиентов появятся услуги, цены "
    "и кнопка записи.\n\n"
    "<b>Что вы получите</b>\n"
    "— ссылку и QR-код для клиентов\n"
    "— заявки прямо сюда, в этот чат\n"
    "— ничего не нужно устанавливать и настраивать"
)


def get_client() -> MaxBotClient:
    return MaxBotClient(
        settings.max_bot_token or "",
        base_url=settings.max_api_base_url,
        ca_bundle=settings.max_ca_bundle,
    )


def _open_app(text: str, slug: str = "") -> dict[str, Any]:
    """A button opening this bot's mini app. The slug becomes its start_param; no slug
    means the owner's view, because the mini app routes on start_param alone."""
    return button_open_app(
        text,
        (settings.max_bot_username or "").lstrip("@"),
        slug,
        contact_id=settings.max_bot_id,
    )


def _e(value: object) -> str:
    """Escape anything a person typed before it goes into an HTML-formatted message."""
    return html.escape(str(value or ""), quote=False)


# --------------------------------------------------------------------------------------
# Outgoing messages
# --------------------------------------------------------------------------------------


def send_welcome(*, chat_id: int | None = None, user_id: int | None = None) -> None:
    """The front door: banner, a few lines, one button into the mini app.

    The banner is fetched by MAX from our own site. If MAX refuses the message over it,
    the welcome goes again without the picture - but only on a refusal: after a timeout
    MAX may already have delivered it, and a second copy would be worse than none.
    """
    client = get_client()
    recipient: dict[str, Any] = {"chat_id": chat_id} if chat_id else {"user_id": user_id}
    message: dict[str, Any] = {
        **recipient,
        "text": WELCOME,
        "html": True,
        "buttons": [[_open_app(OPEN_APP)]],
    }
    image = settings.resolved_max_welcome_image_url
    if image:
        try:
            client.send_message(**message, image_url=image)
            return
        except MaxApiError as exc:
            logger.warning(
                "max_welcome_with_image_failed status=%s body=%s",
                exc.status,
                exc,
            )
            if exc.status != 400:
                return
    client.try_send_message(**message)


def _send_storefront_invite(chat_id: int, service: MaxService) -> None:
    """Someone started the bot from a storefront's link: open that storefront, not ours."""
    config = load_config(service)
    lines = [f"<b>{_e(config.title)}</b>"]
    if config.tagline:
        lines.append(_e(config.tagline))
    get_client().try_send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        html=True,
        buttons=[[_open_app(config.cta_label, service.slug)]],
    )


def notify_owner_of_lead(db: Session, lead: MaxLead) -> None:
    """Push a new lead into the owner's chat - the moment the product pays off.

    Confirming happens in the mini app, where the owner also sees the phone and the rest
    of the queue; the message only has to be impossible to miss.
    """
    service = db.query(MaxService).filter(MaxService.id == lead.service_id).first()
    if service is None:
        return
    owner = db.query(MaxOwner).filter(MaxOwner.id == service.owner_id).first()
    if owner is None:
        return

    what = " · ".join(_e(part) for part in (lead.item_title, lead.slot_label) if part)
    who = ", ".join(_e(part) for part in (lead.customer_name or "без имени", lead.phone) if part)
    lines = [f"<b>Новая заявка</b> · {_e(service.title)}", ""]
    if what:
        lines.append(what)
    lines.append(who)
    if lead.comment:
        lines.append(f"<blockquote>{_e(lead.comment)}</blockquote>")

    recipient: dict[str, Any] = (
        {"chat_id": owner.max_chat_id} if owner.max_chat_id else {"user_id": owner.max_user_id}
    )
    rows = [[_open_app("Открыть заявки")]]
    chat_url = customer_chat_url(lead.max_user_id)
    if chat_url:
        rows.append([button_link("Написать клиенту", chat_url)])
    get_client().try_send_message(
        **recipient,
        text="\n".join(lines),
        html=True,
        buttons=rows,
    )


def notify_customer_of_decision(db: Session, lead: MaxLead, *, confirmed: bool) -> None:
    """Close the loop for the customer: they booked inside MAX, so the answer belongs there
    too. Sent by user id - most customers only ever opened the mini app and have no dialog
    with the bot for a chat id to exist."""
    if not lead.max_user_id:
        return
    service = db.query(MaxService).filter(MaxService.id == lead.service_id).first()
    if service is None:
        return

    when = " · ".join(_e(part) for part in (lead.item_title, lead.slot_label) if part)
    if confirmed:
        lines = ["<b>Запись подтверждена</b>", _e(service.title)]
        if when:
            lines.append(when)
        button = _open_app("Открыть AIRuntime", service.slug)
    else:
        slot = f" на {_e(lead.slot_label)}" if lead.slot_label else ""
        lines = [
            "<b>Время не подошло</b>",
            f"{_e(service.title)} не может принять вас{slot}. "
            "Выберите другое время — это займёт минуту.",
        ]
        button = _open_app("Выбрать другое время", service.slug)

    get_client().try_send_message(
        user_id=lead.max_user_id,
        text="\n".join(lines),
        html=True,
        buttons=[[button]],
    )


# --------------------------------------------------------------------------------------
# Incoming updates
# --------------------------------------------------------------------------------------


async def handle_update(db: Session, update: dict[str, Any]) -> None:
    update_type = str(update.get("update_type") or "")
    if update_type in ("bot_started", "bot_added"):
        _on_started(db, update)
    elif update_type == "message_created":
        _on_message(db, update)
    elif update_type == "message_callback":
        _on_callback(db, update)
    else:
        logger.debug("max_update_ignored type=%s", update_type)


def _remember(db: Session, user: dict[str, Any], chat_id: int | None) -> None:
    """Record whose dialog this is, so a later lead can be delivered into it."""
    user_id = int(user.get("user_id") or user.get("id") or 0)
    if not user_id:
        return
    ensure_owner(
        db,
        user_id=user_id,
        first_name=str(user.get("first_name") or user.get("name") or "").strip(),
        last_name=str(user.get("last_name") or "").strip(),
        username=str(user.get("username") or "").strip(),
        chat_id=chat_id,
    )
    db.commit()


def _on_started(db: Session, update: dict[str, Any]) -> None:
    chat_id = _chat_id(update)
    if not chat_id:
        return
    get_client().mark_seen(chat_id)
    _remember(db, update.get("user") or {}, chat_id)

    # max.ru/<bot>?start=<slug> arrives here with the slug as payload: that visitor wants
    # the storefront, not the owner's front door.
    payload = str(update.get("payload") or "").strip()
    if payload:
        service = db.query(MaxService).filter(MaxService.slug == payload).first()
        if service is not None and service.status == SERVICE_LIVE:
            _send_storefront_invite(chat_id, service)
            return
    send_welcome(chat_id=chat_id)


def _on_message(db: Session, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    sender = (message.get("sender") or {}) or (update.get("user") or {})
    if sender.get("is_bot"):
        return
    recipient = message.get("recipient") or {}
    # In a group the bot would otherwise answer every line anyone writes.
    if str(recipient.get("chat_type") or "dialog").lower() != "dialog":
        return
    chat_id = _chat_id(update)
    if not chat_id:
        return
    get_client().mark_seen(chat_id)
    _remember(db, sender, chat_id)
    send_welcome(chat_id=chat_id)


def _on_callback(db: Session, update: dict[str, Any]) -> None:
    """Buttons from before the bot became a front door can still be tapped in old
    messages. Point them at the app instead of leaving the tap unanswered."""
    callback = update.get("callback") or {}
    get_client().answer_callback(
        str(callback.get("callback_id") or ""),
        notification="Всё управление теперь в приложении",
    )
    chat_id = _chat_id(update)
    if chat_id:
        send_welcome(chat_id=chat_id)


def _chat_id(update: dict[str, Any]) -> int | None:
    raw = update.get("chat_id")
    if raw is None:
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        raw = recipient.get("chat_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
