"""Exercises deployment/preview/run_preview.py's actual in-browser detection logic against a
real headless Chromium (Playwright) and a small local HTTP fixture - not mocked. This is the
one place that genuinely proves overflow/broken-image/console/network detection works, since
preview_runner.py's own tests (test_preview_runner.py) fake Docker entirely.

Skips cleanly if Playwright's Chromium isn't installed in this environment (CI/dev machines
that haven't run `playwright install chromium`) rather than failing the whole suite.
"""

from __future__ import annotations

import http.server
import importlib.util
import socket
import sys
import threading
from pathlib import Path

import pytest

PREVIEW_DIR = Path(__file__).resolve().parents[1] / "deployment" / "preview"

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception


def _load_run_preview():
    spec = importlib.util.spec_from_file_location("run_preview", PREVIEW_DIR / "run_preview.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


run_preview = _load_run_preview() if sync_playwright is not None else None


FIXTURE_HTML = b"""<!doctype html>
<html><head><title>Fixture</title></head>
<body>
<h1>Main Heading</h1>
<h2>Sub Heading</h2>
<button>Click me</button>
<a href="/somewhere">Link text</a>
<img src="/missing.png" alt="a broken image">
<div style="position:absolute; left:0; top:50px; width:3000px; height:10px;">wide box</div>
<p>Some real body copy a reviewer can read later.</p>
<script>
console.error("boom from the page");
fetch("/does-not-exist").catch(function () {});
</script>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required name by BaseHTTPRequestHandler
        if self.path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FIXTURE_HTML)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # silence default request logging
        pass


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _chromium_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except PlaywrightError:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium is not installed in this environment"
)


@pytest.fixture(scope="module")
def fixture_server():
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_check_page_detects_real_issues(tmp_path, fixture_server, browser):
    result = run_preview._check_page(
        browser,
        base_url=fixture_server,
        path="/",
        viewport={"width": 1440, "height": 900},
        output_dir=tmp_path,
        index=0,
    )

    assert "fatal_error" not in result
    assert any("boom from the page" in entry for entry in result["console_errors"])
    assert any("does-not-exist" in entry for entry in result["network_errors"])
    assert any("missing.png" in entry for entry in result["broken_images"])
    assert any("right" in entry for entry in result["overflow_elements"])
    assert "Main Heading" in result["visible_headings"]
    assert "Sub Heading" in result["visible_headings"]
    assert "Click me" in result["interactive_elements"]
    assert "Link text" in result["interactive_elements"]
    assert "Some real body copy" in result["visible_text_sample"]
    # Self-describing filename (path + viewport), not a positional "page_N" index - see
    # run_preview.py's _slug_for.
    assert result["screenshot_ref"] == "home_1440x900.png"
    screenshot_path = tmp_path / result["screenshot_ref"]
    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 0


def test_check_page_reports_fatal_error_for_unreachable_host(tmp_path, browser):
    # index=1 so the fast single-attempt path is used instead of the slow first-load retry loop.
    result = run_preview._check_page(
        browser,
        base_url="http://127.0.0.1:1",  # nothing listens on port 1
        path="/",
        viewport={"width": 1440, "height": 900},
        output_dir=tmp_path,
        index=1,
    )
    assert "fatal_error" in result
    assert "screenshot_ref" not in result
    assert list(tmp_path.glob("*.png")) == []


def test_main_writes_result_json_end_to_end(tmp_path, fixture_server):
    """Runs run_preview.py as a real subprocess (exactly how deployment/preview/Dockerfile's
    ENTRYPOINT invokes it) rather than importing main() in-process - the sync Playwright API
    asserts there is no running asyncio loop in its own thread, which a plain in-process call
    can trip over inside a pytest session that also runs pytest-asyncio tests. A subprocess
    sidesteps that entirely and is closer to the real deployment shape anyway."""
    import json
    import os
    import subprocess

    output_dir = tmp_path / "out"
    env = dict(os.environ)
    env["PREVIEW_TARGET_HOST"] = fixture_server.split("://", 1)[1].split(":")[0]
    env["PREVIEW_TARGET_PORT"] = fixture_server.rsplit(":", 1)[1]
    env["PREVIEW_PATHS"] = '["/"]'
    env["PREVIEW_OUTPUT_DIR"] = str(output_dir)

    completed = subprocess.run(
        [sys.executable, str(PREVIEW_DIR / "run_preview.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    result_path = output_dir / "result.json"
    assert result_path.exists()
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "issues_found"  # console/network/broken-image issues are present
    assert len(data["pages"]) == 1
    assert data["pages"][0]["broken_images"]
