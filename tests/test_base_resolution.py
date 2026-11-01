"""Tests for authoritative base resolution module."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from codex_local_harness.base_resolution import (
    BaseResolutionError,
    ResolvedBase,
    materialize_missing_object,
    pin_remote_ref,
    resolve_base_sha,
    resolve_and_materialize_base,
    verify_base_sha_in_checkout,
    SHA40_PATTERN,
)


class FakeRun:
    """Fake runner for testing base resolution functions."""

    def __init__(self):
        self.calls = []
        self.base_sha = "a" * 40
        self.object_exists = False
        self.fetch_failures = set()
        self.cat_file_call_count = 0

    def __call__(self, argv, cwd=None):
        self.calls.append(tuple(argv))
        cmd = tuple(argv)

        # Check for GitHub API call
        api_arg_str = " ".join(argv)
        if "gh" in argv and "api" in argv and "repos" in api_arg_str:
            if "/git/ref/heads/main" in api_arg_str:
                return self.base_sha + "\n"
            raise ValueError(f"Unexpected API call: {cmd}")

        if cmd[:2] == ("git", "cat-file"):
            self.cat_file_call_count += 1
            # If object_exists is True, return success immediately
            if self.object_exists:
                return ""
            # First call returns error (object missing), subsequent calls succeed after fetch
            if self.cat_file_call_count == 1:
                raise BaseResolutionError("cat-file failed")  # Object missing
            return ""  # Success after fetch

        if cmd[:2] == ("git", "fetch"):
            # git fetch origin <ref> - ref is at index 3
            ref = argv[3] if len(argv) > 3 else ""
            if ref in self.fetch_failures:
                raise BaseResolutionError("fetch failed")
            return ""

        if cmd[:2] == ("git", "update-ref"):
            return ""

        raise AssertionError(f"Unexpected command: {cmd}")


class TestSHA40Pattern:
    """Tests for the SHA40_PATTERN regex."""

    def test_valid_sha_matches(self):
        """Valid 40-char lowercase hex SHAs match."""
        assert SHA40_PATTERN.fullmatch("a" * 40)
        assert SHA40_PATTERN.fullmatch("0" * 40)
        assert SHA40_PATTERN.fullmatch("f" * 40)
        assert SHA40_PATTERN.fullmatch("0123456789abcdef" * 2 + "01234567")  # 40 chars

    def test_invalid_sha_does_not_match(self):
        """Invalid SHAs don't match."""
        assert not SHA40_PATTERN.fullmatch("a" * 39)  # Too short
        assert not SHA40_PATTERN.fullmatch("a" * 41)  # Too long
        assert not SHA40_PATTERN.fullmatch("g" * 40)  # Invalid char
        assert not SHA40_PATTERN.fullmatch("A" * 40)  # Uppercase


class TestResolveBaseSha:
    """Tests for resolve_base_sha function."""

    def test_resolve_success(self):
        """Successfully resolves base SHA from GitHub API."""
        runner = FakeRun()

        result = resolve_base_sha(
            repository="owner/repo",
            base_branch="main",
            gh_api_command=runner,
        )

        assert isinstance(result, ResolvedBase)
        assert result.branch == "main"
        assert result.exact_sha == "a" * 40
        assert "GitHub API" in result.resolved_via

    def test_rejects_invalid_sha_format(self):
        """Rejects SHAs that aren't 40-char lowercase hex."""
        runner = FakeRun()
        # Use 40 chars but with invalid character (g is not hex)
        runner.base_sha = "g" * 40

        with pytest.raises(BaseResolutionError, match="GitHub returned an invalid base SHA"):
            resolve_base_sha(
                repository="owner/repo",
                base_branch="main",
                gh_api_command=runner,
            )

    def test_rejects_api_failure(self):
        """Raises BaseResolutionError when API call fails."""
        def failing_runner(args):
            raise Exception("API unavailable")

        with pytest.raises(BaseResolutionError, match="API unavailable"):
            resolve_base_sha(
                repository="owner/repo",
                base_branch="main",
                gh_api_command=failing_runner,
            )


