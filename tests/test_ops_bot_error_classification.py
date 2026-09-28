from __future__ import annotations

import pytest
from ops_bot.monitors import is_error_log_line


@pytest.mark.parametrize("action_id", ["x", "0", 'bad\\"id'])
def test_malformed_next_action_request_does_not_trigger_frontend_error_alert(
    action_id: str,
) -> None:
    line = (
        f'Error: The Server Reference ID did not match the expected format. Received "{action_id}".'
    )
    assert not is_error_log_line(line, "airuntime-frontend-1")
    assert not is_error_log_line(line, "airuntime-frontend-2")
    assert is_error_log_line(line, "airuntime-backend-1")


@pytest.mark.parametrize(
    "line",
    [
        "Error: database unavailable",
        'Error: The Server Reference ID did not match the expected format. Received "x". Further failure',
        '{"level":"error","message":"database unavailable"}',
    ],
)
def test_real_frontend_errors_still_trigger_alert(line: str) -> None:
    assert is_error_log_line(line, "airuntime-frontend-1")
