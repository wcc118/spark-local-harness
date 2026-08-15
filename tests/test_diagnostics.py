import json

import pytest

from codex_local_harness.diagnostic_cli import main
from codex_local_harness.diagnostics import (
    Adjudication,
    LifecycleEvent,
    append_record,
    latest_adjudications,
    read_records,
)


def adjudication(**changes):
    values = {
        "adjudication_id": "adj-1",
        "run_id": "run-1",
        "verdict": "failed",
        "source": "evaluator",
        "observed_at": "2026-08-15T17:00:00Z",
        "failure_class": "implementation-reader-atomicity",
        "evidence_refs": ("evaluator:case4_hidden",),
    }
    values.update(changes)
    return Adjudication(**values)


def test_adjudications_are_append_only_and_latest_is_derived(tmp_path):
    path = tmp_path / "adjudications.jsonl"
    first = adjudication()
    second = adjudication(
        adjudication_id="adj-2",
        verdict="passed",
        observed_at="2026-08-15T18:00:00Z",
        failure_class=None,
    )
    append_record(path, first)
    original = path.read_bytes()
    append_record(path, second)
    assert path.read_bytes().startswith(original)
    loaded = read_records(path, Adjudication)
    assert loaded == (first, second)
    assert latest_adjudications(loaded) == {"run-1": second}


def test_lifecycle_represents_interrupted_run_without_prompt_content(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    record = LifecycleEvent(
        event_id="run-2:interrupted",
        run_id="run-2",
        event="interrupted",
        observed_at="2026-08-15T17:10:00Z",
        wall_seconds=681.2,
        exit_status=130,
        dirty_entries=1,
    )
    append_record(path, record)
    assert read_records(path, LifecycleEvent) == (record,)
    payload = json.loads(path.read_text())
    assert set(payload) == {
        "dirty_entries", "event", "event_id", "exit_status", "observed_at",
        "run_id", "schema_version", "wall_seconds",
    }


def test_diagnostic_identifiers_reject_multiline_or_freeform_content():
    with pytest.raises(ValueError, match="bounded identifier"):
        adjudication(evidence_refs=("safe", "prompt\nsecret"))
    with pytest.raises(ValueError, match="bounded identifier"):
        adjudication(evidence_refs=("this is freeform prompt content",))
    with pytest.raises(ValueError, match="timezone"):
        adjudication(observed_at="2026-08-15T17:00:00")


def test_cli_appends_hidden_failure_and_interrupted_lifecycle(tmp_path, capsys):
    adjudications = tmp_path / "adjudications.jsonl"
    lifecycle = tmp_path / "lifecycle.jsonl"
    assert main([
        "adjudicate", "--records", str(adjudications), "--adjudication-id", "adj-hidden",
        "--run-id", "run-worker-success", "--verdict", "failed", "--source", "evaluator",
        "--observed-at", "2026-08-15T17:00:00Z", "--failure-class", "test-hidden",
        "--intervention-depth", "audit", "--codex-decision", "CORRECTION",
        "--evidence-ref", "evaluator:case4_hidden",
    ]) == 0
    assert main([
        "lifecycle", "--records", str(lifecycle), "--event-id", "run-correction:interrupted",
        "--run-id", "run-correction", "--event", "interrupted",
        "--observed-at", "2026-08-15T17:12:00Z", "--wall-seconds", "681.2",
        "--exit-status", "130", "--dirty-entries", "1",
    ]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert read_records(adjudications, Adjudication)[0].verdict == "failed"
    assert read_records(lifecycle, LifecycleEvent)[0].event == "interrupted"
