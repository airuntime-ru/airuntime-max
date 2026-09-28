"""Platform media generation via RouterAI (images) and local PDF rendering."""

from __future__ import annotations

import base64
import logging
from io import BytesIO

import httpx

from src.core.config import ROUTERAI_DEFAULT_BASE_URL, settings
from src.services.system_settings import resolve_platform_api_key

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "openai/gpt-image-1"


def _resolve_api_key(explicit: str | None) -> str:
    key = (explicit or "").strip()
    if key:
        return key
    key = (resolve_platform_api_key("routerai") or resolve_platform_api_key("openai") or "").strip()
    if key:
        return key
    key = (settings.routerai_api_key or settings.openai_api_key or "").strip()
    if not key:
        raise RuntimeError("RouterAI/OpenAI API key is not configured for media generation")
    return key


def _base_url() -> str:
    return (settings.openai_base_url or ROUTERAI_DEFAULT_BASE_URL).rstrip("/")


def generate_image_bytes(
    *,
    prompt: str,
    api_key: str | None = None,
    model: str = DEFAULT_IMAGE_MODEL,
    size: str = "1024x1024",
) -> tuple[bytes, str]:
    """Generate an image and return raw bytes + content type."""
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise ValueError("prompt is required")
    response = httpx.post(
        f"{_base_url()}/images/generations",
        headers={"Authorization": f"Bearer {_resolve_api_key(api_key)}"},
        json={
            "model": model,
            "prompt": cleaned,
            "size": size,
            "response_format": "b64_json",
        },
        timeout=180.0,
    )
    if response.status_code >= 400:
        logger.warning(
            "image generation failed: HTTP %s %s", response.status_code, response.text[:300]
        )
        response.raise_for_status()
    payload = response.json()
    items = payload.get("data") or []
    if not items:
        raise RuntimeError("image generation returned no data")
    item = items[0]
    if item.get("b64_json"):
        return base64.b64decode(str(item["b64_json"])), "image/png"
    if item.get("url"):
        img = httpx.get(str(item["url"]), timeout=60.0)
        img.raise_for_status()
        content_type = img.headers.get("content-type") or "image/png"
        return img.content, content_type.split(";")[0]
    raise RuntimeError("image generation response missing b64_json/url")


def generate_pdf_bytes(*, title: str, content: str) -> bytes:
    """Render a simple UTF-8 PDF from plain text/markdown-ish content."""
    from PIL import Image, ImageDraw, ImageFont

    title_text = (title or "Document").strip()
    body = (content or "").strip()
    if not body:
        body = title_text

    page_w, page_h = 1240, 1754
    margin = 72
    line_height = 34
    max_chars = 92

    try:
        font = ImageFont.truetype("arial.ttf", 22)
        title_font = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
        title_font = font

    def wrap(text: str) -> list[str]:
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            if not paragraph.strip():
                lines.append("")
                continue
            words = paragraph.split()
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
        return lines or [""]

    lines = [title_text, ""] + wrap(body)
    pages: list[Image.Image] = []
    y = margin
    img = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(img)

    for line in lines:
        if y > page_h - margin - line_height:
            pages.append(img)
            img = Image.new("RGB", (page_w, page_h), "white")
            draw = ImageDraw.Draw(img)
            y = margin
        active_font = title_font if line == title_text and pages == [] and y == margin else font
        draw.text((margin, y), line, fill="black", font=active_font)
        y += line_height + (8 if line == title_text else 0)

    pages.append(img)

    buf = BytesIO()
    pages[0].save(
        buf, format="PDF", save_all=True, append_images=pages[1:] if len(pages) > 1 else []
    )
    return buf.getvalue()
