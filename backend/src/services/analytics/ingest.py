"""Product analytics batch ingest."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.api.dto.analytics import AnalyticsBatchIn
from src.db.models.analytics import AnalyticsEvent, AnalyticsSession


def ingest_batch(db: Session, payload: AnalyticsBatchIn) -> int:
    now = datetime.now(UTC)
    session = db.get(AnalyticsSession, payload.session_id)
    if session is None:
        session = AnalyticsSession(
            id=payload.session_id,
            anonymous_id=payload.anonymous_id,
            user_id=payload.user_id,
            platform=payload.platform,
            entry_screen=payload.entry_screen,
            referrer=payload.referrer,
            utm_source=payload.utm_source,
            utm_medium=payload.utm_medium,
            utm_campaign=payload.utm_campaign,
            started_at=now,
            last_seen_at=now,
        )
        db.add(session)
    else:
        session.last_seen_at = now
        if payload.user_id:
            session.user_id = payload.user_id

    accepted = 0
    for item in payload.events:
        occurred_at = item.ts.astimezone(UTC) if item.ts else now
        db.add(
            AnalyticsEvent(
                id=str(uuid.uuid4()),
                session_id=payload.session_id,
                anonymous_id=payload.anonymous_id,
                user_id=payload.user_id,
                name=item.name[:64],
                screen=(item.screen[:64] if item.screen else None),
                tab=(item.tab[:32] if item.tab else None),
                platform=payload.platform,
                duration_ms=item.duration_ms,
                props=item.props,
                occurred_at=occurred_at,
            )
        )
        accepted += 1
        if occurred_at > session.last_seen_at:
            session.last_seen_at = occurred_at

    db.commit()
    return accepted
