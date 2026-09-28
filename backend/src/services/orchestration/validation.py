"""Multi-level validation (spec section 9) - the gate between "task executed" and "commit
accepted" in git_transaction.py. Every ValidationFinding is computed from TaskEvidence (the
server's factual observation), never from TaskResult's claims directly - `validate_acceptance_
criteria` is the one place TaskResult is even consulted, and only as a secondary data point
that can never promote a criterion to "passed" on its own.

Levels implemented:
  - scope: allowed/forbidden paths honored, no secret-shaped literal added, no unauthorized
    docker-compose file (the platform owns deploy config, not the agent).
  - static: lint/test results IF the executor/skill populated them in evidence - soft-passes
    (not a blocker) when absent, since the platform has no universal static-analysis tool for
    an arbitrary generated project's language/stack; see the final report's honest limitations.
  - build: evidence.build_result, when a build was part of this task's validation_steps.
  - product (acceptance criteria): build/preview/runtime/test-verifiable criteria are checked
    against evidence; "manual"/"llm_review" criteria stay "unknown" here by design - those are
    resolved by a QAReviewer task's own evidence-grounded output at the plan level, not invented
    from nothing in this per-task validator.
  - preview / runtime: evidence.preview_result / evidence.runtime_health_result, only checked
    when the contract's validation_steps actually declared them.

`scope` is always required, regardless of what the contract's validation_steps say - unlike the
other levels, it isn't a check a task can opt out of.

Write-capable tasks must also produce an observed file change. A successful build of the
pre-existing checkout is not evidence that a requested modification was implemented.
"""

from __future__ import annotations

from pathlib import Path

from src.services.orchestration.preview_gate import (
    preview_passes_validation,
    summarize_preview_issues,
)
from src.services.orchestration.schemas import (
    ClaimedAcceptanceResult,
    RiskLevel,
    TaskContract,
    TaskEvidence,
    TaskResult,
    ValidationFinding,
    ValidationResult,
)


def _normalize_rel_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def path_matches_any(path: str, patterns: list[str]) -> bool:
    """Public (not `_`-prefixed): also used by executors.py's ScopedWorkspaceTools to enforce
    a TaskContract's allowed/forbidden paths PREVENTIVELY, at tool-call time - this module's
    own validate_scope() is the DETECTIVE half of the same rule, checked again afterward
    against real evidence regardless of whether prevention worked, as defense in depth.

    Patterns match a path prefix or an exact path component, never a substring. `.env` must
    not match `.env.example`, and `.git` must not match `.gitignore` — that substring bug
    discarded whole implementer attempts in production (crm для агро wrote `.env.example`).
    """
    normalized = _normalize_rel_path(path)
    parts = [part for part in normalized.split("/") if part and part != "."]
    for raw in patterns:
        pattern = _normalize_rel_path(raw).strip("/")
        if not pattern or pattern == "*":
            return True
        if normalized == pattern or normalized.startswith(pattern + "/"):
            return True
        pattern_parts = [part for part in pattern.split("/") if part]
        if len(pattern_parts) == 1 and pattern_parts[0] in parts:
            return True
        span = len(pattern_parts)
        if span > 1:
            for index in range(len(parts) - span + 1):
                if parts[index : index + span] == pattern_parts:
                    return True
    return False