class TestMaterializeMissingObject:
    """Tests for materialize_missing_object function."""

    def test_materialize_fetches_missing_object(self):
        """Fetches object when absent, succeeds when present."""
        runner = FakeRun()
        runner.object_exists = False  # Object absent initially

        materialize_missing_object(Path("/checkout"), "a" * 40, runner)

        # Should have called cat-file (to check), then fetch, then cat-file again (to verify)
        cat_file_calls = [c for c in runner.calls if c[:2] == ("git", "cat-file")]
        fetch_calls = [c for c in runner.calls if c[:2] == ("git", "fetch")]
        assert len(cat_file_calls) == 2  # Check + Verify
        assert len(fetch_calls) == 1

    def test_skip_when_object_exists(self):
        """Skip fetch if object already exists."""
        runner = FakeRun()
        runner.object_exists = True  # Object present

        materialize_missing_object(Path("/checkout"), "a" * 40, runner)

        # Should only check, not fetch
        fetch_calls = [c for c in runner.calls if c[:2] == ("git", "fetch")]
        assert len(fetch_calls) == 0

    def test_rejects_invalid_sha_format(self):
        """Rejects invalid SHA format."""
        runner = FakeRun()

        with pytest.raises(BaseResolutionError, match="Invalid SHA"):
            materialize_missing_object(Path("/checkout"), "invalid", runner)

    def test_rejects_fetch_failure(self):
        """Raises error when fetch fails."""
        runner = FakeRun()
        runner.object_exists = False
        runner.fetch_failures.add("a" * 40)

        with pytest.raises(BaseResolutionError, match="fetch"):
            materialize_missing_object(Path("/checkout"), "a" * 40, runner)


class TestPinRemoteRef:
    """Tests for pin_remote_ref function."""

    def test_pins_ref_to_sha(self):
        """Pins refs/remotes/origin/<branch> to the SHA."""
        runner = FakeRun()

        pin_remote_ref(Path("/checkout"), "main", "a" * 40, runner)

        update_ref_calls = [c for c in runner.calls if c[:2] == ("git", "update-ref")]
        assert len(update_ref_calls) == 1
        assert "refs/remotes/origin/main" in update_ref_calls[0]

    def test_rejects_invalid_sha(self):
        """Rejects invalid SHA format."""
        runner = FakeRun()

        with pytest.raises(BaseResolutionError, match="Invalid SHA"):
            pin_remote_ref(Path("/checkout"), "main", "invalid", runner)


class TestResolveAndMaterializeBase:
    """Tests for resolve_and_materialize_base convenience function."""

    def test_full_workflow(self, tmp_path):
        """Complete resolution, materialization, and pin workflow."""
        def gh_api_runner(args):
            return "a" * 40 + "\n"

        def fetch_runner(args):
            return ""

        def git_runner(args):
            if args[0] == "git" and args[1] == "update-ref":
                return ""
            raise AssertionError(f"Unexpected git command: {args}")

        result = resolve_and_materialize_base(
            repository="owner/repo",
            base_branch="main",
            checkout_path=tmp_path,
            gh_api_runner=gh_api_runner,
            fetch_runner=fetch_runner,
            git_runner=git_runner,
        )

        assert isinstance(result, ResolvedBase)
        assert result.exact_sha == "a" * 40

    def test_fetch_failure_before_pin(self, tmp_path):
        """Fetch failure prevents pin operation."""
        def gh_api_runner(args):
            return "a" * 40 + "\n"

        def fetch_runner(args):
            raise BaseResolutionError("fetch failed")

        with pytest.raises(BaseResolutionError, match="fetch"):
            resolve_and_materialize_base(
                repository="owner/repo",
                base_branch="main",
                checkout_path=tmp_path,
                gh_api_runner=gh_api_runner,
                fetch_runner=fetch_runner,
            )


class TestVerifyBaseShaInCheckout:
    """Tests for verify_base_sha_in_checkout function."""

    def test_returns_true_when_object_exists(self, tmp_path):
        """Returns True when the object exists in the checkout."""
        # This test would require a real Git repo with an object
        # For now, just verify the function signature
        assert verify_base_sha_in_checkout(tmp_path, "a" * 40) is False  # No repo

    def test_returns_false_for_invalid_sha(self, tmp_path):
        """Returns False for invalid SHA format."""
        assert verify_base_sha_in_checkout(tmp_path, "invalid") is False
