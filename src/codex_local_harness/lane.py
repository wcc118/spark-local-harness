"""LM Studio lane health check and GPU lease management."""
from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LaneStatus:
    """Status of an LM Studio lane health check."""
    status: str  # one of: ok, unreachable, model-not-loaded, context-too-small, error
    model_id: str
    loaded_context: int | None
    quantization: str | None
    latency_seconds: float
    checked_at: str  # UTC ISO 8601
    detail: str  # short string, no secrets

    def as_dict(self) -> dict[str, Any]:
        """Return as a dictionary for JSON serialization."""
        return {
            "status": self.status,
            "model_id": self.model_id,
            "loaded_context": self.loaded_context,
            "quantization": self.quantization,
            "latency_seconds": self.latency_seconds,
            "checked_at": self.checked_at,
            "detail": self.detail,
        }


def check_lane(
    base_url: str,
    expected_model: str,
    min_context: int = 0,
    timeout: float = 8.0,
    token: str | None = None,
) -> LaneStatus:
    """
    Check the health of an LM Studio lane.

    Args:
        base_url: Base URL of LM Studio API (e.g., http://host:1234/v1)
        expected_model: The expected model ID to be loaded
        min_context: Minimum required context length (default 0)
        timeout: Request timeout in seconds (default 8.0)
        token: Optional Bearer token for authentication

    Returns:
        LaneStatus with the health check result
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    start_time = time.monotonic()

    # Derive native URL by removing trailing /v1
    native_url = base_url.rstrip("/")
    if native_url.endswith("/v1"):
        native_url = native_url[:-3]

    models_url = f"{native_url}/api/v0/models"

    try:
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(models_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        elapsed = time.monotonic() - start_time

        # Find the expected model in the list
        found_model = None
        loaded_model_id = None
        loaded_context = None
        quantization = None

        for entry in data.get("data", []):
            model_id = entry.get("id")
            state = entry.get("state")
            ctx = entry.get("loaded_context_length")
            q = entry.get("quantization")

            if state == "loaded":
                loaded_model_id = model_id
                loaded_context = ctx
                quantization = q

            if model_id == expected_model:
                found_model = entry

        # Determine status
        if found_model is None:
            # Expected model not found at all
            if loaded_model_id is not None:
                return LaneStatus(
                    status="model-not-loaded",
                    model_id=expected_model,
                    loaded_context=loaded_context,
                    quantization=quantization,
                    latency_seconds=elapsed,
                    checked_at=checked_at,
                    detail=f"loaded model is {loaded_model_id}",
                )
            else:
                return LaneStatus(
                    status="model-not-loaded",
                    model_id=expected_model,
                    loaded_context=None,
                    quantization=None,
                    latency_seconds=elapsed,
                    checked_at=checked_at,
                    detail="no model is currently loaded",
                )

        # Expected model found, check if loaded
        state = found_model.get("state")
        if state != "loaded":
            if loaded_model_id is not None:
                return LaneStatus(
                    status="model-not-loaded",
                    model_id=expected_model,
                    loaded_context=loaded_context,
                    quantization=quantization,
                    latency_seconds=elapsed,
                    checked_at=checked_at,
                    detail=f"expected model not loaded, {loaded_model_id} is loaded",
                )
            else:
                return LaneStatus(
                    status="model-not-loaded",
                    model_id=expected_model,
                    loaded_context=None,
                    quantization=None,
                    latency_seconds=elapsed,
                    checked_at=checked_at,
                    detail="expected model not loaded, no model is loaded",
                )

        # Model is loaded, check context
        ctx = found_model.get("loaded_context_length")
        if ctx is None or ctx < min_context:
            return LaneStatus(
                status="context-too-small",
                model_id=expected_model,
                loaded_context=ctx,
                quantization=found_model.get("quantization"),
                latency_seconds=elapsed,
                checked_at=checked_at,
                detail=f"required {min_context}, got {ctx}",
            )

        return LaneStatus(
            status="ok",
            model_id=expected_model,
            loaded_context=ctx,
            quantization=found_model.get("quantization"),
            latency_seconds=elapsed,
            checked_at=checked_at,
            detail="expected model loaded with sufficient context",
        )

    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - start_time
        return LaneStatus(
            status="error",
            model_id=expected_model,
            loaded_context=None,
            quantization=None,
            latency_seconds=elapsed,
            checked_at=checked_at,
            detail=f"HTTP {exc.code}: {exc.reason}",
        )
    except urllib.error.URLError as exc:
        elapsed = time.monotonic() - start_time
        if isinstance(exc.reason, socket.gaierror):
            return LaneStatus(
                status="unreachable",
                model_id=expected_model,
                loaded_context=None,
                quantization=None,
                latency_seconds=elapsed,
                checked_at=checked_at,
                detail=f"DNS resolution failed: {exc.reason}",
            )
        elif isinstance(exc.reason, ConnectionRefusedError):
            return LaneStatus(
                status="unreachable",
                model_id=expected_model,
                loaded_context=None,
                quantization=None,
                latency_seconds=elapsed,
                checked_at=checked_at,
                detail="connection refused (service down or port wrong)",
            )
        elif isinstance(exc.reason, socket.timeout):
            return LaneStatus(
                status="unreachable",
                model_id=expected_model,
                loaded_context=None,
                quantization=None,
                latency_seconds=elapsed,
                checked_at=checked_at,
                detail="connection timed out",
            )
        else:
            return LaneStatus(
                status="unreachable",
                model_id=expected_model,
                loaded_context=None,
                quantization=None,
                latency_seconds=elapsed,
                checked_at=checked_at,
                detail=f"network error: {exc.reason}",
            )
    except json.JSONDecodeError as exc:
        elapsed = time.monotonic() - start_time
        return LaneStatus(
            status="error",
            model_id=expected_model,
            loaded_context=None,
            quantization=None,
            latency_seconds=elapsed,
            checked_at=checked_at,
            detail=f"malformed JSON response: {exc}",
        )
    except socket.timeout as exc:
        elapsed = time.monotonic() - start_time
        return LaneStatus(
            status="unreachable",
            model_id=expected_model,
            loaded_context=None,
            quantization=None,
            latency_seconds=elapsed,
            checked_at=checked_at,
            detail="connection timed out",
        )
    except Exception as exc:
        elapsed = time.monotonic() - start_time
        return LaneStatus(
            status="error",
            model_id=expected_model,
            loaded_context=None,
            quantization=None,
            latency_seconds=elapsed,
            checked_at=checked_at,
            detail=f"unexpected error: {exc}",
        )


class GpuLease:
    """
    Advisory lease on a GPU lane using fcntl.flock.

    Uses a lock file with a sidecar JSON file to track holder metadata.
    """

    def __init__(self, name: str, purpose: str, lock_dir: Path | None = None):
        """
        Initialize a GPU lease.

        Args:
            name: Lease name (used in filenames)
            purpose: Description of what needs the lease
            lock_dir: Directory for lock files (default: ~/.local/share/codex-local-harness)
        """
        self.name = name
        self.purpose = purpose
        if lock_dir is None:
            lock_dir = Path.home() / ".local" / "share" / "codex-local-harness"
        self.lock_dir = lock_dir
        self.lock_file = lock_dir / f"lane-{name}.lock"
        self.sidecar_file = lock_dir / f"lane-{name}.sidecar.json"
        self._fd: int | None = None
        self._held = False

    def _ensure_lock_dir(self) -> None:
        """Create lock directory if it doesn't exist."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def _get_sidecar_data(self) -> dict[str, Any] | None:
        """Read and parse sidecar file, or return None if not valid."""
        if not self.sidecar_file.exists():
            return None
        try:
            data = json.loads(self.sidecar_file.read_text(encoding="utf-8"))
            # Validate required fields
            if not isinstance(data.get("pid"), int):
                return None
            if not isinstance(data.get("purpose"), str):
                return None
            if not isinstance(data.get("started_at"), str):
                return None
            return data
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def _is_holder_alive(self, data: dict[str, Any]) -> bool:
        """Check if the holder process is still running."""
        import os
        pid = data.get("pid")
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def holder(self) -> dict[str, Any] | None:
        """
        Return the metadata of the current holder, or None if free.

        Returns None if the sidecar is stale (holder process dead).
        """
        self._ensure_lock_dir()
        data = self._get_sidecar_data()
        if data is None:
            return None
        if not self._is_holder_alive(data):
            # Stale data, clear it
            try:
                self.sidecar_file.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return {
            "pid": data["pid"],
            "purpose": data["purpose"],
            "started_at": data["started_at"],
        }

    def acquire(self, timeout: float = 0.0) -> bool:
        """
        Acquire the lease with polling.

        Args:
            timeout: Maximum time to wait in seconds (0 = poll once, None = wait forever)

        Returns:
            True if acquired, False otherwise
        """
        self._ensure_lock_dir()

        if self._held:
            return True

        self._fd = open(self.lock_file, "a+")

        start_time = time.monotonic()
        poll_interval = 0.1

        while True:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd.seek(0)
                self._fd.truncate()
                self._fd.write(f"{os.getpid()}\n")
                self._fd.flush()
                # Successfully acquired, write sidecar
                sidecar_data = {
                    "pid": os.getpid(),
                    "purpose": self.purpose,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                self.sidecar_file.write_text(
                    json.dumps(sidecar_data, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                self._held = True
                return True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    # Unexpected error
                    self._fd.close()
                    self._fd = None
                    return False

                # Check timeout
                if timeout is not None:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= timeout:
                        self._fd.close()
                        self._fd = None
                        return False

                # Poll again
                time.sleep(poll_interval)

    def release(self) -> None:
        """Release the lease and remove sidecar file."""
        if not self._held or self._fd is None:
            return
        # Remove only our sidecar, while still holding the lock. A contender
        # must not erase the active owner's evidence or a successor's sidecar.
        try:
            self.sidecar_file.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()
            self._fd = None
            self._held = False

    def __enter__(self) -> "GpuLease":
        """Context manager entry - acquire with no timeout."""
        if not self.acquire(timeout=None):
            raise RuntimeError(f"Failed to acquire lease {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - release."""
        self.release()
        return False