def validate_scope(contract: TaskContract, evidence: TaskEvidence) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    touched = [*evidence.changed_files, *evidence.created_files, *evidence.deleted_files]

    if contract.forbidden_paths == [
        "*"
    ]:  # read-only role ceiling (role_policy.compute_write_scope)
        # ScopedWorkspaceTools (executors.py) already prevents write_file/edit_file/delete_file
        # calls for read-only roles at tool-call time. A git diff showing changed files here
        # means a CONCURRENT write task (parallel_read_only runs without a lease, alongside
        # shared_sequential tasks) committed changes that moved HEAD between this task's
        # begin() and complete() — not that this read-only task itself wrote anything.
        # Failing on that diff would blame the wrong task and waste replan budget.
        findings.append(
            ValidationFinding(
                step="scope",
                passed=True,
                message="read-only role: writes prevented at tool level (ScopedWorkspaceTools)",
            )
        )
    else:
        out_of_scope = (
            [
                p
                for p in touched
                if contract.allowed_paths and not path_matches_any(p, contract.allowed_paths)
            ]
            if contract.allowed_paths
            else []
        )
        forbidden_touched = [p for p in touched if path_matches_any(p, contract.forbidden_paths)]
        if out_of_scope:
            findings.append(
                ValidationFinding(
                    step="scope",
                    passed=False,
                    severity=RiskLevel.MEDIUM,
                    message=f"changed files outside allowed_paths: {out_of_scope}",
                    evidence_ref="changed_files",
                )
            )
        if forbidden_touched:
            findings.append(
                ValidationFinding(
                    step="scope",
                    passed=False,
                    severity=RiskLevel.CRITICAL,
                    message=f"changed forbidden paths: {forbidden_touched}",
                    evidence_ref="changed_files",
                )
            )
        if not out_of_scope and not forbidden_touched:
            findings.append(
                ValidationFinding(
                    step="scope", passed=True, message="all changes within allowed scope"
                )
            )

    if evidence.secret_scan_findings:
        findings.append(
            ValidationFinding(
                step="scope",
                passed=False,
                severity=RiskLevel.CRITICAL,
                message=f"{len(evidence.secret_scan_findings)} credential-shaped literal(s) added",
                evidence_ref="secret_scan_findings",
            )
        )

    unauthorized_compose = [
        f
        for f in (*evidence.created_files, *evidence.changed_files)
        if Path(f).name.lower().startswith("docker-compose")
    ]
    if unauthorized_compose:
        findings.append(
            ValidationFinding(
                step="scope",
                passed=False,
                severity=RiskLevel.HIGH,
                message=f"agent authored a docker-compose file the platform is supposed to own: {unauthorized_compose}",
                evidence_ref="created_files",
            )
        )

    return findings


def validate_static(evidence: TaskEvidence) -> ValidationFinding:
    if evidence.lint_result is None and evidence.test_result is None:
        return ValidationFinding(
            step="static",
            passed=True,
            message="no static analysis result available for this project type - not run, not a blocker",
        )
    ok = (evidence.lint_result or {}).get("ok", True) and (evidence.test_result or {}).get(
        "ok", True
    )
    return ValidationFinding(
        step="static",
        passed=bool(ok),
        severity=RiskLevel.MEDIUM,
        message="static analysis passed" if ok else "lint or test failures reported",
        evidence_ref="lint_result/test_result",
    )


def _existing_deliverable_files(contract: TaskContract) -> list[str]:
    files = contract.current_state.current_files
    return [path for path in files if path and not path.startswith(".")]


def validate_changes(contract: TaskContract, evidence: TaskEvidence) -> ValidationFinding | None:
    """Reject a write task that only inspected an empty or still-broken checkout.

    Follow-up turns on an already-shipped site often inspect and decide nothing needs changing.
    Hard-failing those no-ops produced identical `write-capable task produced no file changes`
    fingerprints until loop_detected parked the run. A no-op is accepted only when the
    checkout already has deliverable files and every quality gate that actually ran still
    passed — a brand-new empty project, a failed build, or a failing preview still reject.
    """
    if contract.forbidden_paths == ["*"]:
        return None
    touched = [*evidence.changed_files, *evidence.created_files, *evidence.deleted_files]
    if touched:
        return ValidationFinding(
            step="changes",
            passed=True,
            message="task produced file changes",
            evidence_ref="changed_files",
        )
    build_ok = evidence.build_result is None or bool(evidence.build_result.get("ok"))
    preview_ok = evidence.preview_result is None or preview_passes_validation(
        evidence.preview_result
    )
    if _existing_deliverable_files(contract) and build_ok and preview_ok:
        return ValidationFinding(
            step="changes",
            passed=True,
            message="no-op accepted: existing checkout already satisfies the task",
            evidence_ref="changed_files",
        )
    return ValidationFinding(
        step="changes",
        passed=False,
        severity=RiskLevel.HIGH,
        message="write-capable task produced no file changes",
        evidence_ref="changed_files",
    )


def validate_build(evidence: TaskEvidence) -> ValidationFinding:
    # Symmetric with validate_preview/validate_runtime below: this is only ever called from
    # run_validation() when "build" is in the contract's declared (required-or-not)
    # validation_steps, so missing evidence here means a required check was never satisfied,
    # not "no build was needed" - that softer case is handled by run_validation simply never
    # calling this function at all when "build" wasn't declared.
    if evidence.build_result is None:
        return ValidationFinding(
            step="build",
            passed=False,
            severity=RiskLevel.HIGH,
            message="build was declared as a validation step but no build_result evidence is present",
        )
    ok = bool(evidence.build_result.get("ok"))
    return ValidationFinding(
        step="build",
        passed=ok,
        severity=RiskLevel.HIGH,
        message="build succeeded" if ok else "build failed",
        evidence_ref="build_result",
    )


