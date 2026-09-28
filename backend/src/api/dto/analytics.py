"""Analytics ingest schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Platform = Literal["web", "telegram", "ios", "android", "server"]


class AnalyticsEventIn(BaseModel):
    name: str = Field(..., max_length=64)
    screen: str | None = Field(default=None, max_length=64)
    tab: str | None = Field(default=None, max_length=32)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    ts: datetime | None = None
    props: dict[str, Any] | None = None


class AnalyticsBatchIn(BaseModel):
    session_id: str = Field(..., max_length=64)
    anonymous_id: str = Field(..., min_length=8, max_length=64)
    platform: Platform
    user_id: str | None = Field(default=None, max_length=64)
    entry_screen: str | None = Field(default=None, max_length=64)
    referrer: str | None = Field(default=None, max_length=2048)
    utm_source: str | None = Field(default=None, max_length=128)
    utm_medium: str | None = Field(default=None, max_length=128)
    utm_campaign: str | None = Field(default=None, max_length=128)
    events: list[AnalyticsEventIn] = Field(..., min_length=1, max_length=50)


class AnalyticsBatchOut(BaseModel):
    ok: bool = True
    accepted: int
