"""Comprehensive tests for session management features (Issue #62)."""

import json
import os
import pytest
from datetime import datetime, timezone

from codex_local_harness.sessions import (
    SessionIdentity,
    SessionUsage,
    SessionSummary,
    aggregate_sessions,
    filter_lines_by_token_discriminator,
    parse_token_count_from_line,
    derive_delta,
    append_usage,
    read_usages,
)
from codex_local_harness.health import TokenUsage, RunHealth
from codex_local_harness.runtime.opencode import OpenCodeStats


class TestSessionIdentity:
    """Tests for SessionIdentity model."""

    def test_session_identity_with_minimal_required_fields(self):
        """Session identity requires at least an issue number."""
        identity = SessionIdentity(issue=62)
        assert identity.issue == 62
        assert identity.session_id is None
        assert identity.repository is None

    def test_session_identity_with_optional_fields(self):
        """Session identity supports optional session_id, repository, pull_request."""
        identity = SessionIdentity(
            issue=62,
            session_id="thread_abc123",
            repository="OWNER/REPO",
            pull_request=123,
            environment="production",
            observed_at="2026-08-16T12:00:00Z",
        )
        assert identity.session_id == "thread_abc123"
        assert identity.repository == "OWNER/REPO"
        assert identity.pull_request == 123

    def test_session_identity_rejects_invalid_github_repo(self):
        """Repository must be in OWNER/REPO format."""
        with pytest.raises(ValueError, match="repository"):
            SessionIdentity(issue=62, repository="invalid")
        with pytest.raises(ValueError, match="repository"):
            SessionIdentity(issue=62, repository="OWNER/")
        with pytest.raises(ValueError, match="repository"):
            SessionIdentity(issue=62, repository="/REPO")

    def test_session_identity_rejects_invalid_issue(self):
        """Issue must be positive."""
        with pytest.raises(ValueError, match="issue"):
            SessionIdentity(issue=0)
        with pytest.raises(ValueError, match="issue"):
            SessionIdentity(issue=-1)

    def test_session_identity_with_defaults(self):
        """with_defaults creates identity from environment-like values."""
        identity = SessionIdentity.with_defaults(
            issue=62,
            session_id="thread_abc",
            repository="OWNER/REPO",
            environment="test",
        )
        assert identity.issue == 62
        assert identity.session_id == "thread_abc"
        assert identity.repository == "OWNER/REPO"

    def test_session_identity_as_dict_roundtrip(self):
        """Identity can be serialized and deserialized."""
        identity = SessionIdentity(
            issue=62,
            session_id="thread_abc",
            repository="OWNER/REPO",
        )
        data = identity.as_dict()
        restored = SessionIdentity.from_dict(data)
        assert restored == identity

    def test_session_identity_with_github_issue_validation(self):
        """GitHub issue validation works correctly."""
        identity = SessionIdentity(issue=62, pull_request=123)
        assert identity.pull_request == 123
        
        with pytest.raises(ValueError, match="pull_request"):
            SessionIdentity(issue=62, pull_request=0)
        with pytest.raises(ValueError, match="pull_request"):
            SessionIdentity(issue=62, pull_request=-1)


