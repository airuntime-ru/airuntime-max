from src.api.dto.files import ChatFileResponse
from src.db.models.chat_file import ChatFile
from src.services.storage import storage_service


def chat_file_to_response(row: ChatFile) -> ChatFileResponse:
    return ChatFileResponse(
        id=row.id,
        project_id=row.project_id,
        chat_id=row.chat_id,
        message_id=row.message_id,
        original_filename=row.original_filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        download_url=storage_service.get_presigned_download_url(row.object_key),
        created_at=row.created_at,
    )
