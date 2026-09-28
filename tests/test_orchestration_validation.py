"""Tests for services/orchestration/validation.py - pure functions over TaskContract/
TaskEvidence/TaskResult, no DB/LLM needed."""

from __future__ import annotations

import uuid

from src.services.orchestration import validation as v
from src.services.orchestration.schemas import (
    AcceptanceCriterion,
    ClaimedAcceptanceResult,
    ProjectStateSummary,
    SpecialistRole,
    TaskBudget,
    TaskContract,
    TaskEvidence,
    TaskResult,
    ValidationStep,
)


def _contract(
    *,
    role: SpecialistRole = SpecialistRole.IMPLEMENTER,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    acceptance_criteria: list[AcceptanceCriterion] | None = None,
    validation_steps: list[ValidationStep] | None = None,
) -> TaskContract:
    return TaskContract(
        task_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role=role,
        project_goal="g",
        user_value="u",
        task_goal="t",
        reason="r",
        current_state=ProjectStateSummary(project_type="website", project_name="p"),
        allowed_paths=allowed_paths or ["public/"],
        forbidden_paths=forbidden_paths or [".env", ".git"],
        acceptance_criteria=acceptance_criteria or [],
        validation_steps=validation_steps or [],
        budget=TaskBudget(),
    )


def _evidence(**overrides) -> TaskEvidence:  # noqa: ANN003
    return TaskEvidence(**overrides)


class TestPathMatchesAny:
    def test_env_does_not_match_env_example(self) -> None:
        assert v.path_matches_any(".env", [".env"])
        assert v.path_matches_any("config/.env", [".env"])
        assert not v.path_matches_any(".env.example", [".env"])
        assert not v.path_matches_any(".gitignore", [".git"])
        assert v.path_matches_any(".git/config", [".git"])
        assert v.path_matches_any("public/index.html", ["public"])


class TestValidateScope:
    def test_changes_within_allowed_paths_pass(self) -> None:
        contract = _contract(allowed_paths=["public/"])
        evidence = _evidence(changed_files=["public/index.html"])
        findings = v.validate_scope(contract, evidence)
        assert all(f.passed for f in findings)

    def test_change_outside_allowed_paths_flagged(self) -> None:
        contract = _contract(allowed_paths=["public/"])
        evidence = _evidence(changed_files=["backend/main.py"])
        findings = v.validate_scope(contract, evidence)
        assert any(not f.passed and "outside allowed_paths" in f.message for f in findings)

    def test_forbidden_path_touch_is_critical(self) -> None:
        contract = _contract(allowed_paths=["*"], forbidden_paths=[".env"])
        evidence = _evidence(changed_files=[".env"])
        findings = v.validate_scope(contract, evidence)
        critical = [f for f in findings if not f.passed and f.severity.value == "critical"]
        assert critical

    def test_env_example_is_not_the_forbidden_env_file(self) -> None:
        contract = _contract(allowed_paths=["*"], forbidden_paths=[".env", ".git"])
        evidence = _evidence(changed_files=[".env.example", ".gitignore"])
        findings = v.validate_scope(contract, evidence)
        assert all(f.passed for f in findings)

    def test_nested_env_file_is_still_forbidden(self) -> None:
        contract = _contract(allowed_paths=["*"], forbidden_paths=[".env"])
        evidence = _evidence(changed_files=["config/.env"])
        findings = v.validate_scope(contract, evidence)
        assert any(not f.passed and "forbidden" in f.message for f in findings)

    def test_readonly_role_git_diff_does_not_fail(self) -> None:
        contract = _contract(
            role=SpecialistRole.QA_REVIEWER, allowed_paths=[], forbidden_paths=["*"]
        )
        evidence = _evidence(changed_files=["public/index.html"])
        findings = v.validate_scope(contract, evidence)
        assert all(f.passed for f in findings)

    def test_readonly_role_no_changes_passes(self) -> None:
        contract = _contract(
            role=SpecialistRole.QA_REVIEWER, allowed_paths=[], forbidden_paths=["*"]
        )
        evidence = _evidence()
        findings = v.validate_scope(contract, evidence)
        assert all(f.passed for f in findings)

    def test_secret_scan_finding_fails_scope(self) -> None:
        contract = _contract()
        evidence = _evidence(
            changed_files=["public/config.js"], secret_scan_findings=["line 3 matches"]
        )
        findings = v.validate_scope(contract, evidence)
        assert any(not f.passed and "credential-shaped" in f.message for f in findings)

    def test_unauthorized_docker_compose_flagged(self) -> None:
        contract = _contract(allowed_paths=["*"])
        evidence = _evidence(created_files=["docker-compose.yml"])
        findings = v.validate_scope(contract, evidence)
        assert any(not f.passed and "docker-compose" in f.message for f in findings)


