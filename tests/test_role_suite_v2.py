from __future__ import annotations

import json
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from qualification.role_suite.v2.cases import all_cases
from qualification.role_suite.v2.run import aggregate, identity, run_case, score


def test_frozen_corpus_shape_and_unique_ids():
    cases = all_cases()
    assert len(cases) == 62
    assert len({c.case_id for c in cases}) == 62
    assert sum(c.suite == "reviewer" for c in cases) == 20
    assert sum(c.suite == "investigator" for c in cases) == 8
    assert sum(c.suite == "test-strategy" for c in cases) == 6
    assert sum(c.suite == "non-coding" for c in cases) == 24
    assert sum(c.suite == "end-to-end" for c in cases) == 4


def test_clean_review_penalizes_false_positive(tmp_path: Path):
    case = next(c for c in all_cases() if c.case_id == "review-clean-01")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(case.evidence))
    before = {"evidence.json": __import__("hashlib").sha256(evidence.read_bytes()).hexdigest()}
    (tmp_path / "result.json").write_text(json.dumps({"answers": ["NULL_GUARD"], "evidence": []}))
    assert score(case, tmp_path, before)[0] is False
    (tmp_path / "result.json").write_text(json.dumps({"answers": [], "evidence": []}))
    assert score(case, tmp_path, before)[0] is True


def test_workspace_mutation_fails(tmp_path: Path):
    case = all_cases()[0]
    evidence = tmp_path / "evidence.json"; evidence.write_text(json.dumps(case.evidence))
    before = {"evidence.json": __import__("hashlib").sha256(evidence.read_bytes()).hexdigest()}
    (tmp_path / "result.json").write_text(json.dumps({"answers": list(case.expected), "evidence": []}))
    evidence.write_text("changed")
    assert score(case, tmp_path, before)[0] is False


def test_identity_binds_policy_and_oracle():
    case = all_cases()[0]
    assert identity(case, "neutral") != identity(case, "structured")


def test_aggregate_separates_correctness_and_completion():
    rows = [
        {"artifact_correct": True, "completed_and_correct": False, "state": "timed-out", "elapsed_seconds": 5, "tool_calls": 2, "tool_errors": 0, "tokens_total": 10},
        {"artifact_correct": True, "completed_and_correct": True, "state": "completed", "elapsed_seconds": 2, "tool_calls": 1, "tool_errors": 0, "tokens_total": 5},
    ]
    assert aggregate(rows) == {"attempts": 2, "artifact_correct": 2, "completed_and_correct": 1,
                               "timeouts": 1, "elapsed_seconds": 7, "tool_calls": 3,
                               "tool_errors": 0, "tokens_total": 15}


def test_run_case_executes_worker_and_scores_external_oracle(tmp_path: Path):
    """Run case should execute fake worker and score against external oracle.

    This test verifies the full runner integration including subprocess execution.

    Note: This test may be skipped on systems where /tmp is mounted with noexec.
    """
    import stat
    import subprocess

    worker = tmp_path / "fake-opencode"
    worker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"answers\":[\"NULL_GUARD\"],\"evidence\":[\"missing null check\"]}' > result.json\n"
        "printf '%s\\n' '{\"part\":{\"type\":\"tool\",\"callID\":\"one\",\"tokens\":{\"total\":7}}}'\n"
    )
    worker.chmod(0o755)

    # Verify the worker is executable (may fail on noexec /tmp)
    try:
        subprocess.run(
            [str(worker)], capture_output=True, text=True, timeout=1, cwd=tmp_path
        )
        worker_executable = True
    except (PermissionError, OSError):
        worker_executable = False

    if not worker_executable:
        pytest.skip("Worker execution not supported (noexec /tmp)")

    args = Namespace(
        output=tmp_path / "output",
        opencode=str(worker),
        model="fake/model",
        route="test",
        policy="neutral",
        timeout=10,
        resume=False,
    )
    case = next(c for c in all_cases() if c.case_id == "review-single-01")
    record = run_case(case, args, repeat=1)
    assert record["state"] == "completed"
    assert record["artifact_correct"] is True
    assert record["completed_and_correct"] is True
    assert record["tool_calls"] == 1
    assert record["tokens_total"] == 7
