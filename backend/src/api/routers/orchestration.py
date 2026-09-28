"""Standalone REST + SSE surface for the persistent orchestration engine (services/orchestration/
engine.py), independent of chat.py's own turn-shaped `/stream` endpoint (see that router for the
path a normal chat message actually takes through the engine).

Run creation returns as soon as the OrchestrationRun row exists - the engine itself runs as a
background asyncio task on this process, not tied to the HTTP request's lifetime, so a client
disconnecting (or never connecting to /events in the first place) never aborts a run. Reconnect/
resume-after-reload is what GET /events (events_bus.stream_events's replay-then-tail) is for.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.api.dependencies.auth import get_current_user
from src.api.dto.orchestration import (
    OrchestrationRunCreateRequest,
    OrchestrationRunDetailResponse,
    OrchestrationRunListResponse,
    OrchestrationRunResponse,
    OrchestrationTaskResponse,
    RunEventHistoryResponse,
    RunEventResponse,
)
from src.core.config import settings
from src.db.models.chat import Chat
from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import SessionLocal, get_db
from src.services.byok import resolve_turn_api_key, resolve_user_api_key
from src.services.credit_gate import out_of_credits_detail
from src.services.model_access import ModelNotAllowedError, resolve_model_for_user
from src.services.orchestration import engine, events_bus
from src.services.orchestration.repository import (
    AgentTaskRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
    RunEventRepository,
)
from src.services.orchestration.status import is_run_terminal
from src.services.prompt_guard import sanitize_user_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/orchestration", tags=["orchestration"])


def _get_owned_project(project_id: UUID, current_user: User, db: Session) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == current_user.id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_owned_run(project: Project, run_id: UUID, db: Session):
    run = OrchestrationRunRepository(db).get(run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _task_response(task) -> OrchestrationTaskResponse:
    # Not a plain model_validate(task, from_attributes=True): AgentTask has no `dependencies`
    # attribute at all (only depends_on_json, a JSON-stringified list of local_ids) - Pydantic's
    # attribute-matching would silently fall back to the field's default ([]) rather than error,
    # which would make every task look dependency-free instead of surfacing the real DAG.
    return OrchestrationTaskResponse(
        id=task.id,
        local_id=task.local_id,
        title=task.title,
        role=task.role,
        execution_kind=task.execution_kind,
        status=task.status,
        attempt=task.attempt,
        max_attempts=task.max_attempts,
        sequence=task.sequence,
        workspace_mode=task.workspace_mode,
        dependencies=json.loads(task.depends_on_json or "[]"),
        error_code=task.error_code,
        error_message=task.error_message,
    )


def _run_detail_response(db: Session, run) -> OrchestrationRunDetailResponse:
    plan = OrchestrationPlanRepository(db).get_active(run.id)
    tasks = AgentTaskRepository(db).list_by_plan(plan.id) if plan is not None else []
    return OrchestrationRunDetailResponse(
        **OrchestrationRunResponse.model_validate(run).model_dump(),
        tasks=[_task_response(t) for t in sorted(tasks, key=lambda t: t.sequence)],
    )


@router.post("/runs", response_model=OrchestrationRunResponse)
async def create_run(
    project_id: UUID,
    payload: OrchestrationRunCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrchestrationRunResponse:
    # Async (not a plain `def`) so FastAPI runs this directly on the server's event loop rather
    # than in a worker thread (its default for sync endpoints) - engine.launch_run_in_background's
    # asyncio.create_task() below requires a *running* loop in the calling thread, and a
    # threadpool worker thread has none.
    project = _get_owned_project(project_id, current_user, db)
    chat = db.query(Chat).filter(Chat.id == payload.chat_id, Chat.project_id == project.id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if current_user.credits_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=out_of_credits_detail(db, current_user),
        )

    try:
        content = sanitize_user_message(payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        provider_name, model = resolve_model_for_user(
            db,
            current_user,
            provider_override=payload.provider,
            model_override=payload.model,
            has_own_key=bool(
                resolve_user_api_key(
                    db, current_user, (payload.provider or settings.provider_name).strip().lower()
                )
            ),
        )
    except ModelNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # BYOK first: the user's own key means the provider bills them, not us.
    api_key = resolve_turn_api_key(db, current_user, provider_name) or ""

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No AI provider API key configured"
        )

    run = OrchestrationRunRepository(db).create(
        project_id=project.id,
        chat_id=chat.id,
        user_id=current_user.id,
        original_request=content,
        provider=provider_name,
        model=model,
    )
    db.commit()
    db.refresh(run)

    engine.launch_run_in_background(
        run.id, provider_name=provider_name, model=model, api_key=api_key
    )
    return OrchestrationRunResponse.model_validate(run)


@router.get("/runs", response_model=OrchestrationRunListResponse)
def list_runs(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrchestrationRunListResponse:
    project = _get_owned_project(project_id, current_user, db)
    from src.db.models.orchestration_run import OrchestrationRun

    base_query = db.query(OrchestrationRun).filter(OrchestrationRun.project_id == project.id)
    total = base_query.count()
    rows = base_query.order_by(OrchestrationRun.created_at.desc()).offset(offset).limit(limit).all()
    return OrchestrationRunListResponse(
        items=[OrchestrationRunResponse.model_validate(row) for row in rows], total=total
    )


@router.get("/runs/{run_id}", response_model=OrchestrationRunDetailResponse)
def get_run(
    project_id: UUID,
    run_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrchestrationRunDetailResponse:
    project = _get_owned_project(project_id, current_user, db)
    run = _get_owned_run(project, run_id, db)
    return _run_detail_response(db, run)


@router.get("/runs/{run_id}/events/history", response_model=RunEventHistoryResponse)
def list_run_event_history(
    project_id: UUID,
    run_id: UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RunEventHistoryResponse:
    project = _get_owned_project(project_id, current_user, db)
    _get_owned_run(project, run_id, db)
    rows, total = RunEventRepository(db).list_paginated(run_id, offset=offset, limit=limit)
    return RunEventHistoryResponse(
        items=[
            RunEventResponse(
                seq=row.seq,
                event_type=row.event_type,
                payload=json.loads(row.payload_json),
                task_id=row.task_id,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    project_id: UUID,
    run_id: UUID,
    after_seq: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    project = _get_owned_project(project_id, current_user, db)
    _get_owned_run(project, run_id, db)  # 404s before opening the stream if not owned/found

    async def _source() -> AsyncIterator[str]:
        async for envelope in events_bus.stream_events(
            SessionLocal, str(run_id), after_seq=after_seq
        ):
            yield f"data: {json.dumps(envelope, default=str)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_source(), media_type="text/event-stream")


@router.post("/runs/{run_id}/cancel", response_model=OrchestrationRunResponse)
def cancel_run(
    project_id: UUID,
    run_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrchestrationRunResponse:
    project = _get_owned_project(project_id, current_user, db)
    run = _get_owned_run(project, run_id, db)
    if is_run_terminal(run.status):
        return OrchestrationRunResponse.model_validate(run)

    OrchestrationRunRepository(db).request_cancel(run)
    db.commit()
    # Best-effort immediate signal for the common case (this replica is the one running it) -
    # the persisted cancel_requested flag above is the authoritative, cross-process fallback the
    # engine's own loop polls every iteration regardless of whether this in-process token exists.
    token = engine.get_cancellation_token(run_id)
    if token is not None:
        token.cancel("cancelled via API")
    return OrchestrationRunResponse.model_validate(run)


@router.post("/runs/{run_id}/resume", response_model=OrchestrationRunResponse)
async def resume_run(
    project_id: UUID,
    run_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrchestrationRunResponse:
    """For a run parked at waiting_for_user - either because a task requested a
    secret/service/decision, or because the run's budget was exhausted (error_code
    "budget_exceeded"). Call once whatever was missing has actually been supplied (secret filled
    in, credits topped up). Un-blocks the affected tasks under the active plan and re-launches the
    engine as a fresh background task; a run not currently waiting is a no-op.

    Async for the same reason as create_run: engine.launch_run_in_background's
    asyncio.create_task() needs a running event loop in the calling thread."""
    project = _get_owned_project(project_id, current_user, db)
    run = _get_owned_run(project, run_id, db)
    if run.status != "waiting_for_user":
        return OrchestrationRunResponse.model_validate(run)

    # Must happen before relaunching: a run parked on budget would otherwise immediately re-park,
    # since the engine reseeds its BudgetTracker from the same persisted credits_used/credit_budget.
    engine.prepare_run_for_resume(db, run)

    plan = OrchestrationPlanRepository(db).get_active(run.id)
    if plan is not None:
        task_repo = AgentTaskRepository(db)
        for task in task_repo.list_by_plan(plan.id):
            if task.status == "waiting_for_user":
                engine.resume_task_after_user_input(db, task.id)
    db.commit()

    provider_name = run.provider or settings.provider_name
    model = run.model or ""
    # BYOK first: the user's own key means the provider bills them, not us.
    api_key = resolve_turn_api_key(db, current_user, provider_name) or ""

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No AI provider API key configured"
        )
    engine.launch_run_in_background(
        run.id, provider_name=provider_name, model=model, api_key=api_key
    )
    return OrchestrationRunResponse.model_validate(run)
