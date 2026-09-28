"""Tests for services/deployments.py's log-capture helpers - plain object mutation, no DB
session needed (neither function queries or commits anything itself)."""

from __future__ import annotations

import uuid

from src.db.models.deployment import Deployment
from src.services.deployments import append_deployment_log, store_deployment_error


def _deployment() -> Deployment:
    return Deployment(project_id=uuid.uuid4())


class TestStoreDeploymentError:
    def test_secret_shaped_value_is_redacted_from_error_text(self) -> None:
        deployment = _deployment()
        store_deployment_error(deployment, "Build failed: STRIPE_API_KEY=sk_live_abc123 invalid")
        assert "sk_live_abc123" not in deployment.error_text
        assert "[REDACTED]" in deployment.error_text

    def test_connection_url_credentials_are_redacted(self) -> None:
        deployment = _deployment()
        store_deployment_error(
            deployment, "connection refused: postgres://user:hunter2@db:5432/app"
        )
        assert "hunter2" not in deployment.error_text
        assert "postgres://user:[REDACTED]@db:5432/app" in deployment.error_text

    def test_redacted_error_is_also_appended_to_the_live_log(self) -> None:
        deployment = _deployment()
        store_deployment_error(deployment, "token=abc123supersecret failed")
        assert "abc123supersecret" not in deployment.log_text
        assert "[REDACTED]" in deployment.log_text

    def test_empty_text_clears_error_fields(self) -> None:
        deployment = _deployment()
        store_deployment_error(deployment, "")
        assert deployment.error_text is None


class TestAppendDeploymentLog:
    def test_secret_shaped_value_is_redacted_before_appending(self) -> None:
        deployment = _deployment()
        append_deployment_log(deployment, "env dump: password=hunters_actual_password\n")
        assert "hunters_actual_password" not in deployment.log_text
        assert "[REDACTED]" in deployment.log_text

    def test_plain_text_without_secrets_passes_through_unchanged(self) -> None:
        deployment = _deployment()
        append_deployment_log(deployment, "Step 3/8 : COPY . .\n")
        assert deployment.log_text == "Step 3/8 : COPY . .\n"

    def test_appends_to_existing_log_rather_than_overwriting(self) -> None:
        deployment = _deployment()
        append_deployment_log(deployment, "first\n")
        append_deployment_log(deployment, "second\n")
        assert deployment.log_text == "first\nsecond\n"

    def test_empty_chunk_is_a_noop(self) -> None:
        deployment = _deployment()
        append_deployment_log(deployment, "existing\n")
        append_deployment_log(deployment, "")
        assert deployment.log_text == "existing\n"
