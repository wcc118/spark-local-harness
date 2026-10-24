from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_local_harness.review import (
    Envelope,
    RESULT_PREFIX,
    ReviewError,
    audit_tool_scope,
    build_envelope,
    canonicalize_result,
    normalize_remote,
    parse_result,
    prepare_prompt,
    publish_result,
    render_comment,
    incomplete_result,
    validate_result,
    verify_checkout_unchanged,
)
from codex_local_harness.evidence_packet import ReviewerEvidencePacket


REPO = "wcc118/example"
SHA = "a" * 40
BASE_SHA = "b" * 40


def envelope(checkout: Path) -> Envelope:
    return Envelope(
        schema_version=1,
        repository=REPO,
        issue=7,
        pull_request=11,
        checkout=str(checkout),
        branch="issue-7-fix",
        head_sha=SHA,
        base_sha=BASE_SHA,
        base_branch="main",
    )


def valid_result(**changes):
    result = {
        "schema_version": 1,
        "verdict": "APPROVE",
        "repository": REPO,
        "issue": 7,
        "pull_request": 11,
        "reviewed_head_sha": SHA,
        "criteria": [{"criterion": "works", "status": "pass", "evidence": "tests"}],
        "findings": [],
        "tests": [{"command": "pytest", "exit_code": 0, "provenance": "reviewer-observed"}],
        "confidence": "high",
        "summary": "All criteria are supported.",
        "escalation_reason": None,
    }
    result.update(changes)
    return result


def test_prompt_documents_exact_test_provenance_contract(tmp_path):
    prompt = prepare_prompt(envelope(tmp_path), "Review the exact patch")
    assert "Put explanations in a separate outcome field" in prompt
    assert "integer exit_code from 0 through 255" in prompt
    assert "A test you cannot run is not reviewer-observed" in prompt
    result = valid_result(tests=[{
        "command": "pytest", "provenance": "task-reported",
        "outcome": "Reported passing; not independently executed",
    }])
    assert validate_result(result, envelope(tmp_path)) == result
    result["tests"][0]["provenance"] = "harness-observed (re-run independently, exit 0)"
    with pytest.raises(ReviewError, match="provenance"):
        validate_result(result, envelope(tmp_path))


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/wcc118/example.git", REPO),
        ("https://github.com/wcc118/example", REPO),
        ("git@github.com:wcc118/example.git", REPO),
        ("ssh://git@github.com/wcc118/example.git", REPO),
    ],
)
def test_normalize_remote(remote, expected):
    assert normalize_remote(remote) == expected


def test_normalize_remote_rejects_non_github():
    with pytest.raises(ReviewError, match="unsupported"):
        normalize_remote("https://example.invalid/wcc118/example.git")


class PreflightRun:
    def __init__(self, checkout: Path, *, dirty=False, branch="issue-7-fix", sha=SHA, remote=None, labels=None):
        self.checkout = checkout
        self.dirty = dirty
        self.branch = branch
        self.sha = sha
        self.remote = remote or "https://github.com/wcc118/example.git"
        self.labels = labels or ["status/review"]

    def __call__(self, argv, cwd=None):
        command = tuple(argv)
        if command == ("git", "rev-parse", "--show-toplevel"):
            return str(self.checkout) + "\n"
        if command == ("git", "status", "--porcelain=v1"):
            return " M changed.py\n" if self.dirty else ""
        if command == ("git", "remote", "get-url", "origin"):
            return self.remote + "\n"
        if command == ("git", "branch", "--show-current"):
            return self.branch + "\n"
        if command == ("git", "rev-parse", "HEAD"):
            return self.sha + "\n"
        if command == ("git", "ls-files"):
            return "issuebench/code.py\n"
        if command[:3] == ("gh", "pr", "view"):
            return json.dumps({
                "number": 11,
                "state": "OPEN",
                "headRefName": "issue-7-fix",
                "headRefOid": SHA,
                "baseRefName": "main",
            })
        if command[:3] == ("gh", "api", f"repos/{REPO}/git/ref/heads/main"):
            return BASE_SHA + "\n"
        if command[:3] == ("gh", "issue", "view"):
            return json.dumps({
                "number": 7,
                "state": "OPEN",
                "labels": [{"name": item} for item in self.labels],
            })
        raise AssertionError(command)


