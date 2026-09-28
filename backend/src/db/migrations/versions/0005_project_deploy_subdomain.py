"""project deploy subdomain

Revision ID: 0005_project_deploy_subdomain
Revises: 0004_user_onboarding
Create Date: 2026-07-07 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_project_deploy_subdomain"
down_revision: str | None = "0004_user_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("deploy_subdomain", sa.String(length=63), nullable=True))
    op.create_index("ix_projects_deploy_subdomain", "projects", ["deploy_subdomain"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_projects_deploy_subdomain", table_name="projects")
    op.drop_column("projects", "deploy_subdomain")
