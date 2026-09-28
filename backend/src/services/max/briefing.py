"""Turn a site URL and owner files into extra briefing for the storefront generator.

The mini app lets someone paste a website or attach a logo, a price list, a design
reference. None of that is the storefront itself: we fetch or decode it here, fold the
usable bits into the prompt, and hand photos to the model as images. A bad URL must not
become an SSRF, and a 20 MB PDF must not land in the wizard request.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import re
import socket
from io import BytesIO
from urllib.parse import urljoin, urlparse

import httpx

from src.services.file_context import IMAGE_CONTENT_TYPES, ImageAttachment

logger = logging.getLogger(__name__)

MAX_FILES = 4
MAX_FILE_BYTES = 900_000
SITE_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
# Next.js storefronts (Coffeemania and the like) put the real menu in a 1.5 MB
# ``__NEXT_DATA__`` script at the *end* of the HTML. A 400 KB cap used to cut it off,
# so the generator only saw the <title> and then invented «Позиция 1».
SITE_BYTES = 2_500_000
SITE_TEXT_CHARS = 3500
SITE_MENU_ITEMS = 16

_ALLOWED_IMAGE_TYPES = IMAGE_CONTENT_TYPES | {"image/jpg"}
_TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
_PDF_TYPE = "application/pdf"

_TAG = re.compile(r"<[^>]+>", re.S)
_NOISE = re.compile(r"<(script|style|noscript|svg)[\s\S]*?</\1>", re.I)
_WS = re.compile(r"\s+")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
    re.I | re.S,
)
_META_DESC_REV = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']',
    re.I | re.S,
)
_META_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\'](.*?)["\']',
    re.I | re.S,
)
_META_IMAGE_REV = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.I | re.S,
)
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.I | re.S)
_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S
)


class BriefingError(ValueError):
    """A user-facing reason the extra briefing could not be used."""


def _host_blocked(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        parsed_ip = ipaddress.ip_address(host)
        return not parsed_ip.is_global
    except ValueError:
        pass
    try:
        answers = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for answer in answers:
        try:
            ip = ipaddress.ip_address(answer[4][0])
        except (ValueError, TypeError, IndexError):
            continue
        if not ip.is_global:
            return True
    return False


def normalise_site_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise BriefingError("Укажите обычную ссылку на сайт, вида https://example.ru")
    if parsed.port not in (None, 80, 443):
        raise BriefingError("Укажите обычную ссылку на сайт, вида https://example.ru")
    if _host_blocked(parsed.hostname):
        raise BriefingError("Эту ссылку открыть нельзя")
    return parsed.geturl()


def _visible_text(html: str) -> str:
    cleaned = _NOISE.sub(" ", html)
    cleaned = _TAG.sub(" ", cleaned)
    return _WS.sub(" ", cleaned).strip()


def _to_rub(price: int) -> int:
    """Delivery platforms often store roubles as kopecks (86000 → 860 ₽)."""
    if price >= 10_000 and price % 100 == 0:
        return price // 100
    return price


def _json_ld_bits(html: str) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for match in _LD_JSON.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                parts.append(f"Бренд: {name}")
            phone = str(node.get("telephone") or "").strip()
            if phone:
                parts.append(f"Телефон: {phone}")
            description = str(node.get("description") or "").strip()
            if description:
                parts.append(f"Описание: {description}")
    return parts


def _product_image_url(product: dict[str, object]) -> str:
    images = product.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return ""
    thumbnails = images[0].get("thumbnails")
    if isinstance(thumbnails, list):
        candidates = [thumb for thumb in thumbnails if isinstance(thumb, dict)]
        candidates.sort(key=lambda thumb: int(thumb.get("width") or 0), reverse=True)
        for thumb in candidates:
            url = str(thumb.get("url") or "").strip()
            if url.startswith("https://"):
                return url
    url = str(images[0].get("url") or "").strip()
    return url if url.startswith("https://") else ""


def _collect_priced_products(
    obj: object,
    acc: list[tuple[str, int, str, str]],
    *,
    limit: int,
    category: str = "",
) -> None:
    if len(acc) >= limit:
        return
    if isinstance(obj, dict):
        title = str(obj.get("title") or obj.get("name") or "").strip()
        price = obj.get("price")
        if title and isinstance(price, int | float) and float(price) > 0:
            acc.append((title, _to_rub(int(price)), category, _product_image_url(obj)))
            return
        for key in ("props", "pageProps", "categories", "products", "items", "dishes"):
            if key in obj:
                child_category = category
                if key == "products":
                    child_category = title or category
                _collect_priced_products(obj[key], acc, limit=limit, category=child_category)
                if len(acc) >= limit:
                    return
        return
    if isinstance(obj, list):
        for child in obj:
            _collect_priced_products(child, acc, limit=limit, category=category)
            if len(acc) >= limit:
                return


def _menu_from_next_data(html: str) -> str:
    match = _NEXT_DATA.search(html)
    if not match:
        return ""
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    discovered: list[tuple[str, int, str, str]] = []
    # Do not spend the whole briefing on the first category in a large restaurant site.
    # Collect broadly, then round-robin the categories so a request for "coffee shop" can
    # actually see Coffee, Desserts and Bakery even when Hot dishes appears first.
    _collect_priced_products(data, discovered, limit=160)
    if not discovered:
        return ""
    by_category: dict[str, list[tuple[str, int, str, str]]] = {}
    for item in discovered:
        by_category.setdefault(item[2], []).append(item)
    items: list[tuple[str, int, str, str]] = []
    offset = 0
    while len(items) < SITE_MENU_ITEMS:
        added = False
        for group in by_category.values():
            if offset < len(group):
                items.append(group[offset])
                added = True
                if len(items) >= SITE_MENU_ITEMS:
                    break
        if not added:
            break
        offset += 1
    lines: list[str] = []
    for title, price, category, image_url in items:
        prefix = f"[{category}] " if category else ""
        lines.append(f"- {prefix}{title} — {price} ₽")
        if image_url:
            lines.append(f"  Фото: {image_url}")
    return "Позиции с сайта:\n" + "\n".join(lines)


def _first_catalog_image(html: str) -> str:
    match = _NEXT_DATA.search(html)
    if not match:
        return ""
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    items: list[tuple[str, int, str, str]] = []
    _collect_priced_products(data, items, limit=1)
    return items[0][3] if items else ""


def _extract_site(html: str, url: str) -> str:
    title_match = _TITLE.search(html)
    title = _WS.sub(" ", _TAG.sub("", title_match.group(1))).strip() if title_match else ""
    desc_match = _META_DESC.search(html) or _META_DESC_REV.search(html)
    description = _WS.sub(" ", desc_match.group(1)).strip() if desc_match else ""
    body = _visible_text(html)[:SITE_TEXT_CHARS]
    colors = []
    for color in _HEX.findall(html[:80_000]):
        if color.lower() in {"#fff", "#ffffff", "#000", "#000000"}:
            continue
        if color not in colors:
            colors.append(color)
        if len(colors) == 6:
            break
    parts = [f"Сайт владельца: {url}"]
    if title:
        parts.append(f"Название страницы: {title}")
    parts.extend(_json_ld_bits(html))
    if description and f"Описание: {description}" not in parts:
        parts.append(f"Описание: {description}")
    menu = _menu_from_next_data(html)
    if menu:
        parts.append(menu)
    if colors:
        parts.append("Цвета с сайта: " + ", ".join(colors))
    if body:
        parts.append("Текст страницы:\n" + body)
    return "\n".join(parts)


def fetch_site_brief(url: str) -> str:
    """Download a public page and return a compact text briefing. Empty on fetch failure."""
    try:
        target = normalise_site_url(url)
    except BriefingError:
        raise
    if not target:
        return ""
    try:
        response = httpx.get(
            target,
            timeout=SITE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "AIRuntime-MAX/1.0 (+https://airuntime.ru)"},
        )
    except httpx.HTTPError as exc:
        logger.info("max_site_fetch_failed url=%s", target, extra={"error": type(exc).__name__})
        raise BriefingError(
            "Не удалось открыть сайт. Проверьте ссылку или опишите бизнес текстом."
        ) from None
    if response.status_code >= 400:
        raise BriefingError("Сайт не открылся. Проверьте ссылку или опишите бизнес текстом.")
    final_host = urlparse(str(response.url)).hostname or ""
    if _host_blocked(final_host):
        raise BriefingError("Эту ссылку открыть нельзя")
    html = response.content[:SITE_BYTES].decode(response.encoding or "utf-8", errors="replace")
    return _extract_site(html, str(response.url))


def _site_image(html: str, page_url: str) -> ImageAttachment | None:
    """Fetch a public social/hero image with the same SSRF boundary as the page itself."""
    match = _META_IMAGE.search(html) or _META_IMAGE_REV.search(html)
    raw_url = match.group(1).strip() if match else _first_catalog_image(html)
    if not raw_url:
        return None
    image_url = urljoin(page_url, raw_url)
    parsed = urlparse(image_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or _host_blocked(parsed.hostname)
    ):
        return None
    try:
        with httpx.stream(
            "GET",
            image_url,
            timeout=SITE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "AIRuntime-MAX/1.0 (+https://airuntime.ru)"},
        ) as response:
            final_host = urlparse(str(response.url)).hostname or ""
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            if (
                response.status_code >= 400
                or _host_blocked(final_host)
                or content_type not in _ALLOWED_IMAGE_TYPES
            ):
                return None
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    return None
                chunks.append(chunk)
    except httpx.HTTPError:
        return None
    payload, mime = _shrink_image(b"".join(chunks), content_type)
    return ImageAttachment(
        filename="site-hero.jpg",
        content_type=mime,
        data_base64=base64.b64encode(payload).decode("ascii"),
    )


def fetch_site_context(url: str) -> tuple[str, list[ImageAttachment]]:
    """Return the existing textual brief plus a safe local copy of the site's hero image."""
    target = normalise_site_url(url)
    try:
        response = httpx.get(
            target,
            timeout=SITE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "AIRuntime-MAX/1.0 (+https://airuntime.ru)"},
        )
    except httpx.HTTPError:
        raise BriefingError(
            "Не удалось открыть сайт. Проверьте ссылку или опишите бизнес текстом."
        ) from None
    if response.status_code >= 400:
        raise BriefingError("Сайт не открылся. Проверьте ссылку или опишите бизнес текстом.")
    final_url = str(response.url)
    if _host_blocked(urlparse(final_url).hostname or ""):
        raise BriefingError("Эту ссылку открыть нельзя")
    html = response.content[:SITE_BYTES].decode(response.encoding or "utf-8", errors="replace")
    image = _site_image(html, final_url)
    return _extract_site(html, final_url), [image] if image else []


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages[:8]:
            chunks.append(page.extract_text() or "")
        return _WS.sub(" ", "\n".join(chunks)).strip()[:SITE_TEXT_CHARS]
    except Exception:
        logger.info("max_pdf_extract_failed", exc_info=True)
        return ""