def test_build_envelope_binds_checkout_to_pr(tmp_path):
    result = build_envelope(tmp_path, REPO, 7, 11, PreflightRun(tmp_path))
    assert result.checkout == str(tmp_path.resolve())
    assert result.head_sha == SHA
    assert result.branch == "issue-7-fix"


def test_build_envelope_reports_tracked_generated_artifacts(tmp_path):
    runner = PreflightRun(tmp_path)
    original = runner.__call__

    def run(argv, cwd=None):
        if tuple(argv) == ("git", "ls-files"):
            return "issuebench/code.py\nissuebench/__pycache__/code.pyc\n.pytest_cache/state\n"
        return original(argv, cwd)

    result = build_envelope(tmp_path, REPO, 7, 11, run)
    assert result.tracked_generated_artifacts == (
        "issuebench/__pycache__/code.pyc",
        ".pytest_cache/state",
    )
    assert result.tracked_generated_artifact_count == 2


def test_build_envelope_bounds_generated_artifact_evidence(tmp_path):
    runner = PreflightRun(tmp_path)
    original = runner.__call__
    generated = [f".venv/lib/__pycache__/module_{index}.pyc" for index in range(1000)]

    def run(argv, cwd=None):
        if tuple(argv) == ("git", "ls-files"):
            return "\n".join(generated) + "\n"
        return original(argv, cwd)

    result = build_envelope(tmp_path, REPO, 7, 11, run)
    assert result.tracked_generated_artifact_count == 1000
    assert len(result.tracked_generated_artifacts) == 25
    assert len(json.dumps(result.as_dict())) < 5000


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (lambda path: PreflightRun(path, dirty=True), "not clean"),
        (lambda path: PreflightRun(path, branch="wrong"), "branch mismatch"),
        (lambda path: PreflightRun(path, sha="c" * 40), "head SHA mismatch"),
        (lambda path: PreflightRun(path, remote="https://github.com/other/repo.git"), "remote mismatch"),
        (lambda path: PreflightRun(path, labels=["status/review", "status/in-progress"]), "exactly status/review"),
    ],
)
def test_build_envelope_fails_closed(tmp_path, runner, message):
    with pytest.raises(ReviewError, match=message):
        build_envelope(tmp_path, REPO, 7, 11, runner(tmp_path))


def write_log(path: Path, texts: list[str]):
    events = [{"type": "text", "part": {"text": text}} for text in texts]
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def test_parse_result_requires_exactly_one_marker(tmp_path):
    log = tmp_path / "run.jsonl"
    write_log(log, ["analysis", RESULT_PREFIX + json.dumps(valid_result())])
    assert parse_result(log)["verdict"] == "APPROVE"


def test_parse_result_accepts_pretty_json_and_ignores_empty_trailing_marker(tmp_path):
    log = tmp_path / "run.jsonl"
    rendered = RESULT_PREFIX + json.dumps(valid_result(), indent=2) + "\n" + RESULT_PREFIX
    write_log(log, [rendered])
    assert parse_result(log)["verdict"] == "APPROVE"


@pytest.mark.parametrize("texts", [[], [RESULT_PREFIX + "{}", RESULT_PREFIX + "{}"]])
def test_parse_result_rejects_missing_or_duplicate_marker(tmp_path, texts):
    log = tmp_path / "run.jsonl"
    write_log(log, texts)
    with pytest.raises(ReviewError, match="exactly one"):
        parse_result(log)


def test_parse_result_rejects_bad_event_json(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ReviewError, match="invalid event JSON"):
        parse_result(log)


def test_parse_result_rejects_marker_without_json(tmp_path):
    log = tmp_path / "run.jsonl"
    write_log(log, [RESULT_PREFIX])
    with pytest.raises(ReviewError, match="observed 0"):
        parse_result(log)


def write_tool_log(path: Path, tool: str, inputs: dict):
    event = {"type": "tool_use", "part": {"tool": tool, "state": {"input": inputs}}}
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_tool_scope_records_but_does_not_fail_denied_attempt(tmp_path):
    log = tmp_path / "run.jsonl"
    event = {
        "type": "tool_use",
        "part": {"tool": "bash", "state": {"status": "error", "input": {"command": "gh issue view 7"}}},
    }
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    audit_tool_scope(log, envelope(tmp_path))