class TestSessionUsage:
    """Tests for SessionUsage model."""

    def test_session_usage_from_single_value(self):
        """Create usage from single token count."""
        usage = SessionUsage.from_single_value(
            session_id="thread_abc123",
            issue=62,
            value=1500,
            repository="OWNER/REPO",
        )
        assert usage.session_id == "thread_abc123"
        assert usage.start_tokens == 1500
        assert usage.finish_tokens == 1500
        assert usage.delta == 0
        assert usage.explicit_provenance == "single-value"

    def test_session_usage_from_pair(self):
        """Create usage from start/finish pair with delta calculation."""
        usage = SessionUsage.from_pair(
            session_id="thread_abc123",
            issue=62,
            start_tokens=1000,
            finish_tokens=2500,
            cache_read=50,
            cache_write=25,
        )
        assert usage.start_tokens == 1000
        assert usage.finish_tokens == 2500
        assert usage.delta == 1500
        assert usage.cache_read == 50
        assert usage.cache_write == 25
        assert usage.explicit_provenance == "start-finish-delta"

    def test_session_usage_interrupted(self):
        """Create usage record for interrupted session."""
        usage = SessionUsage.interrupted_session(
            session_id="thread_xyz",
            issue=62,
            repository="OWNER/REPO",
        )
        assert usage.interrupted is True
        assert usage.start_tokens is None
        assert usage.finish_tokens is None
        assert usage.delta is None

    def test_session_usage_missing(self):
        """Create usage record for missing session."""
        usage = SessionUsage.missing_session(
            session_id="thread_xyz",
            issue=62,
        )
        assert usage.missing is True
        assert usage.start_tokens is None
        assert usage.finish_tokens is None

    def test_session_usage_reset(self):
        """Create usage record for reset session."""
        usage = SessionUsage.reset_session(
            session_id="thread_xyz",
            issue=62,
        )
        assert usage.reset is True
        assert usage.start_tokens == 0
        assert usage.finish_tokens == 0
        assert usage.delta == 0

    def test_session_usage_rejects_negative_values(self):
        """Token counts must be non-negative."""
        with pytest.raises(ValueError, match="start_tokens"):
            SessionUsage(
                session_id="thread_abc",
                issue=62,
                start_tokens=-1,
            )
        with pytest.raises(ValueError, match="cache_read"):
            SessionUsage(
                session_id="thread_abc",
                issue=62,
                cache_read=-10,
            )

    def test_session_usage_as_dict_roundtrip(self):
        """Usage can be serialized and deserialized."""
        usage = SessionUsage.from_pair(
            session_id="thread_abc123",
            issue=62,
            start_tokens=1000,
            finish_tokens=2500,
        )
        data = usage.as_dict()
        restored = SessionUsage.from_dict(data)
        assert restored == usage


class TestAggregateSessions:
    """Tests for tuple-based session aggregation."""

    def test_aggregate_single_session(self, tmp_path):
        """Aggregate with single session."""
        identities = (SessionIdentity(issue=62, session_id="thread_1"),)
        usages = (
            SessionUsage.from_pair(
                session_id="thread_1",
                issue=62,
                start_tokens=1000,
                finish_tokens=2500,
            ),
        )
        summaries = aggregate_sessions(identities, usages)
        assert len(summaries) == 1
        assert summaries[0].session_id == "thread_1"
        assert summaries[0].total_tokens == 2500

    def test_aggregate_concurrent_sessions_same_issue(self, tmp_path):
        """Multiple sessions sharing an issue number."""
        identities = (
            SessionIdentity(issue=62, session_id="thread_1"),
            SessionIdentity(issue=62, session_id="thread_2"),
        )
        usages = (
            SessionUsage.from_pair(
                session_id="thread_1",
                issue=62,
                start_tokens=1000,
                finish_tokens=2000,
            ),
            SessionUsage.from_pair(
                session_id="thread_2",
                issue=62,
                start_tokens=500,
                finish_tokens=1500,
            ),
        )
        summaries = aggregate_sessions(identities, usages)
        assert len(summaries) == 2
        # Both should have has_concurrent = True
        assert all(s.has_concurrent for s in summaries)

    def test_aggregate_with_interrupted_session(self, tmp_path):
        """Interrupted sessions are tracked but don't contribute tokens."""
        identities = (SessionIdentity(issue=62, session_id="thread_1"),)
        usages = (
            SessionUsage.interrupted_session(
                session_id="thread_1",
                issue=62,
            ),
        )
        summaries = aggregate_sessions(identities, usages)
        assert len(summaries) == 1
        assert summaries[0].is_interrupted is True
        assert summaries[0].total_tokens is None


