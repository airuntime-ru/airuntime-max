from __future__ import annotations

import base64

from src.services.agent.pipeline_models import (
    PreviewPageResult,
    PreviewResult,
    ReviewResult,
    ReviewScore,
    Viewport,
)
from src.services.orchestration.skills.preview_review import (
    _apply_score_gate,
    _load_screenshots,
)


def test_load_screenshots_limits_codex_workspace_to_preview_run(tmp_path) -> None:
    screenshot_dir = tmp_path / ".airuntime" / "preview" / "run-1"
    screenshot_dir.mkdir(parents=True)
    desktop = screenshot_dir / "home_1440x900.png"
    mobile = screenshot_dir / "home_390x844.png"
    desktop.write_bytes(b"desktop")
    mobile.write_bytes(b"mobile")
    preview = PreviewResult(
        status="passed",
        pages=[
            PreviewPageResult(
                url="http://preview/",
                viewport=Viewport(width=1440, height=900),
                screenshot_ref=".airuntime/preview/run-1/home_1440x900.png",
            ),
            PreviewPageResult(
                url="http://preview/",
                viewport=Viewport(width=390, height=844),
                screenshot_ref=".airuntime/preview/run-1/home_390x844.png",
            ),
        ],
    )

    images, codex_root = _load_screenshots(preview, workspace_root=str(tmp_path))

    assert codex_root == screenshot_dir.resolve()
    assert [image.filename for image in images] == [desktop.name, mobile.name]
    assert base64.b64decode(images[0].data_base64) == b"desktop"


def test_visual_score_below_floor_forces_revision() -> None:
    review = ReviewResult(
        verdict="pass",
        score=ReviewScore(
            product_completeness=90,
            visual_hierarchy=70,
            originality=60,
            content_quality=90,
            responsive_quality=90,
            accessibility=90,
            functional_honesty=90,
        ),
    )

    gated = _apply_score_gate(
        review,
        floors={"visual_hierarchy": 82, "originality": 75},
    )

    assert gated.verdict == "revise"
    assert any("visual_hierarchy=70" in issue for issue in gated.major_issues)
    assert any("visual_hierarchy" in item for item in gated.todos)