def test_tool_scope_accepts_checkout_paths(tmp_path):
    log = tmp_path / "run.jsonl"
    write_tool_log(log, "bash", {"command": f"cd {tmp_path} && python3 -m pytest"})
    audit_tool_scope(log, envelope(tmp_path))


def test_tool_scope_does_not_include_shell_escape_in_absolute_path(tmp_path):
    log = tmp_path / "run.jsonl"
    write_tool_log(log, "bash", {"command": f'python3 -c "sys.path.insert(0, \\"{tmp_path}\\")"'})
    audit_tool_scope(log, envelope(tmp_path))


def test_tool_scope_accepts_only_bound_scratch_directory(tmp_path):
    log = tmp_path / "run.jsonl"
    scratch = tmp_path.parent / "review-scratch"
    scratch.mkdir()
    write_tool_log(log, "bash", {"command": f"python3 {scratch}/reviewer_probe.py"})
    bound = Envelope(**{**envelope(tmp_path).as_dict(), "scratch_dir": str(scratch)})
    audit_tool_scope(log, bound)
    with pytest.raises(ReviewError, match="outside checkout"):
        audit_tool_scope(log, envelope(tmp_path))


def test_tool_scope_accepts_only_bound_runtime_home(tmp_path):
    log = tmp_path / "run.jsonl"
    runtime_home = tmp_path.parent / "worker-home"
    runtime_home.mkdir()
    write_tool_log(log, "bash", {"command": f"{runtime_home}/.local/bin/pytest -q"})
    bound = Envelope(**{**envelope(tmp_path).as_dict(), "runtime_home": str(runtime_home)})
    audit_tool_scope(log, bound)
    with pytest.raises(ReviewError, match="outside checkout"):
        audit_tool_scope(log, envelope(tmp_path))


def test_tool_scope_accepts_user_installed_tool_executables(tmp_path):
    log = tmp_path / "run.jsonl"
    write_tool_log(log, "bash", {"command": f"{Path.home()}/.local/bin/pytest -q"})
    audit_tool_scope(log, envelope(tmp_path))


@pytest.mark.parametrize(
    ("tool", "inputs", "message"),
    [
        ("bash", {"command": "gh pr review 11 --approve"}, "prohibited command"),
        ("bash", {"command": "git push origin HEAD"}, "prohibited command"),
        ("bash", {"command": "cd /home/example && rm -rf __pycache__"}, "prohibited command"),
        ("bash", {"command": "cd /home/other/repo && pytest"}, "outside checkout"),
        ("read", {"filePath": "/home/other/repo/file.py"}, "outside checkout"),
        ("github_issue_edit", {"issue": "7"}, "prohibited GitHub"),
    ],
)
def test_tool_scope_rejects_prohibited_actions(tmp_path, tool, inputs, message):
    log = tmp_path / "run.jsonl"
    write_tool_log(log, tool, inputs)
    with pytest.raises(ReviewError, match=message):
        audit_tool_scope(log, envelope(tmp_path))


class PostflightRun:
    def __init__(self, checkout: Path, *, sha=SHA, branch="issue-7-fix", dirty=False):
        self.checkout = checkout
        self.sha = sha
        self.branch = branch
        self.dirty = dirty

    def __call__(self, argv, cwd=None):
        command = tuple(argv)
        if command == ("git", "rev-parse", "--show-toplevel"):
            return str(self.checkout)
        if command == ("git", "rev-parse", "HEAD"):
            return self.sha
        if command == ("git", "branch", "--show-current"):
            return self.branch
        if command == ("git", "status", "--porcelain=v1"):
            return "?? artifact" if self.dirty else ""
        raise AssertionError(command)


def test_postflight_accepts_unchanged_checkout(tmp_path):
    verify_checkout_unchanged(envelope(tmp_path), PostflightRun(tmp_path))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sha": "c" * 40}, "HEAD changed"),
        ({"branch": "other"}, "branch changed"),
        ({"dirty": True}, "mutated"),
    ],
)
def test_postflight_rejects_mutation(tmp_path, changes, message):
    with pytest.raises(ReviewError, match=message):
        verify_checkout_unchanged(envelope(tmp_path), PostflightRun(tmp_path, **changes))


