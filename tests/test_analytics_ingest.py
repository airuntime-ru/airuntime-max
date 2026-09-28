"""Analytics ingest API tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_analytics_batch_accepts_events(client: TestClient) -> None:
    payload = {
        "session_id": uuid.uuid4().hex[:32],
        "anonymous_id": uuid.uuid4().hex[:32],
        "platform": "web",
        "events": [
            {"name": "session_start"},
            {"name": "screen_view", "screen": "app_home"},
        ],
    }
    resp = client.post("/api/v1/analytics/batch", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 2