def _shrink_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Keep vision payloads small enough for a 10-second wizard turn."""
    try:
        from PIL import Image

        image = Image.open(BytesIO(data))
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=80, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        return data, content_type


def decode_attachments(
    files: list[dict[str, str]],
) -> tuple[list[ImageAttachment], str]:
    """Return vision images and extra text (PDF / notes) from the mini app payload."""
    if len(files) > MAX_FILES:
        raise BriefingError(f"Можно прикрепить не больше {MAX_FILES} файлов")
    images: list[ImageAttachment] = []
    notes: list[str] = []
    for item in files:
        filename = str(item.get("filename") or "file")[:200]
        content_type = str(item.get("content_type") or "").split(";")[0].strip().lower()
        if content_type == "image/jpg":
            content_type = "image/jpeg"
        try:
            data = base64.b64decode(item.get("data_base64") or "", validate=False)
        except Exception as exc:
            raise BriefingError(f"Не удалось прочитать файл {filename}") from exc
        if not data:
            continue
        if len(data) > MAX_FILE_BYTES:
            raise BriefingError(f"{filename} слишком большой — до 900 КБ на файл")
        if content_type in _ALLOWED_IMAGE_TYPES or filename.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif")
        ):
            payload, mime = _shrink_image(data, content_type or "image/jpeg")
            images.append(
                ImageAttachment(
                    filename=filename,
                    content_type=mime,
                    data_base64=base64.b64encode(payload).decode("ascii"),
                )
            )
            notes.append(f"Фото «{filename}»: логотип, интерьер или референс дизайна.")
        elif content_type == _PDF_TYPE or filename.lower().endswith(".pdf"):
            extracted = _pdf_text(data)
            if extracted:
                notes.append(f"Текст из файла «{filename}»:\n{extracted}")
            else:
                notes.append(
                    f"Владелец приложил PDF «{filename}», текст из него прочитать не удалось."
                )
        elif content_type in _TEXT_TYPES or filename.lower().endswith((".txt", ".md", ".csv")):
            notes.append(
                f"Текст из файла «{filename}»:\n{data.decode('utf-8', errors='replace')[:SITE_TEXT_CHARS]}"
            )
        else:
            raise BriefingError(
                f"{filename}: можно прикрепить фото, PDF или текстовый файл с прайсом"
            )
    return images, "\n\n".join(notes)
