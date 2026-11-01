"""Tests for LM Studio lane health check and GPU lease management."""
from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from codex_local_harness.lane import LaneStatus, check_lane, GpuLease


class MockLMStudioHandler(http.server.BaseHTTPRequestHandler):
    """Mock LM Studio API handler for testing."""

    models_data: list[dict] = []  # Class variable shared by all instances

    def log_message(self, format, *args):
        """Suppress logging."""
        pass

    def do_GET(self):
        if self.path == "/api/v0/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"data": self.models_data}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class MockLMStudioHandlerWithAuth(MockLMStudioHandler):
    """Mock handler that requires Bearer token."""

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        expected_token = os.environ.get("TEST_EXPECTED_TOKEN", "secret-token")

        if auth != f"Bearer {expected_token}":
            self.send_response(401)
            self.end_headers()
            return

        super().do_GET()


class MockLMStudioHandlerTimeout(MockLMStudioHandler):
    """Mock handler that hangs on requests."""

    def do_GET(self):
        # Sleep forever to simulate timeout
        time.sleep(3600)


class MockLMStudioHandlerError(MockLMStudioHandler):
    """Mock handler that returns HTTP error."""

    def do_GET(self):
        self.send_response(500)
        self.end_headers()


class MockLMStudioHandlerMalformed(MockLMStudioHandler):
    """Mock handler that returns malformed JSON."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"not valid json {")


def find_free_port() -> int:
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def make_models_response(
    model_id: str, state: str, context: int | None, quantization: str | None = "q8_0"
) -> dict:
    """Create a model entry for the API response."""
    return {
        "id": model_id,
        "state": state,
        "loaded_context_length": context,
        "quantization": quantization,
        "arch": "transformer",
    }


class ServerThread(threading.Thread):
    """Thread that runs an HTTP server for a single test."""

    def __init__(self, handler_class, models_data=None):
        super().__init__(daemon=True)
        self.server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
        self.port = self.server.server_address[1]
        if models_data is not None:
            handler_class.models_data = models_data
        self.started = threading.Event()

    def run(self):
        self.started.set()
        self.server.serve_forever()

    def shutdown_server(self):
        self.server.shutdown()


class TestCheckLane:
    """Tests for check_lane function."""

    def test_redirect_never_reaches_second_endpoint(self):
        received = []

        class Target(MockLMStudioHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                super().do_GET()

        target = ServerThread(Target)
        target.start()

        class Redirect(MockLMStudioHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.port}/api/v0/models")
                self.end_headers()

        source = ServerThread(Redirect)
        source.start()
        try:
            result = check_lane(f"http://127.0.0.1:{source.port}/v1", "model", token="test-only")
            assert result.status == "error"
            assert received == []
        finally:
            for server in (source, target):
                server.shutdown_server()
                server.server.server_close()

    def test_oversized_model_catalogue_is_rejected(self):
        class Large(MockLMStudioHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b" " * (1024 * 1024 + 1))

        server = ServerThread(Large)
        server.start()
        try:
            result = check_lane(f"http://127.0.0.1:{server.port}/v1", "model")
            assert result.status == "error"
            assert "exceeds 1 MiB" in result.detail
        finally:
            server.shutdown_server()
            server.server.server_close()

    @pytest.fixture
    def http_server(self):
        """Fixture to create an HTTP server with a given handler."""
        def _create_server(handler_class, models_data=None):
            server = ServerThread(handler_class, models_data)
            server.start()
            server.started.wait(timeout=2)
            time.sleep(0.1)  # Give server time to fully start
            return server

        return _create_server

    def test_check_lane_ok(self, http_server):
        """Test successful health check."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 65536, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "ok"
        assert result.model_id == "qwen/qwen3.8-27b"
        assert result.loaded_context == 65536
        assert result.quantization == "q8_0"
        assert result.checked_at is not None
        assert "sufficient context" in result.detail
        assert result.latency_seconds > 0

        server.shutdown_server()

    def test_check_lane_unreachable_closed_port(self):
        """Test connection to closed port."""
        # Find a port that's likely closed
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "unreachable"
        assert result.model_id == "qwen/qwen3.8-27b"
        assert result.loaded_context is None
        assert result.checked_at is not None
        assert "connection" in result.detail.lower() or "refused" in result.detail.lower()

    def test_check_lane_model_not_loaded_with_another_loaded(self, http_server):
        """Test when expected model is not loaded but another model is."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "not-loaded", None, "q8_0"),
            make_models_response("mistralai/mistral-7b", "loaded", 32768, "q4_k_m"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "model-not-loaded"
        assert result.model_id == "qwen/qwen3.8-27b"
        assert result.loaded_context == 32768
        assert "mistralai/mistral-7b" in result.detail

        server.shutdown_server()

    def test_check_lane_context_too_small(self, http_server):
        """Test when context is smaller than required."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 16384, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b", min_context=65536)

        assert result.status == "context-too-small"
        assert result.loaded_context == 16384
        assert "required 65536, got 16384" in result.detail

        server.shutdown_server()

    def test_check_lane_malformed_json(self, http_server):
        """Test when API returns malformed JSON."""
        models = []
        server = http_server(MockLMStudioHandlerMalformed, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "error"
        assert "malformed JSON" in result.detail

        server.shutdown_server()

    def test_check_lane_http_error(self, http_server):
        """Test when API returns HTTP error."""
        models = []
        server = http_server(MockLMStudioHandlerError, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "error"
        assert result.loaded_context is None
        assert "HTTP 500" in result.detail

        server.shutdown_server()

    def test_check_lane_token_header_sent(self, http_server):
        """Test that token header is sent only when provided."""
        os.environ["TEST_EXPECTED_TOKEN"] = "secret-token"
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 65536, "q8_0"),
        ]

        server = http_server(MockLMStudioHandlerWithAuth, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        # With token - should succeed
        result = check_lane(
            base_url=base_url,
            expected_model="qwen/qwen3.8-27b",
            token="secret-token",
        )
        assert result.status == "ok"

        # Without token - should fail with 401
        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")
        assert result.status == "error"
        assert "HTTP 401" in result.detail

        server.shutdown_server()

    def test_check_lane_model_not_found_at_all(self, http_server):
        """Test when expected model is not in the models list."""
        models = [
            make_models_response("mistralai/mistral-7b", "loaded", 32768, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "model-not-loaded"
        # Since there's another model loaded, it should be mentioned
        assert "mistralai/mistral-7b" in result.detail

        server.shutdown_server()

    def test_check_lane_no_model_loaded(self, http_server):
        """Test when no model is loaded."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "not-loaded", None, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        result = check_lane(base_url=base_url, expected_model="qwen/qwen3.8-27b")

        assert result.status == "model-not-loaded"
        assert "no model is loaded" in result.detail

        server.shutdown_server()


class TestGpuLease:
    """Tests for GpuLease class."""

    def test_lease_acquire_and_release(self, tmp_path: Path):
        """Test basic lease acquire and release."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="test", purpose="test-purpose", lock_dir=lease_dir)

        assert lease.acquire(timeout=5.0) is True
        assert lease._held is True
        assert lease.holder() is not None
        assert lease.holder()["pid"] == os.getpid()
        assert lease.holder()["purpose"] == "test-purpose"

        lease.release()
        assert lease._held is False
        assert lease.holder() is None
        assert not lease.sidecar_file.exists()

    def test_lease_context_manager(self, tmp_path: Path):
        """Test context manager support."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="test2", purpose="context-manager-test", lock_dir=lease_dir)

        with lease as l:
            assert l._held is True
            holder = l.holder()
            assert holder is not None
            assert holder["pid"] == os.getpid()

        # After context exit, lease should be released
        assert lease._held is False
        assert lease.holder() is None

    def test_lease_holder_metadata(self, tmp_path: Path):
        """Test holder metadata structure."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="metadata", purpose="metadata-test", lock_dir=lease_dir)

        with lease:
            holder = lease.holder()
            assert holder is not None
            assert "pid" in holder
            assert "purpose" in holder
            assert "started_at" in holder
            # Verify started_at is ISO 8601
            from datetime import datetime

            datetime.fromisoformat(holder["started_at"].replace("Z", "+00:00"))

    def test_lease_stale_sidecar_ignored(self, tmp_path: Path):
        """Test that stale sidecar data from dead process is not reported."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="stale", purpose="stale-test", lock_dir=lease_dir)

        # Create a sidecar with a fake dead PID
        lease._ensure_lock_dir()
        fake_data = {
            "pid": 99999999,  # Very unlikely to exist
            "purpose": "stale-pid",
            "started_at": "2025-01-01T00:00:00+00:00",
        }
        lease.sidecar_file.write_text(json.dumps(fake_data))

        # holder() should return None for stale data
        assert lease.holder() is None
        # Sidecar should be cleaned up
        assert not lease.sidecar_file.exists()

    def test_lease_contention_with_timeout(self, tmp_path: Path):
        """Test contention between two processes with timeout."""
        lease_dir = tmp_path / "locks"
        lease1 = GpuLease(name="contention", purpose="holder-1", lock_dir=lease_dir)
        lease2 = GpuLease(name="contention", purpose="holder-2", lock_dir=lease_dir)

        # First process acquires
        assert lease1.acquire(timeout=5.0) is True

        # Second process tries to acquire with short timeout
        result = lease2.acquire(timeout=0.5)
        assert result is False  # Should timeout

        lease1.release()

        # Now second process can acquire
        assert lease2.acquire(timeout=5.0) is True
        lease2.release()

    def test_lease_multiple_calls_to_acquire(self, tmp_path: Path):
        """Test that acquire can be called multiple times on same instance."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="multiple", purpose="multi-test", lock_dir=lease_dir)

        assert lease.acquire(timeout=5.0) is True
        first_holder = lease.holder()

        # Calling acquire again should return True and not change holder
        assert lease.acquire(timeout=5.0) is True
        second_holder = lease.holder()

        assert first_holder is not None
        assert second_holder is not None
        assert first_holder["pid"] == second_holder["pid"]

        lease.release()

    def test_lease_release_on_exception(self, tmp_path: Path):
        """Test that lease is released even when exception occurs."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="exception", purpose="exception-test", lock_dir=lease_dir)

        with pytest.raises(ValueError, match="test error"):
            with lease:
                raise ValueError("test error")

        # After exception, lease should be released
        assert lease._held is False
        assert lease.holder() is None

    def test_lease_acquire_no_timeout(self, tmp_path: Path):
        """Test acquire with no timeout (poll forever)."""
        lease_dir = tmp_path / "locks"
        lease1 = GpuLease(name="no_timeout", purpose="holder-1", lock_dir=lease_dir)
        lease2 = GpuLease(name="no_timeout", purpose="holder-2", lock_dir=lease_dir)

        # First process holds indefinitely (in a thread)
        def hold_forever():
            lease1.acquire(timeout=None)

        thread = threading.Thread(target=hold_forever, daemon=True)
        thread.start()
        time.sleep(0.2)  # Give it time to acquire

        # Second process with very short timeout should fail
        assert lease2.acquire(timeout=0.3) is False

        lease1.release()

    def test_lease_empty_name(self, tmp_path: Path):
        """Test lease with empty name creates valid file path."""
        lease_dir = tmp_path / "locks"
        lease = GpuLease(name="", purpose="empty-name-test", lock_dir=lease_dir)

        assert lease.acquire(timeout=5.0) is True
        assert lease._held is True
        lease.release()

    def test_lease_default_directory(self):
        """Test GpuLease uses default directory when not specified."""
        lease = GpuLease(name="default-dir", purpose="default-dir-test")

        expected_path = Path.home() / ".local" / "share" / "codex-local-harness"
        assert lease.lock_dir == expected_path
        assert str(lease.lock_file).endswith("lane-default-dir.lock")
        assert str(lease.sidecar_file).endswith("lane-default-dir.sidecar.json")


class TestLaneCli:
    """Tests for lane CLI functionality."""

    @pytest.fixture
    def http_server(self):
        """Fixture to create an HTTP server with a given handler."""
        def _create_server(handler_class, models_data=None):
            server = ServerThread(handler_class, models_data)
            server.start()
            server.started.wait(timeout=2)
            time.sleep(0.1)  # Give server time to fully start
            return server

        return _create_server

    def test_health_exit_code_ok(self, http_server, capsys):
        """Test health command exit code for ok status."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 65536, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main(["health", "--base-url", base_url, "--model", "qwen/qwen3.8-27b"])
        assert result == 0

        server.shutdown_server()

    def test_health_exit_code_unreachable(self):
        """Test health command exit code for unreachable status."""
        port = find_free_port()
        base_url = f"http://127.0.0.1:{port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main(["health", "--base-url", base_url, "--model", "qwen/qwen3.8-27b"])
        assert result == 10

    def test_health_exit_code_model_not_loaded(self, http_server):
        """Test health command exit code for model-not-loaded status."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "not-loaded", None, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main(["health", "--base-url", base_url, "--model", "qwen/qwen3.8-27b"])
        assert result == 11

        server.shutdown_server()

    def test_health_exit_code_context_too_small(self, http_server):
        """Test health command exit code for context-too-small status."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 16384, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "health",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "--min-context", "65536",
        ])
        assert result == 12

        server.shutdown_server()

    def test_health_exit_code_error(self, http_server):
        """Test health command exit code for error status."""
        models = []
        server = http_server(MockLMStudioHandlerError, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main(["health", "--base-url", base_url, "--model", "qwen/qwen3.8-27b"])
        assert result == 13

        server.shutdown_server()

    def test_health_json_output(self, http_server, capsys):
        """Test health command JSON output."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 65536, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "health",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "--json",
        ])
        assert result == 0

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["status"] == "ok"
        assert data["model_id"] == "qwen/qwen3.8-27b"
        assert data["loaded_context"] == 65536

        server.shutdown_server()

    def test_lease_status_command(self, capsys):
        """Test lease-status command."""
        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main(["lease-status", "--name", "test-lease"])
        assert result == 0

        output = capsys.readouterr().out
        assert output.strip() == "free"

    def test_run_command_success(self, capsys):
        """Test run command with successful command."""
        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "run",
            "--name", "run-test",
            "--purpose", "test purpose",
            "echo", "hello",
        ])
        # Should return 0 for successful echo
        assert result == 0

    def test_run_command_failure(self, capsys):
        """Test run command with failing command."""
        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "run",
            "--name", "run-fail",
            "--purpose", "test failure",
            "false",
        ])
        # Should return non-zero for false command
        assert result != 0

    def test_run_command_lease_timeout(self, tmp_path: Path, capsys):
        """Test run command with lease timeout."""
        from codex_local_harness.lane_cli import main as cli_main
        from codex_local_harness.lane import GpuLease
        import fcntl

        # Create a lock file in the default directory and hold it
        lease = GpuLease(name="contention", purpose="timeout test")
        lock_path = lease.lock_file

        # Clean up any existing lock
        lock_path.unlink(missing_ok=True)

        fd = open(lock_path, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

        try:
            result = cli_main([
                "run",
                "--name", "contention",
                "--purpose", "timeout test",
                "--lease-timeout", "0.1",
                "echo", "should-not-run",
            ])
            # Should return 75 for lease acquisition failure
            assert result == 75
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
            lock_path.unlink(missing_ok=True)

    def test_guard_command_ok(self, http_server, capsys):
        """Test guard command with healthy lane."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 65536, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "guard",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "echo", "hello",
        ])
        assert result == 0

        server.shutdown_server()

    def test_guard_command_skip(self, http_server, capsys):
        """Test guard command skip (fail-open) with unhealthy lane."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "not-loaded", None, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "guard",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "echo", "should-not-run",
        ])
        # Should return 0 (fail-open)
        assert result == 0

        output = capsys.readouterr()
        assert "LANE-SKIPPED" in output.err
        assert "model-not-loaded" in output.err

        server.shutdown_server()

    def test_guard_command_strict(self, http_server, capsys):
        """Test guard command with --strict flag."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "not-loaded", None, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "guard",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "--strict",
            "echo", "should-not-run",
        ])
        # Should return 11 (model-not-loaded exit code)
        assert result == 11

        server.shutdown_server()

    def test_guard_command_context_too_small(self, http_server, capsys):
        """Test guard command with context too small."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 16384, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "guard",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "--min-context", "65536",
            "echo", "hello",
        ])
        # Should return 0 (fail-open)
        assert result == 0

        output = capsys.readouterr()
        assert "LANE-SKIPPED" in output.err
        assert "context-too-small" in output.err

        server.shutdown_server()

    def test_guard_command_strict_context_too_small(self, http_server, capsys):
        """Test guard command with --strict and context too small."""
        models = [
            make_models_response("qwen/qwen3.8-27b", "loaded", 16384, "q8_0"),
        ]

        server = http_server(MockLMStudioHandler, models)
        base_url = f"http://127.0.0.1:{server.port}/v1"

        from codex_local_harness.lane_cli import main as cli_main

        result = cli_main([
            "guard",
            "--base-url", base_url,
            "--model", "qwen/qwen3.8-27b",
            "--min-context", "65536",
            "--strict",
            "echo", "hello",
        ])
        # Should return 12 (context-too-small exit code)
        assert result == 12

        server.shutdown_server()


def test_unusable_lease_directory_fails_closed(tmp_path):
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('existing file')
    lease = GpuLease('test', 'admission', blocked)
    assert lease.acquire(timeout=0) is False
    lease.release()
    assert blocked.read_text() == 'existing file'
