import uuid

import pytest

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.secret import Secret
from src.db.models.user import User
from src.services.artifacts import (
    ArtifactError,
    _all_secret_environment,
    _telegram_token,
    ensure_project_artifact,
)
from src.services.secrets import encrypt_secret


def test_ensure_project_artifact_requires_agent_dockerfile(tmp_path, monkeypatch, db):
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))
    project = Project(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        type="website",
        name="Demo",
        description="Landing",
    )

    with pytest.raises(ArtifactError, match="нет кода агента"):
        ensure_project_artifact(db, project, "Build a site")


def test_telegram_bot_requires_token_secret(tmp_path, monkeypatch, db):
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))
    project = Project(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        type="telegram_bot",
        name="Support Bot",
        description="Answers customer questions",
    )
    root = tmp_path / str(project.id)
    root.mkdir(parents=True)
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="TELEGRAM_BOT_TOKEN"):
        ensure_project_artifact(db, project, "Launch the bot")


def test_telegram_token_secret_lookup_accepts_human_key_names(db):
    user = User(
        id=uuid.uuid4(),
        email="secret-lookup@airuntime.dev",
        password_hash=None,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        type="telegram_bot",
        name="Support Bot",
        description="Answers customer questions",
    )
    db.add(project)
    db.flush()
    db.add(
        Secret(
            project_id=project.id,
            key="Telegram Bot Token",
            encrypted_value=encrypt_secret("123:abc"),
        )
    )
    db.flush()

    assert _telegram_token(db, project) == "123:abc"


def test_all_secret_environment_includes_every_filled_secret_not_just_telegram(db):
    # Regression test: previously only TELEGRAM_BOT_TOKEN ever reached a deployed container's
    # environment - any other secret an agent requested (request_secret) and the user filled in
    # via Settings was collected but never actually delivered.
    user = User(
        id=uuid.uuid4(), email="secret-env@airuntime.dev", password_hash=None, is_verified=True
    )
    db.add(user)
    db.flush()
    project = Project(id=uuid.uuid4(), user_id=user.id, type="mixed", name="Shop", description="")
    db.add(project)
    db.flush()
    db.add_all(
        [
            Secret(
                project_id=project.id,
                key="TELEGRAM_BOT_TOKEN",
                encrypted_value=encrypt_secret("123:abc"),
            ),
            Secret(
                project_id=project.id,
                key="STRIPE_API_KEY",
                encrypted_value=encrypt_secret("sk_live_x"),
            ),
            Secret(project_id=project.id, key="UNFILLED_KEY", encrypted_value=None),
        ]
    )
    db.flush()

    env = _all_secret_environment(db, project)

    assert env == {"TELEGRAM_BOT_TOKEN": "123:abc", "STRIPE_API_KEY": "sk_live_x"}


def test_ensure_project_artifact_accepts_existing_agent_files(tmp_path, monkeypatch, db):
    monkeypatch.setattr(settings, "generated_projects_dir", str(tmp_path))
    project = Project(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        type="website",
        name="Demo",
        description="Landing",
    )
    root = tmp_path / str(project.id)
    root.mkdir(parents=True)
    (root / "Dockerfile").write_text(
        "FROM nginx:1.27-alpine\nCOPY public/ /usr/share/nginx/html/\n",
        encoding="utf-8",
    )
    path = ensure_project_artifact(db, project)
    assert path == root
