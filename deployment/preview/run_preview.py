"""Runs inside the per-run preview container (deployment/preview/Dockerfile) - see
backend/src/services/preview_runner.py, which launches this container and reads its output.

Deliberately dumb and self-contained: reads its job from env vars (never a CLI URL argument -
the caller, not this script, decides what host/paths are reachable), visits each page with
Playwright, collects deterministic signals, writes one JSON result to
$PREVIEW_OUTPUT_DIR/result.json (and echoes it to stdout for `docker logs` debugging). Never
raises past main() - a script crash still produces a best-effort result.json so the worker
never has to guess why a run produced nothing.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

MAX_TEXT_SAMPLE_CHARS = 4000
MAX_LIST_ITEMS = 25
NAV_TIMEOUT_MS = 15_000
FIRST_LOAD_RETRIES = 6
FIRST_LOAD_RETRY_DELAY_S = 1.5

# Must stay aligned with backend/src/services/orchestration/preview_gate.py — preview runs
# without outbound internet, so external font CDNs must not fail the gate.
_EXTERNAL_FONT_CDN_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "fast.fonts.net",
    "cloud.typography.com",
)


def _is_benign_cdn_network_error(error: str) -> bool:
    lower = error.lower()
    return any(host in lower for host in _EXTERNAL_FONT_CDN_HOSTS)


def _blocking_network_errors(network_errors: list[str]) -> list[str]:
    return [entry for entry in network_errors if not _is_benign_cdn_network_error(entry)]


def _is_document_horizontal_overflow(item: str) -> bool:
    lower = item.lower()
    return "scrollwidth" in lower or lower.startswith("document ")


def _page_has_blocking_issues(page_result: dict) -> bool:
    overflow = page_result.get("overflow_elements") or []
    return bool(
        page_result.get("console_errors")
        or _blocking_network_errors(page_result.get("network_errors") or [])
        or any(_is_document_horizontal_overflow(item) for item in overflow)
        or page_result.get("broken_images")
    )


_OVERFLOW_SCRIPT = """
() => {
  const vw = window.innerWidth;
  const results = [];
  const doc = document.documentElement;
  if (doc.scrollWidth > vw + 1) {
    results.push(`document (scrollWidth ${doc.scrollWidth} > viewport ${vw})`);
  }
  const all = document.querySelectorAll('body *');
  let flagged = 0;
  for (const el of all) {
    if (flagged >= 20) break;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.right > vw + 1 && rect.left < vw) {
      const label = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '');
      results.push(`${label} (right ${Math.round(rect.right)} > viewport ${vw})`);
      flagged += 1;
    }
  }
  return results;
}
"""

_BROKEN_IMAGES_SCRIPT = """
() => {
  const broken = [];
  for (const img of document.querySelectorAll('img')) {
    if (broken.length >= 20) break;
    if (!img.src) continue;
    if (img.complete && img.naturalWidth === 0) {
      broken.push(img.src + (img.alt ? ` (alt="${img.alt}")` : ' (no alt text)'));
    }
  }
  return broken;
}
"""

_HEADINGS_SCRIPT = """
() => Array.from(document.querySelectorAll('h1, h2, h3'))
  .map((el) => el.innerText.trim())
  .filter(Boolean)
  .slice(0, 25)
