from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codex_local_harness.evidence_packet import (
    MAX_CHANGED_FILES,
    MAX_DIFF_STAT_CHARS,
    MAX_PATCH_CHARS,
    ReviewerEvidencePacket,
    build_reviewer_evidence_packet,
    _detect_unexpected_scratch_artifacts,
)


BASE = "a" * 40
HEAD = "b" * 40


class FakeRun:
    def __init__(self, files=("src/value.py", "tests/test_value.py"), stat="2 files changed", patch="diff --git a/value.py b/value.py\n", status_output=""):
        self.files = files
        self.stat = stat
        self.patch = patch
        self.status_output = status_output

    def __call__(self, argv, cwd=None):
        if argv[:2] == ["git", "merge-base"]:
            return BASE + "\n"
        if argv[:3] == ["git", "diff", "--name-only"]:
            return "\n".join(self.files) + "\n"
        if argv[:3] == ["git", "diff", "--stat"]:
            return self.stat + "\n"
        if argv[:3] == ["git", "diff", "--no-ext-diff"]:
            return self.patch
        if argv[:3] == ["git", "diff", "--check"]:
            return ""
        if argv[:3] == ["git", "status", "--porcelain=v1"]:
            return self.status_output
        raise AssertionError(argv)


def packet(run=None):
    return build_reviewer_evidence_packet(
        Path("/checkout"),
        "owner/repo", 7, 11, BASE, HEAD, run or FakeRun(),
        {"pytest": "/home/test/.local/bin/pytest", "python3": "/usr/bin/python3"},
    )


def test_packet_is_deterministic_head_bound_and_answer_neutral():
    value = packet()
    assert value == packet()
    assert value.base_tip_sha == BASE and value.comparison_base_sha == BASE and value.head_sha == HEAD
    assert value.provenance == "harness-observed"
    serialized = value.compact_json()
    for prohibited in ("expected_verdict", "scout_findings", "codex_conclusion", "acceptance_criteria"):
        assert prohibited not in serialized
    assert json.loads(serialized)["test_executables"][0][0] == "pytest"
    assert json.loads(serialized)["verification_exit_code"] == 0
    assert json.loads(serialized)["verification_output"] == ""


def test_packet_bounds_files_stat_and_patch_with_full_patch_digest():
    files = tuple(f"src/file_{index}.py" for index in range(MAX_CHANGED_FILES + 3))
    raw_patch = "x" * (MAX_PATCH_CHARS + 100)
    value = packet(FakeRun(files, "s" * (MAX_DIFF_STAT_CHARS + 100), raw_patch, ""))
    assert len(value.changed_files) == MAX_CHANGED_FILES
    assert value.changed_files_truncated and value.diff_stat_truncated and value.patch_truncated
    assert len(value.diff_stat) == MAX_DIFF_STAT_CHARS
    assert len(value.patch) == MAX_PATCH_CHARS
    assert value.patch_sha256 == hashlib.sha256(raw_patch.encode()).hexdigest()


@pytest.mark.parametrize("files", [("src/a.py", "src/a.py"), ("../escape.py",)])
def test_packet_rejects_duplicate_or_unsafe_paths(files):
    with pytest.raises(ValueError):
        packet(FakeRun(files, status_output=""))


def test_packet_validates_paths_before_inventory_truncation():
    files = tuple(f"src/file_{index}.py" for index in range(MAX_CHANGED_FILES)) + ("../hidden.py",)
    with pytest.raises(ValueError, match="unsafe"):
        packet(FakeRun(files, status_output=""))


def test_packet_rejects_empty_or_malformed_identity():
    with pytest.raises(ValueError):
        build_reviewer_evidence_packet(
            Path("/checkout"), "owner/repo", 7, 11,
            "bad", HEAD, FakeRun(status_output=""), {},
        )


def test_packet_json_escapes_observed_text():
    value = packet(FakeRun(stat='changed "quoted"', patch="diff\n<script>", status_output=""))
    serialized = value.compact_json()
    assert '\\"quoted\\"' in serialized
    assert "<script>" in json.loads(serialized)["patch"]


