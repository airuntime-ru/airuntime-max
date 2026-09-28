"""Structured intermediate artifacts for the product-quality pipeline
(product_pipeline.py): brief -> UX spec -> visual direction -> preview -> review.

These persist as JSON under a project's own `.airuntime/planning/` (see
project_git.py/workspace.py - that prefix is already excluded from the agent's own
list_files and from the chat UI, and already git-tracked via commit_snapshot). Never
put a Secret value or raw API key in any of these - they get written to disk, fed back
into review prompts, and logged in pipeline_run_metrics.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProductType = Literal["website", "telegram_bot", "mixed"]


class ProductBrief(BaseModel):
    product_type: ProductType
    target_audience: list[str] = Field(default_factory=list)
    primary_user_goal: str = ""
    primary_conversion: str = ""
    core_entities: list[str] = Field(default_factory=list)
    required_pages_or_flows: list[str] = Field(default_factory=list)
    trust_factors: list[str] = Field(default_factory=list)
    required_features: list[str] = Field(default_factory=list)
    content_requirements: list[str] = Field(default_factory=list)
    visual_constraints: list[str] = Field(default_factory=list)
    claims_requiring_implementation: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)


class BriefOutcome(BaseModel):
    """Result of the brief-planning call: either a usable brief, or up to 3 blocking
    clarifying questions (never both - product_pipeline.py treats non-empty
    clarifying_questions as "ask, don't build yet")."""

    brief: ProductBrief | None = None
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)


class UXStates(BaseModel):
    empty: list[str] = Field(default_factory=list)
    loading: list[str] = Field(default_factory=list)
    success: list[str] = Field(default_factory=list)
    error: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)
    permission_denied: list[str] = Field(default_factory=list)


class UXSpec(BaseModel):
    information_architecture: list[str] = Field(default_factory=list)
    primary_flow: list[str] = Field(default_factory=list)
    secondary_flows: list[str] = Field(default_factory=list)
    screen_requirements: list[str] = Field(default_factory=list)
    states: UXStates = Field(default_factory=UXStates)
    navigation_rules: list[str] = Field(default_factory=list)
    responsive_requirements: list[str] = Field(default_factory=list)
    accessibility_requirements: list[str] = Field(default_factory=list)


class VisualDirection(BaseModel):
    concept_name: str = ""
    concept_rationale: str = ""
    visual_metaphor: str = ""
    typography: dict[str, str] = Field(default_factory=dict)
    color_strategy: dict[str, str] = Field(default_factory=dict)
    layout_principles: list[str] = Field(default_factory=list)
    image_direction: list[str] = Field(default_factory=list)
    motion_principles: list[str] = Field(default_factory=list)
    distinctive_elements: list[str] = Field(default_factory=list)
    patterns_to_avoid: list[str] = Field(default_factory=list)


class Viewport(BaseModel):
    width: int
    height: int


class PreviewPageResult(BaseModel):
    url: str
    viewport: Viewport
    screenshot_ref: str = ""
    console_errors: list[str] = Field(default_factory=list)
    network_errors: list[str] = Field(default_factory=list)
    overflow_elements: list[str] = Field(default_factory=list)
    broken_images: list[str] = Field(default_factory=list)
    visible_headings: list[str] = Field(default_factory=list)
    interactive_elements: list[str] = Field(default_factory=list)
    # Extends the request's suggested schema: a truncated body-text sample. Deterministic
    # checks (console/network/overflow/broken-image) cannot catch self-referential dev-note
    # copy or unproven feature claims living in ordinary paragraphs - review_project needs
    # actual page copy to check those, not just headings.
    visible_text_sample: str = ""


class PreviewResult(BaseModel):
    status: Literal["passed", "issues_found", "failed"]
    pages: list[PreviewPageResult] = Field(default_factory=list)
    fatal_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewScore(BaseModel):
    product_completeness: int = Field(default=0, ge=0, le=100)
    visual_hierarchy: int = Field(default=0, ge=0, le=100)
    originality: int = Field(default=0, ge=0, le=100)
    content_quality: int = Field(default=0, ge=0, le=100)
    responsive_quality: int = Field(default=0, ge=0, le=100)
    accessibility: int = Field(default=0, ge=0, le=100)
    functional_honesty: int = Field(default=0, ge=0, le=100)


class ReviewResult(BaseModel):
    verdict: Literal["pass", "revise", "blocked"]
    score: ReviewScore = Field(default_factory=ReviewScore)
    critical_issues: list[str] = Field(default_factory=list)
    major_issues: list[str] = Field(default_factory=list)
    minor_issues: list[str] = Field(default_factory=list)
    recommended_fixes: list[str] = Field(default_factory=list)
    todos: list[str] = Field(
        default_factory=list,
        description=(
            "Short actionable punch list for the implementer (file or area + what to change). "
            "Required when verdict is revise. Do not ask to rewrite the whole product."
        ),
    )