"""

_INTERACTIVE_SCRIPT = """
() => {
  const items = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('a, button')) {
    if (items.length >= 25) break;
    const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!label || seen.has(label)) continue;
    seen.add(label);
    items.push(label.slice(0, 80));
  }
  return items;
}
"""


DEFAULT_TARGETS: list[dict] = [{"path": "/", "viewport": {"width": 1440, "height": 900}}]


def _env_targets(name: str) -> list[dict]:
    """Each target is {"path": "/some/path", "viewport": {"width": int, "height": int}} -
    lets the caller mix desktop/mobile viewports per path in one run instead of one fixed
    viewport for every page. Malformed entries are dropped individually rather than failing
    the whole run; an empty/missing/all-invalid env var falls back to one desktop home check."""
    raw = os.environ.get(name)
    if not raw:
        return DEFAULT_TARGETS
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_TARGETS
    if not isinstance(parsed, list):
        return DEFAULT_TARGETS
    cleaned: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        viewport = item.get("viewport") or {}
        if not isinstance(path, str) or not path:
            continue
        try:
            width = int(viewport.get("width", 1440))
            height = int(viewport.get("height", 900))
        except (TypeError, ValueError):
            width, height = 1440, 900
        cleaned.append({"path": path, "viewport": {"width": width, "height": height}})
        if len(cleaned) >= 10:
            break
    return cleaned or DEFAULT_TARGETS


def _slug_for(path: str, viewport: dict) -> str:
    slug = path.strip("/").replace("/", "_") or "home"
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug)
    return f"{slug}_{viewport['width']}x{viewport['height']}"


def _scroll_to_bottom(page) -> None:
    """Step-scroll (not one jump) so IntersectionObserver-driven lazy content/scroll effects
    actually fire, same as a real visitor scrolling down."""
    try:
        height = page.evaluate("document.body.scrollHeight")
        viewport = page.viewport_size or {"height": 900}
        step = max(200, int(viewport.get("height", 900) * 0.8))
        position = 0
        while position < height and position < 20_000:
            position += step
            page.evaluate(f"window.scrollTo(0, {position})")
            page.wait_for_timeout(150)
            height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(100)
    except PlaywrightError:
        pass


def _check_page(
    browser, *, base_url: str, path: str, viewport: dict, output_dir: Path, index: int
) -> dict:
    url = base_url.rstrip("/") + path
    console_errors: list[str] = []
    network_errors: list[str] = []

    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text[:500])
        if msg.type == "error" and len(console_errors) < MAX_LIST_ITEMS
        else None,
    )
    page.on(
        "requestfailed",
        # Playwright's Python sync API exposes Request.failure as a plain str | None (the
        # error text itself), not the {errorText: str} dict some other language bindings use -
        # confirmed by a real AttributeError against a live browser before this fix.
        lambda req: network_errors.append(f"{req.method} {req.url} - {req.failure or 'failed'}")
        if len(network_errors) < MAX_LIST_ITEMS
        else None,
    )
    page.on(
        "response",
        lambda res: network_errors.append(f"{res.status} {res.url}")
        if res.status >= 400 and len(network_errors) < MAX_LIST_ITEMS
        else None,
    )

    last_error: str | None = None
    loaded = False
    for attempt in range(FIRST_LOAD_RETRIES if index == 0 else 1):
        try:
            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="load")
            loaded = True
            break
        except PlaywrightError as exc:
            last_error = str(exc)
            if attempt < FIRST_LOAD_RETRIES - 1:
                time.sleep(FIRST_LOAD_RETRY_DELAY_S)

    if not loaded:
        context.close()
        return {
            "url": url,
            "viewport": viewport,
            "fatal_error": f"Could not load {url}: {last_error}",
        }

    _scroll_to_bottom(page)

    screenshot_name = f"{_slug_for(path, viewport)}.png"
    screenshot_ref = ""
    try:
        page.screenshot(path=str(output_dir / screenshot_name), full_page=True)
        screenshot_ref = screenshot_name
    except PlaywrightError:
        pass

    overflow_elements = _safe_eval(page, _OVERFLOW_SCRIPT, [])
    broken_images = _safe_eval(page, _BROKEN_IMAGES_SCRIPT, [])
    visible_headings = _safe_eval(page, _HEADINGS_SCRIPT, [])
    interactive_elements = _safe_eval(page, _INTERACTIVE_SCRIPT, [])
    text_sample = _safe_eval(page, "() => document.body.innerText || ''", "")

    context.close()

    return {
        "url": url,
        "viewport": viewport,
        "screenshot_ref": screenshot_ref,
        "console_errors": console_errors,
        "network_errors": network_errors,
        "overflow_elements": overflow_elements,
        "broken_images": broken_images,
        "visible_headings": visible_headings,
        "interactive_elements": interactive_elements,
        "visible_text_sample": (text_sample or "")[:MAX_TEXT_SAMPLE_CHARS],
    }


def _safe_eval(page, script: str, default):
    try:
        return page.evaluate(script)
    except PlaywrightError:
        return default


def main() -> int:
    host = os.environ.get("PREVIEW_TARGET_HOST", "").strip()
    port = os.environ.get("PREVIEW_TARGET_PORT", "80").strip() or "80"
    output_dir = Path(os.environ.get("PREVIEW_OUTPUT_DIR", "/output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = _env_targets("PREVIEW_TARGETS")

    result = {"status": "passed", "pages": [], "fatal_errors": [], "warnings": []}
    if not host:
        result["status"] = "failed"
        result["fatal_errors"].append("PREVIEW_TARGET_HOST was not set")
        _write_result(output_dir, result)
        return 0

    base_url = f"http://{host}:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                for index, target in enumerate(targets):
                    page_result = _check_page(
                        browser,
                        base_url=base_url,
                        path=target["path"],
                        viewport=target["viewport"],
                        output_dir=output_dir,
                        index=index,
                    )
                    if "fatal_error" in page_result:
                        result["fatal_errors"].append(page_result["fatal_error"])
                        continue
                    result["pages"].append(page_result)
                    if _page_has_blocking_issues(page_result):
                        vp = page_result["viewport"]
                        result["warnings"].append(
                            f"Issues found on {page_result['url']} ({vp['width']}x{vp['height']})"
                        )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - always produce a result, never a bare crash
        result["fatal_errors"].append(f"Preview run crashed: {exc}")
        traceback.print_exc(file=sys.stderr)

    if result["fatal_errors"] and not result["pages"]:
        result["status"] = "failed"
    elif result["fatal_errors"] or result["warnings"]:
        result["status"] = "issues_found"
    else:
        result["status"] = "passed"

    _write_result(output_dir, result)
    return 0


def _write_result(output_dir: Path, result: dict) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (output_dir / "result.json").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
