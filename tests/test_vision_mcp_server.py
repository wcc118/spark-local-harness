"""Tests for the vision-review MCP server (issue #205).

Covers the pure logic (path validation, per-process call budget, the review()
result shape for every failure mode) without needing a live MCP transport,
the 3090, or a browser: check_lane and score_one_call are monkeypatched.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

mcp = pytest.importorskip("mcp")

from codex_local_harness.lane import LaneStatus
from codex_local_harness.vision_mcp_server import PNG_MAGIC, VisionReviewService, build_server


def _write_png(path: Path) -> None:
    path.write_bytes(PNG_MAGIC + b"rest-of-file-does-not-need-to-be-a-real-png-for-this-test")


def _ok_status() -> LaneStatus:
    return LaneStatus(
        status="ok", model_id="qwen/qwen3.8-27b", loaded_context=65536, quantization="Q4_K_M",
        latency_seconds=0.01, checked_at="2026-09-22T00:00:00+00:00", detail="ok",
    )


def _not_ok_status() -> LaneStatus:
    return LaneStatus(
        status="unreachable", model_id="qwen/qwen3.8-27b", loaded_context=None, quantization=None,
        latency_seconds=0.01, checked_at="2026-09-22T00:00:00+00:00", detail="connection timed out",
    )


class FakeLease:
    """A GpuLease stand-in that always succeeds and records acquire/release calls."""

    calls: list[str] = []

    def __init__(self, name, purpose):
        self.name, self.purpose = name, purpose

    def acquire(self, timeout):
        FakeLease.calls.append("acquire")
        return True

    def release(self):
        FakeLease.calls.append("release")


class RefusingLease(FakeLease):
    def acquire(self, timeout):
        FakeLease.calls.append("acquire-refused")
        return False


@pytest.fixture(autouse=True)
def _reset_fake_lease():
    FakeLease.calls = []
    yield


# --- path validation ---------------------------------------------------------


def test_validate_path_accepts_a_real_png_under_an_allowed_root(tmp_path):
    root = tmp_path / "checkouts"
    root.mkdir()
    png = root / "a" / "shot.png"
    png.parent.mkdir()
    _write_png(png)
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(png))
    assert reason is None
    assert resolved == png.resolve()


def test_validate_path_rejects_a_path_outside_allowed_roots(tmp_path):
    root = tmp_path / "checkouts"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _write_png(outside)
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(outside))
    assert resolved is None
    assert "outside the allowed roots" in reason


def test_validate_path_rejects_a_missing_file(tmp_path):
    root = tmp_path / "checkouts"
    root.mkdir()
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(root / "does-not-exist.png"))
    assert resolved is None
    assert "does not exist" in reason


def test_validate_path_rejects_a_non_png_file(tmp_path):
    root = tmp_path / "checkouts"
    root.mkdir()
    not_png = root / "notes.txt"
    not_png.write_text("hello")
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(not_png))
    assert resolved is None
    assert "not a PNG" in reason


def test_validate_path_rejects_a_directory(tmp_path):
    root = tmp_path / "checkouts"
    root.mkdir()
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(root))
    assert resolved is None
    assert "not a regular file" in reason


def test_validate_path_rejects_traversal_that_escapes_the_root(tmp_path):
    root = tmp_path / "checkouts" / "allowed"
    root.mkdir(parents=True)
    sibling = tmp_path / "checkouts" / "not-allowed.png"
    _write_png(sibling)
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    resolved, reason = service.validate_path(str(root / ".." / "not-allowed.png"))
    assert resolved is None
    assert "outside the allowed roots" in reason


def test_validate_path_rejects_non_string_input():
    service = VisionReviewService(base_url="http://x", allowed_roots=[Path("/")])
    resolved, reason = service.validate_path("")
    assert resolved is None
    assert "non-empty string" in reason


# --- review(): failure modes never raise, always return the fixed shape -----


def test_review_returns_unavailable_when_lane_is_not_ok(tmp_path, monkeypatch):
    root = tmp_path
    png = root / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _not_ok_status())
    service = VisionReviewService(base_url="http://x", allowed_roots=[root])
    result = service.review(str(png))
    assert result == {"available": False, "detail": mock.ANY, "findings": [], "numeric_readings": []}
    assert "not ready" in result["detail"]
    assert FakeLease.calls == []  # never reaches the lease step


def test_review_returns_unavailable_when_lease_cannot_be_acquired(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _ok_status())
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.GpuLease", RefusingLease)
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path], lease_timeout=0.01)
    result = service.review(str(png))
    assert result["available"] is False
    assert "lease" in result["detail"]


def test_review_releases_the_lease_even_when_the_model_call_raises(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _ok_status())
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.GpuLease", FakeLease)

    def boom(*a, **k):
        raise RuntimeError("simulated model-call crash")

    monkeypatch.setattr("codex_local_harness.vision_mcp_server.score_one_call", boom)
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path])
    with pytest.raises(RuntimeError):
        service.review(str(png))
    assert FakeLease.calls == ["acquire", "release"]


def test_review_reports_unavailable_on_a_non_ok_outcome(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _ok_status())
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.GpuLease", FakeLease)
    monkeypatch.setattr(
        "codex_local_harness.vision_mcp_server.score_one_call",
        lambda *a, **k: {"outcome": "schema-invalid", "parsed": None, "attempts": []},
    )
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path])
    result = service.review(str(png))
    assert result["available"] is False
    assert "schema-invalid" in result["detail"]
    assert FakeLease.calls == ["acquire", "release"]


def test_review_returns_findings_on_a_successful_call(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _ok_status())
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.GpuLease", FakeLease)
    parsed = {"findings": [{"description": "d", "region": "r", "severity": "low"}], "numeric_readings": []}
    monkeypatch.setattr(
        "codex_local_harness.vision_mcp_server.score_one_call",
        lambda *a, **k: {"outcome": "ok", "parsed": parsed, "attempts": []},
    )
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path])
    result = service.review(str(png))
    assert result == {"available": True, "detail": "ok", "findings": parsed["findings"], "numeric_readings": []}


# --- per-process call budget --------------------------------------------------


def test_budget_is_enforced_before_the_lane_is_even_checked(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    check_calls = []
    monkeypatch.setattr(
        "codex_local_harness.vision_mcp_server.check_lane",
        lambda *a, **k: check_calls.append(1) or _ok_status(),
    )
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path], max_calls=2)
    service._calls_made = 2  # simulate the budget already spent
    result = service.review(str(png))
    assert result["available"] is False
    assert "budget exhausted" in result["detail"]
    assert check_calls == []  # never even checks the lane once the budget is gone


def test_budget_allows_exactly_max_calls_then_refuses(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _not_ok_status())
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path], max_calls=2)
    r1 = service.review(str(png))
    r2 = service.review(str(png))
    r3 = service.review(str(png))
    assert "not ready" in r1["detail"]
    assert "not ready" in r2["detail"]
    assert "budget exhausted" in r3["detail"]


# --- MCP server wiring ---------------------------------------------------------


def test_build_server_exposes_review_screenshot_with_a_scoped_description():
    service = VisionReviewService(base_url="http://x")
    server = build_server(service)
    assert server.name == "vision-review"


def test_review_result_is_json_serializable(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _write_png(png)
    monkeypatch.setattr("codex_local_harness.vision_mcp_server.check_lane", lambda *a, **k: _not_ok_status())
    service = VisionReviewService(base_url="http://x", allowed_roots=[tmp_path])
    result = service.review(str(png))
    json.dumps(result)  # must not raise
