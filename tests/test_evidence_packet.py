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
)


BASE = "a" * 40
HEAD = "b" * 40


class FakeRun:
    def __init__(self, files=("src/value.py", "tests/test_value.py"), stat="2 files changed", patch="diff --git a/value.py b/value.py\n"):
        self.files = files
        self.stat = stat
        self.patch = patch

    def __call__(self, argv, cwd=None):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return "\n".join(self.files) + "\n"
        if argv[:3] == ["git", "diff", "--stat"]:
            return self.stat + "\n"
        if argv[:3] == ["git", "diff", "--no-ext-diff"]:
            return self.patch
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
    assert value.base_sha == BASE and value.head_sha == HEAD
    assert value.provenance == "harness-observed"
    serialized = value.compact_json()
    for prohibited in ("expected_verdict", "scout_findings", "codex_conclusion", "acceptance_criteria"):
        assert prohibited not in serialized
    assert json.loads(serialized)["test_executables"][0][0] == "pytest"


def test_packet_bounds_files_stat_and_patch_with_full_patch_digest():
    files = tuple(f"src/file_{index}.py" for index in range(MAX_CHANGED_FILES + 3))
    raw_patch = "x" * (MAX_PATCH_CHARS + 100)
    value = packet(FakeRun(files, "s" * (MAX_DIFF_STAT_CHARS + 100), raw_patch))
    assert len(value.changed_files) == MAX_CHANGED_FILES
    assert value.changed_files_truncated and value.diff_stat_truncated and value.patch_truncated
    assert len(value.diff_stat) == MAX_DIFF_STAT_CHARS
    assert len(value.patch) == MAX_PATCH_CHARS
    assert value.patch_sha256 == hashlib.sha256(raw_patch.encode()).hexdigest()


@pytest.mark.parametrize("files", [("src/a.py", "src/a.py"), ("../escape.py",)])
def test_packet_rejects_duplicate_or_unsafe_paths(files):
    with pytest.raises(ValueError):
        packet(FakeRun(files))


def test_packet_validates_paths_before_inventory_truncation():
    files = tuple(f"src/file_{index}.py" for index in range(MAX_CHANGED_FILES)) + ("../hidden.py",)
    with pytest.raises(ValueError, match="unsafe"):
        packet(FakeRun(files))


def test_packet_rejects_empty_or_malformed_identity():
    with pytest.raises(ValueError):
        build_reviewer_evidence_packet(
            Path("/checkout"), "owner/repo", 7, 11,
            "bad", HEAD, FakeRun(), {},
        )


def test_packet_json_escapes_observed_text():
    value = packet(FakeRun(stat='changed "quoted"', patch="diff\n<script>"))
    serialized = value.compact_json()
    assert '\\"quoted\\"' in serialized
    assert "<script>" in json.loads(serialized)["patch"]