class DiffCheckRun:
    """Fake run that supports git diff --check output and status."""
    def __init__(self, files=("src/value.py", "tests/test_value.py"), stat="2 files changed",
                 patch="diff --git a/value.py b/value.py\n", diff_check_output="",
                 status_output=""):
        self.files = files
        self.stat = stat
        self.patch = patch
        self.diff_check_output = diff_check_output
        self.status_output = status_output

    def __call__(self, argv, cwd=None):
        if argv[:2] == ["git", "merge-base"]:
            return BASE + "\n"
        if argv[:3] == ["git", "diff", "--name-only"]:
            return "\n".join(self.files) + "\n"
        if argv[:3] == ["git", "diff", "--stat"]:
            return self.stat + "\n"
        if argv[:3] == ["git", "diff", "--no-ext-diff"]:
            return self.patch
        if argv[:3] == ["git", "diff", "--check"]:
            return self.diff_check_output
        if argv[:3] == ["git", "status", "--porcelain=v1"]:
            return self.status_output
        raise AssertionError(argv)


def test_packet_clean_checkout_passes_verification():
    """Test that a clean checkout with no diff-check failures passes."""
    value = packet(DiffCheckRun(diff_check_output="", status_output=""))
    assert value.verification_exit_code == 0
    assert value.verification_output == ""
    assert json.loads(value.compact_json())["verification_exit_code"] == 0


def test_packet_diff_check_failure_raises_with_detailed_output():
    """Test that git diff --check failures are captured and reported."""
    from codex_local_harness.evidence_packet import _detect_unexpected_scratch_artifacts
    
    diff_check_output = "src/value.py:15: trailing whitespace.\n"
    status_output = ""
    
    with pytest.raises(ValueError, match="trailing whitespace"):
        packet(DiffCheckRun(diff_check_output=diff_check_output, status_output=status_output))


def test_packet_captures_diff_check_output_in_packet():
    """Test that git diff --check output is captured even on success."""
    diff_check_output = "src/value.py:5: whitespace error.\n"
    status_output = ""
    
    with pytest.raises(ValueError, match="whitespace error"):
        packet(DiffCheckRun(diff_check_output=diff_check_output, status_output=status_output))


def test_generated_artifacts_are_expected_in_status():
    """Test that known generated runtime artifacts are filtered from unexpected list."""
    status_output = "?? issuebench/__pycache__/\n?? .pytest_cache/\n?? .coverage\n"
    assert _detect_unexpected_scratch_artifacts(status_output) == []


def test_unexpected_scratch_artifacts_are_detected():
    """Test that unknown scratch artifacts are detected as failures."""
    from codex_local_harness.evidence_packet import _detect_unexpected_scratch_artifacts
    
    status_output = "?? issuebench/__pycache__/\n?? .pytest_cache/\n?? .coverage\n?? scratch.tmp\n"
    unexpected = _detect_unexpected_scratch_artifacts(status_output)
    assert unexpected == ["scratch.tmp"]


def test_unexpected_scratch_artifacts_raise_value_error():
    """Test that unexpected scratch artifacts cause packet build to fail."""
    status_output = "?? issuebench/__pycache__/\n?? .pytest_cache/\n?? .coverage\n?? scratch.tmp\n"
    
    with pytest.raises(ValueError, match="scratch.tmp"):
        packet(DiffCheckRun(diff_check_output="", status_output=status_output))


def test_packet_detects_multiple_unexpected_artifacts():
    """Test that multiple unexpected artifacts are all reported."""
    status_output = "?? temp_file.txt\n?? other.tmp\n?? issuebench/__pycache__/\n"
    unexpected = _detect_unexpected_scratch_artifacts(status_output)
    assert set(unexpected) == {"temp_file.txt", "other.tmp"}


def test_packet_with_trailing_whitespace_diff_check_failure():
    """Test specific whitespace error detection from git diff --check."""
    diff_check_output = "src/model.py:42: trailing whitespace.\nsrc/model.py:100: whitespace error before EOF.\n"
    
    with pytest.raises(ValueError, match="trailing whitespace"):
        packet(DiffCheckRun(diff_check_output=diff_check_output, status_output=""))
