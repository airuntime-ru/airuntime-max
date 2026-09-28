import base64
import json
from dataclasses import dataclass
from io import BytesIO
from uuid import UUID

from sqlalchemy.orm import Session

from src.db.models.chat_file import ChatFile
from src.services.storage import storage_service

TEXTUAL_PREFIXES = ("text/",)
TEXTUAL_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
}

IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
PDF_CONTENT_TYPES = {"application/pdf"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ImageAttachment:
    filename: str
    content_type: str
    data_base64: str


def _is_textual(content_type: str, filename: str) -> bool:
    if content_type.startswith(TEXTUAL_PREFIXES):
        return True
    if content_type in TEXTUAL_TYPES:
        return True
    return filename.lower().endswith(
        (
            ".md",
            ".markdown",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".json",
            ".yaml",
            ".yml",
            ".csv",
            ".txt",
            ".html",
            ".css",
            ".xml",
        )
    )


def attach_files_to_message(
    db: Session, *, message_id: UUID, attachment_ids: list[UUID]
) -> list[ChatFile]:
    if not attachment_ids:
        return []
    rows = db.query(ChatFile).filter(ChatFile.id.in_(attachment_ids)).all()
    if len(rows) != len(attachment_ids):
        raise ValueError("One or more attachments were not found")
    for row in rows:
        if row.message_id is not None:
            raise ValueError("Attachment already linked to a message")
        row.message_id = message_id
    db.commit()
    return rows


def build_attachment_context(db: Session, attachment_ids: list[UUID], *, max_chars: int) -> str:
    if not attachment_ids:
        return ""
    rows = (
        db.query(ChatFile)
        .filter(ChatFile.id.in_(attachment_ids))
        .order_by(ChatFile.created_at.asc())
        .all()
    )
    if not rows:
        return ""

    parts: list[str] = []
    remaining = max_chars
    for row in rows:
        header = f"Attachment: {row.original_filename} ({row.content_type}, {row.size_bytes} bytes)"
        if _is_textual(row.content_type, row.original_filename):
            preview = storage_service.read_text_preview(
                row.object_key, max_chars=min(remaining, 8000)
            )
            if preview:
                block = f"{header}\n```\n{preview}\n```"
            else:
                block = f"{header}\n[binary or unreadable text content omitted]"
        elif row.content_type in IMAGE_CONTENT_TYPES:
            # Sent to the model as real image content (see extract_image_attachments) - just
            # note its presence here so the text transcript still makes sense on its own.
            block = f"{header}\n[image attached below]"
        elif row.content_type in PDF_CONTENT_TYPES:
            preview = extract_pdf_text(db, [row.id], max_chars=min(remaining, 8000))
            if preview:
                block = f"{header}\n```\n{preview}\n```"
            else:
                block = f"{header}\n[pdf attached — text could not be extracted]"
        else:
            block = f"{header}\n[non-text attachment omitted from model context]"
        parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    return "\n\n".join(parts)


def extract_pdf_text(db: Session, attachment_ids: list[UUID], *, max_chars: int = 8000) -> str:
    if not attachment_ids or max_chars <= 0:
        return ""
    rows = (
        db.query(ChatFile)
        .filter(
            ChatFile.id.in_(attachment_ids),
            ChatFile.content_type.in_(PDF_CONTENT_TYPES),
        )
        .order_by(ChatFile.created_at.asc())
        .all()
    )
    if not rows:
        return ""

    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    parts: list[str] = []
    remaining = max_chars
    for row in rows:
        if row.size_bytes > MAX_PDF_BYTES:
            continue
        data = storage_service.read_bytes(row.object_key, max_bytes=MAX_PDF_BYTES)
        if not data:
            continue
        try:
            reader = PdfReader(BytesIO(data))
            chunks: list[str] = []
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    chunks.append(text)
                if sum(len(c) for c in chunks) >= remaining:
                    break
            body = "\n\n".join(chunks).strip()
        except Exception:
            body = ""
        if not body:
            continue
        header = f"PDF: {row.original_filename}"
        block = f"{header}\n{body[:remaining]}"
        parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    return "\n\n".join(parts)


def load_run_attachment_ids(metadata_json: str | None) -> list[UUID]:
    if not metadata_json:
        return []
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("attachment_ids") or []
    ids: list[UUID] = []
    for item in raw:
        try:
            ids.append(UUID(str(item)))
        except (ValueError, TypeError):
            continue
    return ids


def extract_image_attachments(db: Session, attachment_ids: list[UUID]) -> list[ImageAttachment]:
    if not attachment_ids:
        return []
    rows = (
        db.query(ChatFile)
        .filter(ChatFile.id.in_(attachment_ids), ChatFile.content_type.in_(IMAGE_CONTENT_TYPES))
        .order_by(ChatFile.created_at.asc())
        .all()
    )
    images: list[ImageAttachment] = []
    for row in rows:
        if row.size_bytes > MAX_IMAGE_BYTES:
            continue
        data = storage_service.read_bytes(row.object_key, max_bytes=MAX_IMAGE_BYTES)
        if not data:
            continue
        images.append(
            ImageAttachment(
                filename=row.original_filename,
                content_type=row.content_type,
                data_base64=base64.b64encode(data).decode("ascii"),
            )
        )
    return images


def serialize_message_metadata(attachment_ids: list[UUID]) -> str:
    return json.dumps({"attachment_ids": [str(item) for item in attachment_ids]})
