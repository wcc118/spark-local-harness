from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_local_harness.context_controller import (
    CompactionLifecycleEvent,
    append_compaction_record,
    read_compaction_records,
)


def test_compaction_record_round_trip(tmp_path: Path):
    path = tmp_path / "compaction.jsonl"
    record = CompactionLifecycleEvent(
        record_id="compact-1:started:turn-1",
        compaction_id="compact-1",
        observed_at="2026-09-06T12:00:00Z",
        state="started",
        trigger="automatic",
        surface="primary-codex",
        run_id="run-1",
        session_id="session-1",
        repository="owner/repo",
        issue=169,
        turn_id="turn-1",
        item_id="item-1",
        before_input_tokens=161000,
        context_window=258400,
    )
    assert append_compaction_record(path, record) is True
    assert append_compaction_record(path, record) is False
    records = read_compaction_records(path)
    assert len(records) == 1
    assert records[0].record_id == record.record_id
    assert records[0].state == "started"
    assert records[0].trigger == "automatic"


def test_compaction_record_surfaceistinguishes_primary_and_local():
    primary = CompactionLifecycleEvent(
        record_id="p-1:requested:primary",
        compaction_id="p-1",
        observed_at="2026-09-06T12:00:00Z",
        state="requested",
        trigger="manual",
        surface="primary-codex",
        run_id="run-p",
        session_id="session-p",
        repository="owner/repo",
        issue=169,
        before_input_tokens=161000,
        context_window=258400,
    )
    local = CompactionLifecycleEvent(
        record_id="l-1:started:local",
        compaction_id="l-1",
        observed_at="2026-09-06T12:00:01Z",
        state="started",
        trigger="automatic",
        surface="local-opencode",
        run_id="run-l",
        session_id="session-l",
        repository="owner/repo",
        issue=169,
        item_id="item-l",
    )
    assert primary.surface == "primary-codex"
    assert local.surface == "local-opencode"


def test_compaction_record_excludes_private_content():
    record = CompactionLifecycleEvent(
        record_id="compact-1:completed:turn-1",
        compaction_id="compact-1",
        observed_at="2026-09-06T12:00:00Z",
        state="completed",
        trigger="automatic",
        surface="primary-codex",
        run_id="run-1",
        session_id="session-1",
        repository="owner/repo",
        issue=169,
        turn_id="turn-1",
        item_id="item-1",
        before_input_tokens=161000,
        after_input_tokens=15000,
        context_window=258400,
        error_class=None,
    )
    as_dict = record.as_dict()
    # Verify no private content fields
    assert "prompt" not in as_dict
    assert "reasoning" not in as_dict
    assert "summary" not in as_dict
    # Verify scalar counters only
    assert as_dict["before_input_tokens"] == 161000
    assert as_dict["after_input_tokens"] == 15000


def test_compaction_record_required_fields_validation():
    with pytest.raises(ValueError, match="invalid compaction state"):
        CompactionLifecycleEvent(
            record_id="r-1:unknown:turn-1",
            compaction_id="r-1",
            observed_at="2026-09-06T12:00:00Z",
            state="unknown",
            trigger="automatic",
            surface="primary-codex",
            run_id="run-1",
            session_id="session-1",
            repository="owner/repo",
            issue=169,
        )
    with pytest.raises(ValueError, match="invalid compaction trigger"):
        CompactionLifecycleEvent(
            record_id="r-1:started:turn-1",
            compaction_id="r-1",
            observed_at="2026-09-06T12:00:00Z",
            state="started",
            trigger="unknown",
            surface="primary-codex",
            run_id="run-1",
            session_id="session-1",
            repository="owner/repo",
            issue=169,
        )
    with pytest.raises(ValueError, match="invalid compaction surface"):
        CompactionLifecycleEvent(
            record_id="r-1:started:turn-1",
            compaction_id="r-1",
            observed_at="2026-09-06T12:00:00Z",
            state="started",
            trigger="automatic",
            surface="unknown-surface",
            run_id="run-1",
            session_id="session-1",
            repository="owner/repo",
            issue=169,
        )
    with pytest.raises(ValueError, match="negative"):
        CompactionLifecycleEvent(
            record_id="r-1:started:turn-1",
            compaction_id="r-1",
            observed_at="2026-09-06T12:00:00Z",
            state="started",
            trigger="automatic",
            surface="primary-codex",
            run_id="run-1",
            session_id="session-1",
            repository="owner/repo",
            issue=169,
            before_input_tokens=-1,
        )


def test_append_compaction_record_succeeds_only_once(tmp_path: Path):
    path = tmp_path / "compaction.jsonl"
    record = CompactionLifecycleEvent(
        record_id="unique-id-1",
        compaction_id="compact-1",
        observed_at="2026-09-06T12:00:00Z",
        state="started",
        trigger="automatic",
        surface="primary-codex",
        run_id="run-1",
        session_id="session-1",
        repository="owner/repo",
        issue=169,
        turn_id="turn-1",
        item_id="item-1",
        before_input_tokens=161000,
        context_window=258400,
    )
    assert append_compaction_record(path, record) is True
    assert append_compaction_record(path, record) is False
    assert len(read_compaction_records(path)) == 1
