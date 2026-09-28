"""preview_project must never let the model steer the browser to an arbitrary external URL,
the Docker API, cloud metadata, or a host-private address - see product_pipeline.md's
"Key architectural decisions" #3. Both enforcement points (the agent-facing tool in
agent/tools.py and the worker-side preview_runner.py) are tested here so a bypass at one
layer is still caught by the other.
"""

import pytest

from src.services import preview_runner
from src.services.agent.tools import WorkspaceTools


@pytest.mark.parametrize(
    "sanitizer",
    [WorkspaceTools.sanitize_preview_paths, preview_runner._sanitize_paths],
    ids=["tools.WorkspaceTools", "preview_runner"],
)
class TestPreviewPathSanitization:
    def test_keeps_plain_relative_paths(self, sanitizer):
        assert sanitizer(["/", "/services.html", "/pricing"]) == [
            "/",
            "/services.html",
            "/pricing",
        ]

    def test_rejects_absolute_external_urls(self, sanitizer):
        assert sanitizer(["http://evil.example/steal"]) == ["/"]
        assert sanitizer(["https://169.254.169.254/latest/meta-data/"]) == ["/"]

    def test_rejects_protocol_relative_urls(self, sanitizer):
        assert sanitizer(["//evil.example/x"]) == ["/"]

    def test_rejects_parent_directory_traversal(self, sanitizer):
        assert sanitizer(["/../../etc/passwd"]) == ["/"]

    def test_rejects_backslash_smuggling(self, sanitizer):
        assert sanitizer(["/..\\..\\windows"]) == ["/"]

    def test_rejects_missing_leading_slash(self, sanitizer):
        assert sanitizer(["services.html"]) == ["/"]

    def test_drops_non_string_entries_but_keeps_valid_ones(self, sanitizer):
        assert sanitizer([123, None, "/ok", {"a": 1}]) == ["/ok"]

    def test_empty_or_none_defaults_to_root(self, sanitizer):
        assert sanitizer(None) == ["/"]
        assert sanitizer([]) == ["/"]

    def test_all_invalid_falls_back_to_root(self, sanitizer):
        assert sanitizer(["http://x", "//x", "../x"]) == ["/"]

    def test_caps_at_five_paths(self, sanitizer):
        many = [f"/page-{i}" for i in range(10)]
        result = sanitizer(many)
        assert len(result) == 5
        assert result == many[:5]