def test_validate_result_accepts_all_verdicts(tmp_path):
    env = envelope(tmp_path)
    assert validate_result(valid_result(), env)["verdict"] == "APPROVE"
    assert validate_result(valid_result(verdict="REQUEST_CHANGES"), env)["verdict"] == "REQUEST_CHANGES"
    assert validate_result(valid_result(verdict="ESCALATE", escalation_reason="conflict"), env)["verdict"] == "ESCALATE"


def test_canonicalize_observed_coder_next_alias_shape(tmp_path):
    raw = {
        "verdict": "PASS",
        "issue": f"{REPO}#7",
        "pr": 11,
        "head": SHA,
        "criteria": {"requirements": "pass", "tests": "pass"},
        "findings": ["No blocking findings."],
        "tests": [{"command": "pytest -q", "provenance": "task-reported"}],
        "confidence": 0.95,
        "summary": "The bounded change satisfies the issue.",
    }
    canonical = canonicalize_result(raw, envelope(tmp_path))
    assert validate_result(canonical, envelope(tmp_path))["verdict"] == "APPROVE"
    assert canonical["confidence"] == "high"
    assert canonical["tests"][0]["provenance"] == "task-reported"


def test_canonicalize_preserves_valid_schema(tmp_path):
    result = valid_result()
    assert canonicalize_result(result, envelope(tmp_path)) == result


def test_canonicalize_observed_nested_identity_shape(tmp_path):
    raw = {
        "identity": {
            "repository": REPO, "issue": "#7", "pull_request": "#11",
            "exact_head": SHA,
        },
        "verdict": "APPROVE", "criteria": [], "findings": [], "tests": [],
        "confidence": "high", "summary": "No blocking findings.",
        "escalation_reason": None,
    }
    assert validate_result(canonicalize_result(raw, envelope(tmp_path)), envelope(tmp_path))["verdict"] == "APPROVE"


def test_canonicalize_observed_compact_pr_identity_shape(tmp_path):
    raw = {
        "identity": f"{REPO}#11", "verdict": "APPROVE", "criteria": [],
        "findings": [], "tests": [], "confidence": "high",
        "summary": "No blocking findings.", "escalation": None,
    }
    assert validate_result(canonicalize_result(raw, envelope(tmp_path)), envelope(tmp_path))["pull_request"] == 11


@pytest.mark.parametrize(
    "changes",
    [
        {"issue": 8},
        {"issue": "other/repo#7"},
        {"pr": 12},
        {"head": "c" * 40},
        {"unexpected": "value"},
        {"confidence": 1.2},
        {"tests": {"pytest -q": "pass"}},
    ],
)
def test_canonicalize_rejects_conflicting_or_ambiguous_results(tmp_path, changes):
    raw = {"verdict": "PASS", "summary": "Looks correct.", "confidence": "high", **changes}
    with pytest.raises(ReviewError):
        canonicalize_result(raw, envelope(tmp_path))


def test_validate_result_normalizes_omitted_optional_escalation_reason(tmp_path):
    value = valid_result()
    value.pop("escalation_reason")
    assert validate_result(value, envelope(tmp_path))["escalation_reason"] is None


def test_validate_result_distinguishes_observed_and_reported_tests(tmp_path):
    reported = valid_result(tests=[{"command": "pytest -q", "provenance": "task-reported"}])
    assert validate_result(reported, envelope(tmp_path))["tests"][0]["provenance"] == "task-reported"
    observed = valid_result(tests=[{"command": "git diff --check", "exit_code": 0, "provenance": "harness-observed"}])
    assert validate_result(observed, envelope(tmp_path))["tests"][0]["provenance"] == "harness-observed"
    with pytest.raises(ReviewError, match="provenance"):
        validate_result(valid_result(tests=[{"command": "pytest", "exit_code": 0}]), envelope(tmp_path))
    with pytest.raises(ReviewError, match="exit_code"):
        validate_result(valid_result(tests=[{"command": "pytest", "provenance": "reviewer-observed"}]), envelope(tmp_path))
    with pytest.raises(ReviewError, match="zero through 255"):
        validate_result(valid_result(tests=[{"command": "pytest", "exit_code": 439, "provenance": "harness-observed"}]), envelope(tmp_path))