class TestValidateStaticBuildPreviewRuntime:
    def test_static_soft_passes_when_absent(self) -> None:
        assert v.validate_static(_evidence()).passed is True

    def test_static_fails_on_failed_test_result(self) -> None:
        finding = v.validate_static(_evidence(test_result={"ok": False}))
        assert finding.passed is False

    def test_build_fails_when_declared_but_not_attempted(self) -> None:
        # validate_build() is only ever invoked (by run_validation) once "build" is already
        # known to be a declared validation step, so missing evidence there must fail, not
        # soft-pass - see test_required_build_step_missing_evidence_blocks_acceptance below
        # for the same invariant exercised through the public run_validation() entry point.
        assert v.validate_build(_evidence()).passed is False

    def test_build_fails_on_failed_result(self) -> None:
        assert v.validate_build(_evidence(build_result={"ok": False})).passed is False

    def test_preview_required_but_missing_fails(self) -> None:
        assert v.validate_preview(_evidence()).passed is False

    def test_preview_passed_status(self) -> None:
        assert v.validate_preview(_evidence(preview_result={"status": "passed"})).passed is True

    def test_preview_issues_found_only_external_font_cdn_passes(self) -> None:
        preview = {
            "status": "issues_found",
            "fatal_errors": [],
            "pages": [
                {
                    "url": "http://preview/",
                    "console_errors": [],
                    "network_errors": [
                        "GET https://fonts.googleapis.com/css2?family=Inter - net::ERR_NAME_NOT_RESOLVED"
                    ],
                    "overflow_elements": [],
                    "broken_images": [],
                }
            ],
        }
        assert v.validate_preview(_evidence(preview_result=preview)).passed is True

    def test_preview_issues_found_with_console_errors_fails(self) -> None:
        preview = {
            "status": "issues_found",
            "fatal_errors": [],
            "pages": [
                {
                    "console_errors": ["Uncaught TypeError"],
                    "network_errors": [
                        "GET https://fonts.googleapis.com/css2?family=Inter - net::ERR_NAME_NOT_RESOLVED"
                    ],
                    "overflow_elements": [],
                    "broken_images": [],
                }
            ],
        }
        assert v.validate_preview(_evidence(preview_result=preview)).passed is False

    def test_runtime_required_but_missing_fails(self) -> None:
        assert v.validate_runtime(_evidence()).passed is False

    def test_runtime_ok_flag(self) -> None:
        assert v.validate_runtime(_evidence(runtime_health_result={"ok": True})).passed is True


class TestValidateAcceptanceCriteria:
    def test_build_criterion_verified_from_evidence(self) -> None:
        contract = _contract(
            acceptance_criteria=[
                AcceptanceCriterion(id="ac1", description="builds", verification_method="build")
            ]
        )
        outcomes = v.validate_acceptance_criteria(
            contract, None, _evidence(build_result={"ok": True})
        )
        assert outcomes[0].status == "passed"

    def test_build_criterion_failed_from_evidence(self) -> None:
        contract = _contract(
            acceptance_criteria=[
                AcceptanceCriterion(id="ac1", description="builds", verification_method="build")
            ]
        )
        outcomes = v.validate_acceptance_criteria(
            contract, None, _evidence(build_result={"ok": False})
        )
        assert outcomes[0].status == "failed"

    def test_manual_criterion_never_auto_passes(self) -> None:
        contract = _contract(
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="ac1", description="looks nice", verification_method="manual"
                )
            ]
        )
        claimed_result = TaskResult(
            status="completed",
            summary="done",
            acceptance_results=[ClaimedAcceptanceResult(criterion_id="ac1", status="passed")],
        )
        outcomes = v.validate_acceptance_criteria(contract, claimed_result, _evidence())
        assert outcomes[0].status == "unknown", (
            "a claim alone must never resolve a manual criterion to passed"
        )

    def test_criterion_unknown_when_no_matching_evidence(self) -> None:
        contract = _contract(
            acceptance_criteria=[
                AcceptanceCriterion(id="ac1", description="fast", verification_method="test")
            ]
        )
        outcomes = v.validate_acceptance_criteria(contract, None, _evidence())
        assert outcomes[0].status == "unknown"