class TestTokenFilteringAndParsing:
    """Tests for thread ID file parsing."""

    def test_filter_lines_by_token_discriminator(self):
        """Filter lines containing token-related patterns."""
        content = """
        {"type": "step_finish", "part": {"tokens": {"input": 100, "output": 50}}}
        {"type": "tool_use", "part": {"state": {"status": "success"}}}
        {"session_id": "thread_1", "tokens": 1500}
        some random line without tokens
        """
        filtered = filter_lines_by_token_discriminator(content)
        # The tool_use line doesn't have "tokens" keyword, only 2 match
        assert len(filtered) == 2
        assert any('"tokens"' in line for line in filtered)

    def test_parse_token_count_from_line(self):
        """Extract token count from various line formats."""
        assert parse_token_count_from_line('{"tokens": 1500}') == 1500
        assert parse_token_count_from_line('tokens: 2500') == 2500
        assert parse_token_count_from_line('tokens=3000') == 3000
        assert parse_token_count_from_line('input_tokens: 1000') == 1000
        assert parse_token_count_from_line('no tokens here') is None

    def test_derive_delta_normal(self):
        """Normal delta calculation."""
        assert derive_delta(1000, 2500) == 1500

    def test_derive_delta_reset(self):
        """Handle reset case where finish is 0."""
        # When reset to zero, delta should be start_tokens (complete usage)
        assert derive_delta(1000, 0) == 1000

    def test_derive_delta_invalid(self):
        """Handle unexpected decrease without reset."""
        assert derive_delta(2500, 1000) is None  # Unexpected decrease


class TestOpenCodeTokenParsing:
    """Tests for extended OpenCode token parsing."""

    def test_opencode_stats_with_cache(self):
        """OpenCodeStats includes cache read/write fields."""
        stats = OpenCodeStats(
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=100,
            turns=10,
            tool_calls=5,
            tool_errors=1,
            wall_seconds=60.0,
            cache_read=50,
            cache_write=25,
            cache_provenance="local-cache",
        )
        assert stats.cache_read == 50
        assert stats.cache_write == 25
        assert stats.cache_provenance == "local-cache"

    def test_token_usage_with_cache_provenance(self):
        """TokenUsage includes cache fields."""
        usage = TokenUsage(
            local_input=1000,
            local_output=500,
            local_reasoning=100,
            cache_read=50,
            cache_write=25,
            cache_provenance="local-cache",
        )
        assert usage.cache_total == 75
        assert usage.cache_read == 50
        assert usage.cache_write == 25

    def test_run_health_token_usage_preserves_cache(self, tmp_path):
        """RunHealth with TokenUsage preserves cache information."""
        from codex_local_harness.health import append_jsonl, read_jsonl
        
        health = RunHealth(
            run_id="run-1",
            issue=62,
            role="coder",
            runtime="opencode",
            model="qwen",
            profile="v4",
            task_class="test",
            status="success",
            wall_seconds=60.0,
            usage=TokenUsage(
                local_input=1000,
                local_output=500,
                cache_read=50,
                cache_write=25,
                cache_provenance="local-cache",
            ),
        )
        path = tmp_path / "health.jsonl"
        append_jsonl(path, health)
        records = read_jsonl(path)
        assert len(records) == 1
        assert records[0].usage.cache_read == 50
        assert records[0].usage.cache_write == 25

    def test_opencode_stats_as_health_includes_cache(self, tmp_path):
        """OpenCodeStats.as_health includes cache info in RunHealth."""
        stats = OpenCodeStats(
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=100,
            turns=10,
            tool_calls=5,
            tool_errors=1,
            wall_seconds=60.0,
            cache_read=50,
            cache_write=25,
            cache_provenance="local-cache",
        )
        health = stats.as_health(
            run_id="run-1",
            issue=62,
            role="coder",
            model="qwen",
            profile="v4",
            task_class="test",
            status="success",
        )
        assert health.usage.cache_read == 50
        assert health.usage.cache_write == 25
        assert health.usage.cache_provenance == "local-cache"


