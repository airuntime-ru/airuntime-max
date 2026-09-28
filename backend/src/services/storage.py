import mimetypes
import re
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status

from src.core.config import settings

ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/css",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/pdf",
    "application/octet-stream",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/svg+xml",
}

ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".css",
    ".yaml",
    ".yml",
    ".xml",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
}


def _sanitize_filename(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")
    return cleaned or "file"


def _guess_content_type(filename: str, provided: str | None) -> str:
    if provided and provided != "application/octet-stream":
        return provided
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


@lru_cache
def _internal_client():
    kwargs = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **kwargs)


@lru_cache
def _public_client():
    kwargs = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


class ObjectStorageService:
    def ensure_bucket(self) -> None:
        client = _internal_client()
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
        except ClientError:
            client.create_bucket(Bucket=settings.s3_bucket)

    def validate_upload(self, *, filename: str, content_type: str | None, size_bytes: int) -> str:
        if size_bytes <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
        if size_bytes > settings.s3_max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {settings.s3_max_upload_bytes} bytes",
            )
        safe_name = _sanitize_filename(filename)
        extension = "." + safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        resolved_type = _guess_content_type(safe_name, content_type)
        if resolved_type not in ALLOWED_CONTENT_TYPES and extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type"
            )
        return resolved_type

    def build_object_key(
        self, *, project_id: str, chat_id: str, file_id: str, filename: str
    ) -> str:
        safe_name = _sanitize_filename(filename)
        return f"projects/{project_id}/chats/{chat_id}/{file_id}/{safe_name}"

    async def upload_chat_file(
        self,
        *,
        upload: UploadFile,
        project_id: str,
        chat_id: str,
        file_id: str,
    ) -> tuple[str, str, int]:
        body = await upload.read()
        size_bytes = len(body)
        filename = upload.filename or "file"
        content_type = self.validate_upload(
            filename=filename,
            content_type=upload.content_type,
            size_bytes=size_bytes,
        )
        object_key = self.build_object_key(
            project_id=project_id,
            chat_id=chat_id,
            file_id=file_id,
            filename=filename,
        )
        self.ensure_bucket()
        _internal_client().put_object(
            Bucket=settings.s3_bucket,
            Key=object_key,
            Body=body,
            ContentType=content_type,
        )
        return object_key, content_type, size_bytes

    def get_presigned_download_url(self, object_key: str) -> str:
        return _public_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": object_key},
            ExpiresIn=settings.s3_presign_expire_seconds,
        )

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes | None:
        try:
            response = _internal_client().get_object(Bucket=settings.s3_bucket, Key=object_key)
            return response["Body"].read(max_bytes + 1)[:max_bytes]
        except ClientError:
            return None

    def read_text_preview(self, object_key: str, *, max_chars: int) -> str | None:
        try:
            response = _internal_client().get_object(Bucket=settings.s3_bucket, Key=object_key)
            raw = response["Body"].read(max_chars + 1)
        except ClientError:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if len(text) > max_chars:
            return text[:max_chars] + "\n...[truncated]"
        return text

    def delete_object(self, object_key: str) -> None:
        _internal_client().delete_object(Bucket=settings.s3_bucket, Key=object_key)


storage_service = ObjectStorageService()
