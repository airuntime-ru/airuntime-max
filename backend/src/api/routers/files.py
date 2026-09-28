import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.files import ChatFileResponse
from src.api.mappers.chat_files import chat_file_to_response
from src.db.models.chat import Chat
from src.db.models.chat_file import ChatFile
from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import get_db
from src.services.storage import storage_service

router = APIRouter(prefix="/projects/{project_id}/chats/{chat_id}/files", tags=["files"])


def _authorize_chat(
    db: Session, project_id: UUID, chat_id: UUID, current_user: User
) -> tuple[Project, Chat]:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    chat = db.get(Chat, chat_id)
    if not chat or chat.project_id != project_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return project, chat


def _to_response(row: ChatFile) -> ChatFileResponse:
    return chat_file_to_response(row)


@router.get("", response_model=list[ChatFileResponse])
def list_chat_files(
    project_id: UUID,
    chat_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChatFileResponse]:
    _authorize_chat(db, project_id, chat_id, current_user)
    rows = (
        db.query(ChatFile)
        .filter(ChatFile.chat_id == chat_id)
        .order_by(ChatFile.created_at.desc())
        .all()
    )
    return [_to_response(row) for row in rows]


@router.post("", response_model=ChatFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_chat_file(
    project_id: UUID,
    chat_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatFileResponse:
    project, _chat = _authorize_chat(db, project_id, chat_id, current_user)
    file_id = str(uuid.uuid4())
    object_key, content_type, size_bytes = await storage_service.upload_chat_file(
        upload=file,
        project_id=str(project.id),
        chat_id=str(chat_id),
        file_id=file_id,
    )
    row = ChatFile(
        id=uuid.UUID(file_id),
        project_id=project.id,
        chat_id=chat_id,
        user_id=current_user.id,
        object_key=object_key,
        original_filename=file.filename or "file",
        content_type=content_type,
        size_bytes=size_bytes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.delete("/{file_id}")
def delete_chat_file(
    project_id: UUID,
    chat_id: UUID,
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _authorize_chat(db, project_id, chat_id, current_user)
    row = db.get(ChatFile, file_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="File not found")
    if row.message_id is not None:
        raise HTTPException(status_code=400, detail="Cannot delete file linked to a message")
    storage_service.delete_object(row.object_key)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}
