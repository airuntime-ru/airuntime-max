"""Shared preview acceptance rules — isolated preview has no outbound internet."""

from __future__ import annotations

# External font/style CDNs are unreachable in the preview network; failing on them
# causes endless implementer/replan loops without improving the shipped site.
_EXTERNAL_FONT_CDN_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "fast.fonts.net",
    "cloud.typography.com",
)


def is_benign_preview_network_error(error: str) -> bool:
    lower = error.lower()
    return any(host in lower for host in _EXTERNAL_FONT_CDN_HOSTS)


def blocking_network_errors(network_errors: list[str]) -> list[str]:
    return [entry for entry in network_errors if not is_benign_preview_network_error(entry)]


def is_document_horizontal_overflow(item: str) -> bool:
    """True only for page-level horizontal scroll, not a decorative box past the viewport."""
    lower = item.lower()
    return "scrollwidth" in lower or lower.startswith("document ")


def blocking_overflow_elements(overflow_elements: list[str]) -> list[str]:
    """Ignore abs-positioned decorations (orbit/hero blobs) that do not expand scrollWidth.

    Production runs (crm для агро) looped for hours on `div.demo-orbit (right 1610 > 1440)`
    even though the document itself did not scroll horizontally.
    """
    return [item for item in overflow_elements if is_document_horizontal_overflow(item)]


def _page_issues_blocking(page: dict) -> list[str]:
    issues: list[str] = []
    if page.get("console_errors"):
        issues.append("console_errors")
    if blocking_network_errors(page.get("network_errors") or []):
        issues.append("network_errors")
    if blocking_overflow_elements(page.get("overflow_elements") or []):
        issues.append("overflow")
    if page.get("broken_images"):
        issues.append("broken_images")
    return issues


def preview_passes_validation(preview_result: dict) -> bool:
    status = preview_result.get("status")
    if status == "passed":
        return True
    if status != "issues_found":
        return False
    if preview_result.get("fatal_errors"):
        return False
    pages = preview_result.get("pages")
    if not isinstance(pages, list) or not pages:
        return False
    return all(not _page_issues_blocking(page) for page in pages if isinstance(page, dict))


def _clip_issue(value: object, *, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def summarize_preview_issues(preview_result: dict, *, limit: int = 8) -> str:
    """Compact, fingerprint-stable summary so retries see *what* failed, not just the status."""
    parts: list[str] = []
    for error in preview_result.get("fatal_errors") or []:
        parts.append(f"fatal: {_clip_issue(error)}")
    pages = preview_result.get("pages")
    if isinstance(pages, list):
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            label = page.get("url") or page.get("path") or f"page[{index}]"
            for error in page.get("console_errors") or []:
                parts.append(f"{label} console: {_clip_issue(error)}")
            for error in blocking_network_errors(page.get("network_errors") or []):
                parts.append(f"{label} network: {_clip_issue(error)}")
            for item in blocking_overflow_elements(page.get("overflow_elements") or []):
                parts.append(f"{label} overflow: {_clip_issue(item)}")
            for item in page.get("broken_images") or []:
                parts.append(f"{label} broken_image: {_clip_issue(item)}")
    if not parts:
        status = preview_result.get("status") or "unknown"
        return f"preview status={status}"
    shown = parts[:limit]
    extra = len(parts) - len(shown)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return "; ".join(shown) + suffix