class TestSummaryOperations:
    """Tests for summary calculations including cache."""

    def test_health_summarize_includes_cache(self, tmp_path):
        """Health summarize includes cache totals."""
        from codex_local_harness.health import append_jsonl, summarize, read_jsonl
        
        path = tmp_path / "health.jsonl"
        for i in range(3):
            append_jsonl(path, RunHealth(
                run_id=f"run-{i}",
                issue=62,
                role="coder",
                runtime="opencode",
                model="qwen",
                profile="v4",
                task_class="test",
                status="success",
                wall_seconds=60.0,
                usage=TokenUsage(
                    local_input=1000,
                    local_output=500,
                    cache_read=50,
                    cache_write=25,
                ),
            ))
        
        summary = summarize(read_jsonl(path))
        assert summary["cache_read"] == 150  # 50 * 3
        assert summary["cache_write"] == 75   # 25 * 3
        assert summary["cache_total"] == 225


class TestRestartReconstruction:
    """Tests for restart reconstruction scenario."""

    def test_restart_recovery_with_existing_record(self, tmp_path):
        """Recovery after restart: new snapshot uses previous finish as start."""
        path = tmp_path / "sessions.jsonl"
        
        # First session
        usage1 = SessionUsage.from_pair(
            session_id="thread_abc",
            issue=62,
            start_tokens=1000,
            finish_tokens=2500,
        )
        append_usage(path, usage1)
        
        # After restart: new start is previous finish
        usage2 = SessionUsage.from_pair(
            session_id="thread_abc",
            issue=62,
            start_tokens=2500,  # Previous finish
            finish_tokens=3200,
        )
        append_usage(path, usage2)
        
        # Read back
        usages = read_usages(path)
        assert len(usages) == 2
        assert usages[0].finish_tokens == 2500
        assert usages[1].start_tokens == 2500
        assert usages[1].delta == 700  # 3200 - 2500

    def test_reset_recovery_creates_new_log(self, tmp_path):
        """Reset sets tokens to zero, creates new log entry."""
        path = tmp_path / "sessions.jsonl"
        
        # Original session
        usage1 = SessionUsage.from_pair(
            session_id="thread_abc",
            issue=62,
            start_tokens=1000,
            finish_tokens=2000,
        )
        append_usage(path, usage1)
        
        # Reset event
        usage2 = SessionUsage.reset_session(
            session_id="thread_abc",
            issue=62,
        )
        append_usage(path, usage2)
        
        usages = read_usages(path)
        assert usages[1].reset is True
        assert usages[1].start_tokens == 0
        assert usages[1].finish_tokens == 0


class TestSessionCLIIntegration:
    """Integration tests for session CLI commands."""

    def test_session_usage_cli_from_file(self, tmp_path):
        """CLI can parse token usage from file."""
        sessions_file = tmp_path / "sessions.jsonl"
        roll_file = tmp_path / "rollout.txt"
        
        # Create a rolling file with token data
        roll_file.write_text("""
{"type": "step_finish", "part": {"tokens": {"input": 100, "output": 50}}}
{"session_id": "thread_abc", "tokens": 1500}
{"session_id": "thread_abc", "finish_tokens": 2500}
""")
        
        from codex_local_harness.sessions import read_usages, append_usage
        # Manually simulate what the CLI would do
        content = roll_file.read_text()
        from codex_local_harness.sessions import filter_lines_by_token_discriminator, parse_token_count_from_line
        filtered = filter_lines_by_token_discriminator(content)
        for line in filtered:
            try:
                data = json.loads(line)
                if "session_id" in data and "tokens" in data:
                    usage = SessionUsage.from_single_value(
                        session_id=str(data["session_id"]),
                        issue=62,
                        value=int(data["tokens"]),
                    )
                    append_usage(sessions_file, usage)
                if "session_id" in data and "finish_tokens" in data:
                    usage = SessionUsage.from_single_value(
                        session_id=str(data["session_id"]),
                        issue=62,
                        value=int(data["finish_tokens"]),
                    )
                    append_usage(sessions_file, usage)
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Verify the usages were appended
        usages = read_usages(sessions_file)
        assert len(usages) >= 1
        assert all(u.issue == 62 for u in usages)

    def test_session_cli_boundary_start_finish(self, tmp_path):
        """CLI can record start and finish boundaries."""
        sessions_file = tmp_path / "sessions.jsonl"
        
        from codex_local_harness.session_cli import main as session_cli
        
        # Record start
        result = session_cli([
            "boundary", "start",
            "--records", str(sessions_file),
            "--session-id", "thread_abc",
            "--issue", "62",
            "--tokens", "1000",
        ])
        assert result == 0
        
        # Read start and record finish
        start_data = json.loads(sessions_file.read_text().strip())
        start_file = tmp_path / "start.jsonl"
        start_file.write_text(json.dumps(start_data))
        
        result = session_cli([
            "boundary", "finish",
            "--records", str(sessions_file),
            "--session-id", "thread_abc",
            "--issue", "62",
            "--start-record", str(start_file),
            "--tokens", "2500",
        ])
        assert result == 0
        
        usages = read_usages(sessions_file)
        start_usage = next(u for u in usages if "start" in (u.explicit_provenance or ""))
        finish_usage = next(u for u in usages if "finish" in (u.explicit_provenance or ""))
        assert start_usage.explicit_provenance == "explicit-start-boundary"
        assert finish_usage.delta == 1500


