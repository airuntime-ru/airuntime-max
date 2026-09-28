"""Tests for preview_gate — external font CDN noise must not block acceptance."""

from __future__ import annotations

from src.services.agent.pipeline_models import PreviewPageResult, PreviewResult, Viewport
from src.services.orchestration.preview_gate import (
    blocking_network_errors,
    blocking_overflow_elements,
    is_benign_preview_network_error,
    preview_passes_validation,
    summarize_preview_issues,
)
from src.services.orchestration.skills.preview_review import _deterministic_gate


def test_google_fonts_network_error_is_benign() -> None:
    err = "GET https://fonts.googleapis.com/css2?family=Inter - net::ERR_NAME_NOT_RESOLVED"
    assert is_benign_preview_network_error(err) is True
    assert blocking_network_errors([err]) == []


def test_api_404_is_blocking() -> None:
    err = "404 https://api.example.com/v1/data"
    assert is_benign_preview_network_error(err) is False
    assert blocking_network_errors([err]) == [err]


def test_preview_passes_when_only_font_cdn_failed() -> None:
    preview = {
        "status": "issues_found",
        "fatal_errors": [],
        "pages": [
            {
                "console_errors": [],
                "network_errors": ["GET https://fonts.gstatic.com/s/inter/v13/foo.woff2 - failed"],
                "overflow_elements": [],
                "broken_images": [],
            }
        ],
    }
    assert preview_passes_validation(preview) is True


def test_deterministic_gate_ignores_font_cdn_only() -> None:
    preview = PreviewResult(
        status="issues_found",
        pages=[
            PreviewPageResult(
                url="http://preview/",
                viewport=Viewport(width=1440, height=900),
                network_errors=[
                    "GET https://fonts.googleapis.com/css2?family=Inter - net::ERR_NAME_NOT_RESOLVED"
                ],
            )
        ],
    )
    assert _deterministic_gate(preview) is None


def test_summarize_preview_issues_includes_console_and_document_overflow() -> None:
    summary = summarize_preview_issues(
        {
            "status": "issues_found",
            "fatal_errors": [],
            "pages": [
                {
                    "url": "/",
                    "console_errors": ["Uncaught TypeError: x is not a function"],
                    "network_errors": [],
                    "overflow_elements": ["document (scrollWidth 1600 > viewport 1440)"],
                    "broken_images": [],
                }
            ],
        }
    )
    assert "console" in summary
    assert "overflow" in summary
    assert "TypeError" in summary


def test_decorative_element_overflow_is_not_blocking() -> None:
    item = "div.demo-orbit (right 1610 > viewport 1440)"
    assert blocking_overflow_elements([item]) == []
    preview = {
        "status": "issues_found",
        "fatal_errors": [],
        "pages": [
            {
                "console_errors": [],
                "network_errors": [],
                "overflow_elements": [item],
                "broken_images": [],
            }
        ],
    }
    assert preview_passes_validation(preview) is True


def test_document_scrollwidth_overflow_is_blocking() -> None:
    item = "document (scrollWidth 1610 > viewport 1440)"
    assert blocking_overflow_elements([item]) == [item]
    preview = {
        "status": "issues_found",
        "fatal_errors": [],
        "pages": [
            {
                "console_errors": [],
                "network_errors": [],
                "overflow_elements": [item],
                "broken_images": [],
            }
        ],
    }
    assert preview_passes_validation(preview) is False
