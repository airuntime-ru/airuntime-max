from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.deployment import DeploymentResponse
from src.api.dto.project_versions import (
    ProjectRollbackResponse,
    ProjectVersionFileResponse,
    ProjectVersionResponse,
    ProjectVersionTreeEntryResponse,
)
from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import get_db
from src.services.deployments import create_deployment_for_project
from src.services.project_git import (
    ProjectGitError,
    archive_version_stream,
    list_version_tree,
    list_versions,
    project_repo_dir,
    read_version_file,
    rollback_to,
)
from src.services.project_runtime import RunningProjectLimitError

router = APIRouter(prefix="/projects/{project_id}/versions", tags=["versions"])


def _authorize_project(db: Session, project_id: UUID, current_user: User) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectVersionResponse])
def list_project_versions(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectVersionResponse]:
    project = _authorize_project(db, project_id, current_user)
    repo_dir = project_repo_dir(project.id)
    versions = list_versions(repo_dir)
    return [
        ProjectVersionResponse(
            commit_hash=v.commit_hash, created_at=v.created_at, message=v.message
        )
        for v in versions
    ]


@router.get("/{commit_hash}/archive")
def download_project_version_archive(
    project_id: UUID,
    commit_hash: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    project = _authorize_project(db, project_id, current_user)
    repo_dir = project_repo_dir(project.id)
    try:
        archive_bytes = archive_version_stream(repo_dir, commit_hash=commit_hash)
    except ProjectGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"project-{str(project.id)[:8]}-{commit_hash[:8]}.zip"
    return StreamingResponse(
        iter([archive_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{commit_hash}/rollback", response_model=ProjectRollbackResponse)
def rollback_project_version(
    project_id: UUID,
    commit_hash: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRollbackResponse:
    project = _authorize_project(db, project_id, current_user)
    repo_dir = project_repo_dir(project.id)

    # Create rollback commit and queue deployment using the current repo state.
    try:
        new_head = rollback_to(
            repo_dir, commit_hash=commit_hash, message=f"Rollback to {commit_hash[:8]}"
        )
    except ProjectGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        deployment = create_deployment_for_project(db, project)
    except RunningProjectLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # `create_deployment_for_project` returns ORM model; deployment DTO is created by FastAPI.
    return ProjectRollbackResponse(
        rollback_commit_hash=new_head,
        deployment=DeploymentResponse.model_validate(deployment),
    )


@router.get("/{commit_hash}/tree", response_model=list[ProjectVersionTreeEntryResponse])
def list_project_version_tree(
    project_id: UUID,
    commit_hash: str,
    path: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectVersionTreeEntryResponse]:
    project = _authorize_project(db, project_id, current_user)
    repo_dir = project_repo_dir(project.id)
    try:
        entries = list_version_tree(repo_dir, commit_hash=commit_hash, rel_path=path)
    except ProjectGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path and project.type == "telegram_bot" and any(e.name == "app.py" for e in entries):
        # Older fallback generations could leave website files in the same repo
        # when a project changed type. Hide that stale website folder for bot projects.
        entries = [e for e in entries if e.name != "public"]
    return [
        ProjectVersionTreeEntryResponse(
            name=e.name, entry_type=e.entry_type, size_bytes=e.size_bytes
        )
        for e in entries
    ]


@router.get("/{commit_hash}/file", response_model=ProjectVersionFileResponse)
def get_project_version_file(
    project_id: UUID,
    commit_hash: str,
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectVersionFileResponse:
    project = _authorize_project(db, project_id, current_user)
    repo_dir = project_repo_dir(project.id)
    try:
        payload = read_version_file(repo_dir, commit_hash=commit_hash, rel_path=path)
    except ProjectGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectVersionFileResponse(path=path, **payload)