def test_incomplete_result_is_nonpublishing_and_identity_bound(tmp_path):
    result = incomplete_result(envelope(tmp_path), "token-budget")
    assert result == {
        "schema_version": 1,
        "status": "incomplete",
        "reason": "token-budget",
        "repository": REPO,
        "issue": 7,
        "pull_request": 11,
        "reviewed_head_sha": SHA,
        "published": False,
    }
    with pytest.raises(ReviewError, match="invalid incomplete reason"):
        incomplete_result(envelope(tmp_path), "made-up")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"verdict": "REJECT"}, "invalid verdict"),
        ({"repository": "other/repo"}, "identity mismatch"),
        ({"reviewed_head_sha": "c" * 40}, "identity mismatch"),
        ({"criteria": {}}, "criteria must be a list"),
        ({"verdict": "ESCALATE", "escalation_reason": None}, "requires an escalation reason"),
        ({"findings": [{"severity": "blocker", "message": "broken"}]}, "cannot contain blocker"),
    ],
)
def test_validate_result_rejects_invalid_artifacts(tmp_path, changes, message):
    with pytest.raises(ReviewError, match=message):
        validate_result(valid_result(**changes), envelope(tmp_path))


def test_validate_result_rejects_invalid_confidence_or_summary(tmp_path):
    with pytest.raises(ReviewError, match="confidence"):
        validate_result(valid_result(confidence=1.0), envelope(tmp_path))
    with pytest.raises(ReviewError, match="summary"):
        validate_result(valid_result(summary=""), envelope(tmp_path))


def test_prepare_prompt_contains_immutable_identity(tmp_path):
    prompt = prepare_prompt(envelope(tmp_path), "Review the change")
    assert "IMMUTABLE_REVIEW_ENVELOPE=" in prompt
    assert SHA in prompt
    assert RESULT_PREFIX in prompt
    assert "Inspect the harness-observed exact-head diff" in prompt
    assert "repository evidence boundary is packet-only" in prompt
    assert "Configured shared-memory tools may be used" in prompt
    assert '\"harness-observed\", \"reviewer-observed\", or \"task-reported\"' in prompt
    assert '"reviewed_head_sha":"40-char SHA"' in prompt
    assert "Copy every field exactly once" in prompt
    assert prompt.endswith("REPORTED_TASK_CLAIMS:\nReview the change")


def test_prepare_prompt_labels_observed_packet_before_reported_claims(tmp_path):
    packet = ReviewerEvidencePacket(
        1, "harness-observed", REPO, 7, 11, BASE_SHA, BASE_SHA, SHA, 1,
        ("src/example.py",), False, "1 file changed", False,
        "diff --git a/src/example.py b/src/example.py\n", False, "c" * 64,
        (("pytest", "/home/test/.local/bin/pytest"),),
    )
    prompt = prepare_prompt(envelope(tmp_path), "Reported tests pass", packet)
    assert prompt.index("HARNESS_OBSERVED_EVIDENCE=") < prompt.index("REPORTED_TASK_CLAIMS:")
    assert "expected_verdict" not in prompt
    assert "deterministic search cues only" in prompt
    assert "do not treat their presence or absence as a verdict" in prompt


def test_prepare_prompt_rejects_empty_or_oversized_reported_task(tmp_path):
    with pytest.raises(ReviewError, match="size bound"):
        prepare_prompt(envelope(tmp_path), "")
    with pytest.raises(ReviewError, match="size bound"):
        prepare_prompt(envelope(tmp_path), "x" * 24_001)


def test_render_comment_preserves_shell_metacharacters(tmp_path):
    result = valid_result(summary="Handles `code`, $HOME, $(false), and café.")
    body = render_comment(result, envelope(tmp_path), "run-1")
    assert "`code`, $HOME, $(false), and café" in body
    assert f"<!-- codex-review:run-1:{SHA} -->" in body


class PublishRun:
    def __init__(self, *, stale=False, existing=False, bad_labels=False):
        self.stale = stale
        self.existing = existing
        self.bad_labels = bad_labels
        self.calls = []
        self.body = None

    def __call__(self, argv, cwd=None):
        command = list(argv)
        self.calls.append(command)
        if command[:3] == ["gh", "pr", "view"] and command[-1] == "headRefOid,state":
            return json.dumps({"headRefOid": "c" * 40 if self.stale else SHA, "state": "OPEN"})
        if command[:3] == ["gh", "pr", "view"] and command[-1] == "comments":
            comments = []
            if self.existing:
                comments = [{"body": f"<!-- codex-review:run-1:{SHA} -->", "url": "https://comment/existing"}]
            return json.dumps({"comments": comments})
        if command[:3] == ["gh", "pr", "comment"]:
            body_path = Path(command[command.index("--body-file") + 1])
            self.body = body_path.read_text(encoding="utf-8")
            return "https://comment/new\n"
        if command[:3] == ["gh", "issue", "edit"]:
            return ""
        if command[:3] == ["gh", "issue", "view"]:
            labels = ["status/review"] if self.bad_labels else ["status/review-passed"]
            return json.dumps({"labels": [{"name": item} for item in labels]})
        raise AssertionError(command)


