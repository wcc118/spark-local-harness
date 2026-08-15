import json

import pytest

from codex_local_harness.merge_publish import MergePublishError, load_merge_decision, publish_merge


HEAD = "b" * 40
MERGE = "c" * 40
DIGEST = "d" * 64


def write_decision(path, **changes):
    payload = {
        "schema_version": 1,
        "repository": "owner/repo",
        "issue": 4,
        "reviewed_head_sha": HEAD,
        "evidence_sha256": DIGEST,
        "decision": "merge",
        "reasons": ["Acceptance criteria satisfied"],
        "decided_by": "codex",
        "decided_at": "2026-08-15T16:00:00Z",
        "deterministic_blockers": [],
    }
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


class FakeRun:
    def __init__(self, *, stale=False, check_conclusion="SUCCESS"):
        self.calls = []
        self.stale = stale
        self.check_conclusion = check_conclusion
        self.merged = False
        self.closed = False
        self.done = False

    def __call__(self, argv):
        self.calls.append(list(argv))
        assert "--repo" in argv
        if argv[:3] == ["gh", "pr", "view"]:
            if self.merged:
                return json.dumps({"number": 5, "state": "MERGED", "headRefOid": HEAD, "mergeCommit": {"oid": MERGE}})
            return json.dumps(
                {
                    "number": 5,
                    "state": "OPEN",
                    "isDraft": False,
                    "headRefOid": "e" * 40 if self.stale else HEAD,
                    "mergeable": "MERGEABLE",
                    "statusCheckRollup": [{"status": "COMPLETED", "conclusion": self.check_conclusion}],
                }
            )
        if argv[:3] == ["gh", "issue", "view"]:
            if self.closed:
                return json.dumps({"state": "CLOSED", "labels": [{"name": "status/done"}]})
            return json.dumps({"number": 4, "state": "OPEN", "labels": [{"name": "status/review"}]})
        if argv[:3] == ["gh", "pr", "merge"]:
            self.merged = True
            return ""
        if argv[:3] == ["gh", "issue", "edit"]:
            self.done = True
            return ""
        if argv[:3] == ["gh", "issue", "close"]:
            self.closed = True
            return ""
        raise AssertionError(argv)


def test_publish_merge_revalidates_and_reaches_durable_state(tmp_path):
    decision = tmp_path / "decision.json"
    write_decision(decision)
    run = FakeRun()
    result = publish_merge("owner/repo", 4, 5, decision, run)
    assert result["merge_commit"] == MERGE
    assert result["issue_state"] == "CLOSED"
    assert run.merged and run.closed and run.done
    merge_call = next(call for call in run.calls if call[:3] == ["gh", "pr", "merge"])
    assert merge_call == ["gh", "pr", "merge", "5", "--repo", "owner/repo", "--merge", "--delete-branch"]


def test_non_codex_or_blocked_decisions_are_rejected(tmp_path):
    decision = tmp_path / "decision.json"
    write_decision(decision, decided_by="reviewer")
    with pytest.raises(MergePublishError, match="Codex MERGE"):
        load_merge_decision(decision, "owner/repo", 4)
    write_decision(decision, deterministic_blockers=["stale-head"])
    with pytest.raises(MergePublishError, match="blockers"):
        load_merge_decision(decision, "owner/repo", 4)


def test_stale_head_stops_before_merge(tmp_path):
    decision = tmp_path / "decision.json"
    write_decision(decision)
    run = FakeRun(stale=True)
    with pytest.raises(MergePublishError, match="head changed"):
        publish_merge("owner/repo", 4, 5, decision, run)
    assert not run.merged


def test_failed_check_stops_before_merge(tmp_path):
    decision = tmp_path / "decision.json"
    write_decision(decision)
    run = FakeRun(check_conclusion="FAILURE")
    with pytest.raises(MergePublishError, match="did not pass"):
        publish_merge("owner/repo", 4, 5, decision, run)
    assert not run.merged

