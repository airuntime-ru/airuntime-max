from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "brand" / "email-logo.png"
LEGACY_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "brand" / "logo-mark.png"
LOGO_CID = "airuntime-logo"

_HEADER_UNSAFE = re.compile(r"[\r\n]+")


def _safe_header(value: str) -> str:
    if _HEADER_UNSAFE.search(value):
        raise ValueError("Email header value contains CR/LF")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Email header value is empty")
    return cleaned


def _safe_address(value: str) -> str:
    name, addr = parseaddr(value)
    addr = _safe_header(addr)
    if "@" not in addr:
        raise ValueError("Invalid email address")
    if name:
        return formataddr((_safe_header(name), addr))
    return addr


def send_email(
    *,
    to: str,
    subject: str,
    plain: str,
    html: str | None = None,
    embed_logo: bool = True,
) -> bool:
    if not settings.smtp_host:
        return False

    message = EmailMessage()
    from_addr = _safe_address(settings.smtp_from)
    from_domain = parseaddr(from_addr)[1].split("@")[-1]
    display_name = _safe_header(settings.smtp_from_name or "AIRuntime")
    message["From"] = formataddr((display_name, parseaddr(from_addr)[1]))
    message["To"] = _safe_address(to)
    message["Subject"] = _safe_header(subject)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=from_domain)
    if settings.smtp_reply_to:
        message["Reply-To"] = _safe_address(settings.smtp_reply_to)
    elif settings.support_email:
        message["Reply-To"] = _safe_address(settings.support_email)

    # Never log OTP/reset tokens — callers must not put secrets in subject.
    message.set_content(plain)

    if html:
        message.add_alternative(html, subtype="html")
        logo_file = LOGO_PATH if LOGO_PATH.exists() else LEGACY_LOGO_PATH
        if embed_logo and logo_file.exists():
            html_part = message.get_payload()[-1]
            with logo_file.open("rb") as handle:
                html_part.add_related(
                    handle.read(),
                    maintype="image",
                    subtype="png",
                    cid=f"<{LOGO_CID}>",
                    filename="email-logo.png",
                )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except Exception:
        logger.exception(
            "Transactional email failed to=%s subject=%s", parseaddr(to)[1], subject[:80]
        )
        return False
    logger.info("Transactional email sent to=%s subject=%s", parseaddr(to)[1], subject[:80])
    return True


def send_branded_email(*, to: str, subject: str, plain: str, html: str) -> bool:
    return send_email(to=to, subject=subject, plain=plain, html=html, embed_logo=True)
