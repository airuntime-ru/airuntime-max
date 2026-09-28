"""Three read-only, deterministic (no LLM) analysis skills: database_migrations,
dependency_health_check, project_structure_review. All three inspect the workspace on disk and
report findings - none of them modify files (that stays BuildFixer/Implementer's job, informed
by these findings).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.services.orchestration.schemas import (
    RetryPolicy,
    RiskLevel,
    SkillDefinition,
    SkillResult,
    SpecialistRole,
)
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.common import BaseSkill

_MIGRATION_DIR_PATTERNS = (
    "alembic/versions",
    "migrations",
    "prisma/migrations",
    "db/migrate",
)


class DatabaseMigrationsSkill(BaseSkill):
    """Deterministic presence/shape check for a migrations setup - does NOT execute migrations
    (that would mean running arbitrary commands inside the generated project's own runtime,
    which this platform has no sandboxed path for today; see the final report's honest
    limitations section). Reports what it found so BuildFixer/Implementer can act on it."""

    definition = SkillDefinition(
        id="database_migrations",
        version="1.0",
        title="Database migrations check",
        description="Detect whether the project has a migrations directory and report its apparent framework/state.",
        supported_roles=[
            SpecialistRole.SOLUTION_ARCHITECT,
            SpecialistRole.BUILD_FIXER,
            SpecialistRole.QA_REVIEWER,
        ],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={},
        output_schema={"found": "bool", "path": "string|null", "migration_count": "int"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        root = Path(context.workspace_root)
        for pattern in _MIGRATION_DIR_PATTERNS:
            candidate = root / pattern
            if candidate.is_dir():
                count = sum(1 for _ in candidate.glob("*") if _.is_file())
                return SkillResult(
                    status="completed",
                    summary=f"Found migrations directory at {pattern} ({count} file(s))",
                    output={"found": True, "path": pattern, "migration_count": count},
                )
        return SkillResult(
            status="completed",
            summary="No migrations directory found",
            output={"found": False, "path": None, "migration_count": 0},
        )


_MANIFEST_FILES = ("requirements.txt", "package.json")
_UNPINNED_MARKERS = ("*", ">=", "latest")


class DependencyHealthCheckSkill(BaseSkill):
    definition = SkillDefinition(
        id="dependency_health_check",
        version="1.0",
        title="Dependency health check",
        description="Scan requirements.txt/package.json for missing lockfiles and obviously unpinned/risky dependency versions.",
        supported_roles=[
            SpecialistRole.SOLUTION_ARCHITECT,
            SpecialistRole.BUILD_FIXER,
            SpecialistRole.SECURITY_REVIEWER,
        ],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={},
        output_schema={"findings": "list[string]"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        root = Path(context.workspace_root)
        findings: list[str] = []

        requirements = root / "requirements.txt"
        if requirements.exists():
            for line_no, line in enumerate(
                requirements.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "==" not in stripped and any(marker in stripped for marker in _UNPINNED_MARKERS):
                    findings.append(f"requirements.txt:{line_no} unpinned dependency: {stripped}")
                elif "==" not in stripped and not any(c in stripped for c in "<>="):
                    findings.append(f"requirements.txt:{line_no} no version pin: {stripped}")

        package_json = root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError):
                findings.append("package.json: could not parse as JSON")
                data = {}
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for name, version in deps.items():
                if isinstance(version, str) and version.strip() in {"*", "latest"}:
                    findings.append(
                        f"package.json: {name} pinned to {version!r} (unpredictable builds)"
                    )
            if (
                deps
                and not (root / "package-lock.json").exists()
                and not (root / "pnpm-lock.yaml").exists()
            ):
                findings.append(
                    "package.json present with no lockfile (package-lock.json/pnpm-lock.yaml)"
                )

        if not requirements.exists() and not package_json.exists():
            return SkillResult(
                status="completed", summary="No dependency manifest found", output={"findings": []}
            )

        return SkillResult(
            status="completed" if not findings else "partial",
            summary=f"{len(findings)} dependency finding(s)"
            if findings
            else "No dependency issues found",
            output={"findings": findings},
        )


class ProjectStructureReviewSkill(BaseSkill):
    """Reuses the same required-file contract agentic_artifacts.py already enforces at deploy
    time (WEBSITE_REQUIRED="public/index.html", TELEGRAM_REQUIRED="app.py") so a plan can check
    structural completeness *before* a build attempt, not just discover it from a failed one."""

    definition = SkillDefinition(
        id="project_structure_review",
        version="1.0",
        title="Project structure review",
        description="Check the workspace against the platform's required-file contract for the project's type.",
        supported_roles=[
            SpecialistRole.SOLUTION_ARCHITECT,
            SpecialistRole.QA_REVIEWER,
            SpecialistRole.SECURITY_REVIEWER,
        ],
        supported_project_types=["website", "telegram_bot", "mixed"],
        input_schema={},
        output_schema={"missing": "list[string]", "file_count": "int"},
        risk_level=RiskLevel.LOW,
        idempotent=True,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    async def execute(self, context: SkillContext) -> SkillResult:
        from src.services.agentic_artifacts import TELEGRAM_REQUIRED, WEBSITE_REQUIRED
        from src.services.workspace import list_workspace_files

        root = Path(context.workspace_root)
        required: list[str] = []
        if context.project_type in ("website", "mixed"):
            required.append(WEBSITE_REQUIRED)
        if context.project_type in ("telegram_bot", "mixed"):
            required.append(TELEGRAM_REQUIRED)

        missing = [path for path in required if not (root / path).exists()]
        files = list_workspace_files(root)

        return SkillResult(
            status="completed" if not missing else "partial",
            summary=f"Missing required files: {', '.join(missing)}"
            if missing
            else "All required files present",
            output={"missing": missing, "file_count": len(files)},
        )