class TestDashboardSessionIntegration:
    """Tests for dashboard session data integration."""

    def test_dashboard_includes_session_data_when_available(self, tmp_path):
        """Dashboard snapshot includes session data when sessions file exists."""
        from codex_local_harness.dashboard import DashboardSource
        
        sessions_file = tmp_path / "sessions.jsonl"
        sessions_file.write_text(json.dumps({
            "session_id": "thread_abc",
            "issue": 62,
            "finish_tokens": 2500,
            "cache_read": 50,
            "cache_write": 25,
        }) + "\n")
        
        source = DashboardSource(
            tmp_path / "runs.jsonl",
            tmp_path / "adjudications.jsonl",
            tmp_path / "lifecycle.jsonl",
            sessions=sessions_file,
        )
        
        snapshot = source.snapshot()
        assert "sessions" in snapshot
        assert len(snapshot["sessions"]) == 1

    def test_dashboard_conditional_session_cards(self, tmp_path):
        """Dashboard shows session telemetry cards only when data available."""
        from codex_local_harness.dashboard import DashboardSource, render_dashboard
        
        # With session data
        sessions_file = tmp_path / "sessions.jsonl"
        sessions_file.write_text(json.dumps({
            "session_id": "thread_abc",
            "issue": 62,
            "finish_tokens": 2500,
        }) + "\n")
        
        source = DashboardSource(
            tmp_path / "runs.jsonl",
            tmp_path / "adjudications.jsonl",
            tmp_path / "lifecycle.jsonl",
            sessions=sessions_file,
        )
        snapshot = source.snapshot()
        html = render_dashboard(snapshot)
        assert "Session tokens" in html
        assert "session" in html.lower()

    def test_dashboard_no_session_cards_without_data(self, tmp_path):
        """Dashboard doesn't show session cards when no sessions file."""
        from codex_local_harness.dashboard import DashboardSource, render_dashboard
        
        source = DashboardSource(
            tmp_path / "runs.jsonl",
            tmp_path / "adjudications.jsonl",
            tmp_path / "lifecycle.jsonl",
            # No sessions file
        )
        snapshot = source.snapshot()
        html = render_dashboard(snapshot)
        # Session cards should not appear
        assert "Session tokens" not in html


class TestTokenProvenance:
    """Tests for token provenance tracking."""

    def test_explicit_provenance_types(self):
        """All provenance types are properly recorded."""
        usage = SessionUsage(
            session_id="thread_abc",
            issue=62,
            start_tokens=1000,
            finish_tokens=2500,
            delta=1500,
            explicit_provenance="local-cache-read",
        )
        assert usage.explicit_provenance == "local-cache-read"

    def test_cache_provenance_preserved(self):
        """Cache provenance tracks source of cache hit/miss."""
        usage = SessionUsage(
            session_id="thread_abc",
            issue=62,
            start_tokens=1000,
            finish_tokens=2500,
            delta=1500,
            explicit_provenance="distributed-cache",
        )
        assert "cache" in (usage.explicit_provenance or "").lower() or "distributed" in (usage.explicit_provenance or "")
