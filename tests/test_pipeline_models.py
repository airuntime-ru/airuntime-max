import pytest
from pydantic import ValidationError

from src.services.agent.pipeline_models import (
    BriefOutcome,
    PreviewResult,
    ProductBrief,
    ReviewResult,
    ReviewScore,
    UXSpec,
    Viewport,
    VisualDirection,
)


def test_product_brief_defaults_and_required_field():
    brief = ProductBrief(product_type="website")
    assert brief.target_audience == []
    assert brief.primary_user_goal == ""
    assert brief.definition_of_done == []


def test_product_brief_rejects_unknown_product_type():
    with pytest.raises(ValidationError):
        ProductBrief(product_type="mobile_app")


def test_brief_outcome_clarifying_questions_capped_at_three():
    with pytest.raises(ValidationError):
        BriefOutcome(clarifying_questions=["a", "b", "c", "d"])
    outcome = BriefOutcome(clarifying_questions=["a", "b", "c"])
    assert len(outcome.clarifying_questions) == 3
    assert outcome.brief is None


def test_ux_spec_states_default_to_empty_lists():
    spec = UXSpec()
    assert spec.states.empty == []
    assert spec.states.permission_denied == []


def test_visual_direction_round_trip():
    direction = VisualDirection(
        concept_name="Autoshop atelier",
        typography={"heading": "Bebas Neue"},
        patterns_to_avoid=["purple gradient", "pill buttons everywhere"],
    )
    dumped = direction.model_dump_json()
    restored = VisualDirection.model_validate_json(dumped)
    assert restored.concept_name == "Autoshop atelier"
    assert restored.patterns_to_avoid == ["purple gradient", "pill buttons everywhere"]


def test_review_score_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        ReviewScore(product_completeness=101)
    with pytest.raises(ValidationError):
        ReviewScore(accessibility=-1)
    ReviewScore(product_completeness=100, accessibility=0)  # boundary values are fine


def test_review_result_requires_valid_verdict():
    with pytest.raises(ValidationError):
        ReviewResult(verdict="maybe")
    result = ReviewResult(verdict="revise", critical_issues=["meta text on landing page"])
    assert result.score.product_completeness == 0


def test_preview_result_requires_valid_status():
    with pytest.raises(ValidationError):
        PreviewResult(status="ok")
    result = PreviewResult(status="passed")
    assert result.pages == []
    assert result.fatal_errors == []


def test_preview_page_result_viewport_is_required():
    with pytest.raises(ValidationError):
        PreviewResult.model_validate({"status": "passed", "pages": [{"url": "http://x/"}]})
    ok = PreviewResult.model_validate(
        {
            "status": "issues_found",
            "pages": [
                {
                    "url": "http://x/",
                    "viewport": {"width": 1440, "height": 900},
                    "broken_images": ["http://x/missing.png"],
                }
            ],
        }
    )
    assert ok.pages[0].viewport == Viewport(width=1440, height=900)
    assert ok.pages[0].broken_images == ["http://x/missing.png"]
