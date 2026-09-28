from __future__ import annotations

import pytest

from src.services.agent.pipeline_models import ReviewResult, ReviewScore
from src.services.orchestration.skills.base import SkillContext
from src.services.orchestration.skills.product_review import (
    ProductQualityReviewSkill,
    _evidence_pack,
    _quality_gate,
)


def _score(**updates: int) -> ReviewScore:
    values = {
        "product_completeness": 95,
        "visual_hierarchy": 90,
        "originality": 90,
        "content_quality": 90,
        "responsive_quality": 90,
        "accessibility": 90,
        "functional_honesty": 95,
    }
    values.update(updates)
    return ReviewScore(**values)


def test_evidence_pack_excludes_private_state_and_redacts_bare_credentials(tmp_path) -> None:
    (tmp_path / ".env").write_text("API_KEY=do-not-send", encoding="utf-8")
    hidden = tmp_path / ".airuntime"
    hidden.mkdir()
    (hidden / "internal.txt").write_text("private state", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.13-slim", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'BOT_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("aiogram==3.20.0", encoding="utf-8")

    evidence, missing = _evidence_pack(str(tmp_path), project_type="telegram_bot")

    assert missing == []
    assert ".env" not in evidence
    assert ".airuntime" not in evidence
    assert "do-not-send" not in evidence
    assert "123456789:abcdefghijklmnopqrstuvwxyzABCDE" not in evidence
    assert "[REDACTED" in evidence


def test_quality_gate_rejects_missing_files_and_low_functional_honesty() -> None:
    review = ReviewResult(
        verdict="pass",
        score=_score(functional_honesty=70),
    )

    gated = _quality_gate(review, missing_paths=["app.py"])

    assert gated.verdict == "revise"
    assert any("missing required project files: app.py" in issue for issue in gated.major_issues)
    assert any("functional_honesty=70" in issue for issue in gated.major_issues)


@pytest.mark.asyncio
async def test_product_review_returns_usage_and_partial_findings(tmp_path, monkeypatch) -> None:
    import src.services.orchestration.skills.product_review as module

    (tmp_path / "Dockerfile").write_text("FROM python:3.13-slim", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('bot')", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("aiogram==3.20.0", encoding="utf-8")

    async def _fake_complete(**kwargs):  # noqa: ANN003
        kwargs["usage_sink"]({"input_tokens": 100, "output_tokens": 20})
        kwargs["usage_sink"]({"input_tokens": 10, "output_tokens": 5})
        return ReviewResult(
            verdict="revise",
            score=_score(),
            major_issues=["No /start handler"],
        )

    monkeypatch.setattr(module, "complete_structured", _fake_complete)
    context = SkillContext(
        project_id="project",
        run_id="run",
        task_id="task",
        workspace_root=str(tmp_path),
        project_type="telegram_bot",
        arguments={
            "brief_text": "Create a support bot",
            "provider_name": "openai",
            "model": "gpt-5.6-sol",
            "api_key": "key",
        },
        db=None,  # type: ignore[arg-type]
    )

    result = await ProductQualityReviewSkill().execute(context)

    assert result.status == "partial"
    assert result.output["review"]["major_issues"] == ["No /start handler"]
    assert result.output["usage"] == {"input_tokens": 110, "output_tokens": 25}