class TestRunValidation:
    def test_accepts_clean_task_with_no_declared_steps(self) -> None:
        contract = _contract(allowed_paths=["public/"], validation_steps=[])
        evidence = _evidence(changed_files=["public/index.html"])
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is True

    def test_scope_violation_blocks_acceptance_even_without_declared_scope_step(self) -> None:
        contract = _contract(
            allowed_paths=["public/"], forbidden_paths=[".env"], validation_steps=[]
        )
        evidence = _evidence(changed_files=[".env"])
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is False

    def test_write_task_without_observed_changes_is_rejected(self) -> None:
        contract = _contract(
            allowed_paths=["public/"],
            validation_steps=[
                ValidationStep(kind="build", description="must build", required=True)
            ],
        )
        evidence = _evidence(build_result={"ok": True})

        result = v.run_validation(contract=contract, result=None, evidence=evidence)

        assert result.accepted is False
        assert any(
            finding.step == "changes"
            and not finding.passed
            and "no file changes" in finding.message
            for finding in result.findings
        )

    def test_write_task_noop_on_existing_healthy_checkout_is_accepted(self) -> None:
        contract = _contract(
            allowed_paths=["public/"],
            validation_steps=[
                ValidationStep(kind="build", description="must build", required=True)
            ],
        )
        contract = contract.model_copy(
            update={
                "current_state": contract.current_state.model_copy(
                    update={"current_files": ["public/index.html"]}
                )
            }
        )
        evidence = _evidence(build_result={"ok": True})
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is True
        assert any(
            finding.step == "changes" and finding.passed and "no-op accepted" in finding.message
            for finding in result.findings
        )

    def test_write_task_noop_still_rejected_when_preview_fails(self) -> None:
        contract = _contract(
            allowed_paths=["public/"],
            validation_steps=[
                ValidationStep(kind="preview", description="must preview", required=True)
            ],
        )
        contract = contract.model_copy(
            update={
                "current_state": contract.current_state.model_copy(
                    update={"current_files": ["public/index.html"]}
                )
            }
        )
        evidence = _evidence(
            preview_result={
                "status": "issues_found",
                "fatal_errors": [],
                "pages": [
                    {
                        "url": "/",
                        "console_errors": ["Uncaught TypeError"],
                        "network_errors": [],
                        "overflow_elements": [],
                        "broken_images": [],
                    }
                ],
            }
        )
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is False
        assert any(finding.step == "changes" and not finding.passed for finding in result.findings)
        assert any(
            finding.step == "preview" and not finding.passed and "console" in finding.message
            for finding in result.findings
        )

    def test_should_discard_unsafe_failures_but_keep_preview_work(self) -> None:
        unsafe = v.run_validation(
            contract=_contract(allowed_paths=["public/"]),
            result=None,
            evidence=_evidence(changed_files=[".env"]),
        )
        assert v.should_discard_uncommitted_work(unsafe) is True

        preview_fail = v.run_validation(
            contract=_contract(
                allowed_paths=["public/"],
                validation_steps=[
                    ValidationStep(kind="preview", description="must preview", required=True)
                ],
            ),
            result=None,
            evidence=_evidence(
                changed_files=["public/index.html"],
                preview_result={
                    "status": "issues_found",
                    "fatal_errors": [],
                    "pages": [
                        {
                            "console_errors": ["Uncaught TypeError"],
                            "network_errors": [],
                            "overflow_elements": [],
                            "broken_images": [],
                        }
                    ],
                },
            ),
        )
        assert preview_fail.accepted is False
        assert v.should_discard_uncommitted_work(preview_fail) is False

    def test_read_only_task_without_changes_can_still_pass(self) -> None:
        contract = _contract(
            role=SpecialistRole.QA_REVIEWER,
            allowed_paths=[],
            forbidden_paths=["*"],
            validation_steps=[],
        )

        result = v.run_validation(contract=contract, result=None, evidence=_evidence())

        assert result.accepted is True
        assert all(finding.step != "changes" for finding in result.findings)

    def test_required_build_step_missing_evidence_blocks_acceptance(self) -> None:
        contract = _contract(
            validation_steps=[ValidationStep(kind="build", description="must build", required=True)]
        )
        evidence = _evidence(changed_files=["public/index.html"])
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is False

    def test_required_build_step_satisfied_accepts(self) -> None:
        contract = _contract(
            validation_steps=[ValidationStep(kind="build", description="must build", required=True)]
        )
        evidence = _evidence(changed_files=["public/index.html"], build_result={"ok": True})
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is True

    def test_optional_step_failure_does_not_block_acceptance(self) -> None:
        contract = _contract(
            validation_steps=[
                ValidationStep(kind="build", description="nice to have", required=False)
            ]
        )
        evidence = _evidence(changed_files=["public/index.html"], build_result={"ok": False})
        result = v.run_validation(contract=contract, result=None, evidence=evidence)
        assert result.accepted is True

    def test_failed_run_is_never_silently_accepted_by_a_claim(self) -> None:
        contract = _contract(
            allowed_paths=["public/"],
            validation_steps=[
                ValidationStep(kind="build", description="must build", required=True)
            ],
        )
        evidence = _evidence(changed_files=["public/index.html"], build_result={"ok": False})
        claim = TaskResult(status="completed", summary="I finished successfully!")
        result = v.run_validation(contract=contract, result=claim, evidence=evidence)
        assert result.accepted is False, (
            "a rosy TaskResult claim must not override real build evidence"
        )