def test_publish_is_idempotent_and_verifies_label(tmp_path):
    runner = PublishRun(existing=True)
    publication = publish_result(valid_result(), envelope(tmp_path), "run-1", runner)
    assert publication["comment_url"] == "https://comment/existing"
    assert not any(call[:3] == ["gh", "pr", "comment"] for call in runner.calls)
    assert publication["label"] == "status/review-passed"


def test_publish_uses_body_file_and_request_changes_blocks(tmp_path):
    runner = PublishRun()
    result = valid_result(verdict="REQUEST_CHANGES", summary="Contains `ticks` and $HOME")
    # Adjust the fake postcondition for the requested-changes target.
    original = runner.__call__

    def run(argv, cwd=None):
        if list(argv)[:3] == ["gh", "issue", "view"]:
            return json.dumps({"labels": [{"name": "status/blocked"}]})
        return original(argv, cwd)

    publication = publish_result(result, envelope(tmp_path), "run-2", run)
    assert publication["label"] == "status/blocked"
    assert "`ticks` and $HOME" in runner.body


def test_publish_rejects_stale_head(tmp_path):
    with pytest.raises(ReviewError, match="stale review"):
        publish_result(valid_result(), envelope(tmp_path), "run-1", PublishRun(stale=True))


def test_publish_fails_when_label_postcondition_is_wrong(tmp_path):
    with pytest.raises(ReviewError, match="label postcondition failed"):
        publish_result(valid_result(), envelope(tmp_path), "run-1", PublishRun(bad_labels=True))


def test_investigative_packet_does_not_advertise_host_executables(tmp_path, monkeypatch):
    from codex_local_harness import review
    monkeypatch.setattr(review, "build_envelope", lambda *a, **kw: envelope(tmp_path))
    monkeypatch.setattr(review, "discover_test_executables", lambda: pytest.fail("host executables are not container evidence"))
    def packet(*args):
        assert args[-1] == {}
        return None
    monkeypatch.setattr(review, "build_reviewer_evidence_packet", packet)
    task = tmp_path / "task.txt"
    task.write_text("Run python3 -m pytest inside the worker environment.")
    assert review.main(["prepare", "--investigative", "--checkout", str(tmp_path),
                        "--repository", REPO, "--issue", "7", "--pr", "11",
                        "--task-file", str(task), "--envelope-out", str(tmp_path / "env.json")]) == 0


def test_memory_contribution_sources_are_not_filesystem_access(tmp_path):
    log = tmp_path / "memory-log.jsonl"
    log.write_text(json.dumps({"type": "tool_use", "part": {"tool": "memory_record_memory", "state": {"status": "completed", "input": {"source": "/home/other/project/source.py", "text": "A sourced finding"}}}}) + "\n")
    audit_tool_scope(log, envelope(tmp_path))
    for tool in ["memory_resolve_memory", "memory_delete_memory", "memory_query"]:
        log.write_text(json.dumps({"type": "tool_use", "part": {"tool": tool, "state": {"status": "completed", "input": {}}}}) + "\n")
        with pytest.raises(ReviewError, match="prohibited memory tool"):
            audit_tool_scope(log, envelope(tmp_path))


def test_memory_record_use_is_allowed_in_tool_scope(tmp_path):
    log = tmp_path / "memory-log.jsonl"
    log.write_text(json.dumps({"type": "tool_use", "part": {"tool": "memory_record_memory_use", "state": {"status": "completed", "input": {"memory_id": "memory:abc123", "outcome": "helpful", "explanation": "Validated in run", "source": "worker observation", "request_id": "use-1"}}}}) + "\n")
    audit_tool_scope(log, envelope(tmp_path))