def validate_preview(evidence: TaskEvidence) -> ValidationFinding:
    if evidence.preview_result is None:
        return ValidationFinding(
            step="preview",
            passed=False,
            severity=RiskLevel.MEDIUM,
            message="preview was required but no preview_result is present",
        )
    preview = evidence.preview_result
    ok = preview_passes_validation(preview)
    status = preview.get("status")
    if ok and status == "issues_found":
        message = "preview issues_found (external font CDN only; ignored)"
    elif ok:
        message = f"preview status={status}"
    else:
        message = summarize_preview_issues(preview)
    return ValidationFinding(
        step="preview",
        passed=ok,
        severity=RiskLevel.MEDIUM,
        message=message,
        evidence_ref="preview_result",
    )


def validate_runtime(evidence: TaskEvidence) -> ValidationFinding:
    if evidence.runtime_health_result is None:
        return ValidationFinding(
            step="runtime",
            passed=False,
            severity=RiskLevel.HIGH,
            message="runtime validation was required but no runtime_health_result is present",
        )
    ok = bool(evidence.runtime_health_result.get("ok"))
    return ValidationFinding(
        step="runtime",
        passed=ok,
        severity=RiskLevel.HIGH,
        message="runtime healthy"
        if ok
        else "runtime health check failed (see restart_count/port_80_listening)",
        evidence_ref="runtime_health_result",
    )


def _verify_criterion_status(method: str, evidence: TaskEvidence) -> str:
    lookup = {
        "build": evidence.build_result,
        "preview": evidence.preview_result,
        "runtime": evidence.runtime_health_result,
        "test": evidence.test_result,
    }
    if method not in lookup:
        return "unknown"  # "manual" / "llm_review" - resolved at plan level via QAReviewer, not invented here
    result = lookup[method]
    if result is None:
        return "unknown"
    if method == "preview":
        return "passed" if preview_passes_validation(result) else "failed"
    return "passed" if result.get("ok") or result.get("status") == "passed" else "failed"


def validate_acceptance_criteria(
    contract: TaskContract, result: TaskResult | None, evidence: TaskEvidence
) -> list[ClaimedAcceptanceResult]:
    outcomes: list[ClaimedAcceptanceResult] = []
    for criterion in contract.acceptance_criteria:
        status = _verify_criterion_status(criterion.verification_method, evidence)
        outcomes.append(
            ClaimedAcceptanceResult(
                criterion_id=criterion.id,
                status=status,  # type: ignore[arg-type]
                notes=f"verified via {criterion.verification_method} evidence"
                if status != "unknown"
                else "no matching evidence collected",
            )
        )
    return outcomes


def run_validation(
    *, contract: TaskContract, result: TaskResult | None, evidence: TaskEvidence
) -> ValidationResult:
    findings: list[ValidationFinding] = list(validate_scope(contract, evidence))
    changes_finding = validate_changes(contract, evidence)
    if changes_finding is not None:
        findings.append(changes_finding)
    findings.append(validate_static(evidence))

    step_required = {step.kind: step.required for step in contract.validation_steps}
    if "build" in step_required:
        findings.append(validate_build(evidence))
    if "preview" in step_required:
        findings.append(validate_preview(evidence))
    if "runtime" in step_required:
        findings.append(validate_runtime(evidence))

    acceptance_results = validate_acceptance_criteria(contract, result, evidence)

    def _is_hard_required(finding: ValidationFinding) -> bool:
        if finding.step == "scope":
            return True  # never optional, regardless of contract.validation_steps
        if finding.step == "changes":
            return True  # a write task cannot satisfy a new request with an unchanged checkout
        return step_required.get(finding.step, False)

    accepted = not any(not f.passed and _is_hard_required(f) for f in findings)
    return ValidationResult(
        accepted=accepted, findings=findings, acceptance_results=acceptance_results
    )


# Scope/static/build failures leave an unsafe or broken checkout — rollback so the next
# attempt starts from the last accepted commit. Preview/product findings are quality gates
# on otherwise-valid work: discarding them forced the agent to rewrite the site from
# scratch every retry, which is how production runs hit loop_detected with no progress.
_UNSAFE_DISCARD_STEPS = frozenset({"scope", "static", "build"})


def should_discard_uncommitted_work(validation_result: ValidationResult) -> bool:
    if validation_result.accepted:
        return False
    return any(
        not finding.passed and finding.step in _UNSAFE_DISCARD_STEPS
        for finding in validation_result.findings
    )
