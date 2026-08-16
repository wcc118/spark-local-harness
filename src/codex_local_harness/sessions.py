from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, _safe_atom, _timestamp

# Security: bounded identifiers for session_id with GitHub validation
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SAFE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@+\-]{0,255}\Z")


def _validate_github_repo(value: str | None, name: str) -> None:
    """Validate GitHub repository format: OWNER/REPO"""
    if value is not None:
        if not value or not value.strip():
            raise ValueError(f"{name} must be non-empty")
        if not GITHUB_REPO_PATTERN.fullmatch(value):
            raise ValueError(f"{name} must be a valid GitHub OWNER/REPO identifier")


def _validate_session_id(value: str | None, name: str) -> None:
    """Validate session identifier"""
    if value is not None:
        if not value or not value.strip():
            raise ValueError(f"{name} must be non-empty")
        if not SAFE_ATOM.fullmatch(value):
            raise ValueError(f"{name} must be a bounded identifier")


def _validate_github_issue(value: int | None, name: str) -> None:
    """Validate GitHub issue number"""
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_issue(value: int | None, name: str) -> None:
    """Validate internal issue number"""
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class SessionIdentity:
    """Workflow identity with optional session_id and repository defaults"""
    issue: int
    session_id: str | None = None
    repository: str | None = None
    pull_request: int | None = None
    environment: str | None = None
    observed_at: str | None = None
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_issue(self.issue, "issue")
        if self.session_id is not None:
            _validate_session_id(self.session_id, "session_id")
        _validate_github_repo(self.repository, "repository")
        if self.pull_request is not None and self.pull_request <= 0:
            raise ValueError("pull_request must be positive")
        if self.environment is not None:
            _safe_atom(self.environment, "environment")
        if self.observed_at is not None:
            _timestamp(self.observed_at)
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionIdentity:
        return cls(**payload)

    @classmethod
    def with_defaults(
        cls,
        issue: int,
        *,
        session_id: str | None = None,
        repository: str | None = None,
        pull_request: int | None = None,
        environment: str | None = None,
    ) -> SessionIdentity:
        """Create session identity with environment defaults"""
        return cls(
            issue=issue,
            session_id=session_id,
            repository=repository,
            pull_request=pull_request,
            environment=environment,
        )


@dataclass(frozen=True)
class SessionUsage:
    """Privacy-minimal operation-usage model for token tracking"""
    session_id: str
    issue: int
    repository: str | None = None
    pull_request: int | None = None
    start_tokens: int | None = None
    finish_tokens: int | None = None
    delta: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    explicit_provenance: str | None = None
    captured_at: str | None = None
    interrupted: bool = False
    missing: bool = False
    reset: bool = False
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.session_id, "session_id")
        _validate_issue(self.issue, "issue")
        _validate_github_repo(self.repository, "repository")
        if self.pull_request is not None and self.pull_request <= 0:
            raise ValueError("pull_request must be positive")
        if self.start_tokens is not None and self.start_tokens < 0:
            raise ValueError("start_tokens must be non-negative")
        if self.finish_tokens is not None and self.finish_tokens < 0:
            raise ValueError("finish_tokens must be non-negative")
        if self.delta is not None and self.delta < 0:
            raise ValueError("delta must be non-negative")
        if self.cache_read is not None and self.cache_read < 0:
            raise ValueError("cache_read must be non-negative")
        if self.cache_write is not None and self.cache_write < 0:
            raise ValueError("cache_write must be non-negative")
        if self.explicit_provenance is not None:
            _safe_atom(self.explicit_provenance, "explicit_provenance")
        if self.captured_at is not None:
            _timestamp(self.captured_at)
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionUsage:
        return cls(**payload)

    @classmethod
    def from_single_value(
        cls,
        session_id: str,
        issue: int,
        value: int,
        *,
        repository: str | None = None,
        pull_request: int | None = None,
        provenance: str = "single-value",
        captured_at: str | None = None,
    ) -> SessionUsage:
        """Create usage record from a single token count value"""
        return cls(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            start_tokens=value,
            finish_tokens=value,
            delta=0,
            explicit_provenance=provenance,
            captured_at=captured_at or datetime.now().astimezone().isoformat(),
        )

    @classmethod
    def from_pair(
        cls,
        session_id: str,
        issue: int,
        start_tokens: int,
        finish_tokens: int,
        *,
        repository: str | None = None,
        pull_request: int | None = None,
        cache_read: int | None = None,
        cache_write: int | None = None,
        captured_at: str | None = None,
    ) -> SessionUsage:
        """Create usage record from start/finish token pair"""
        delta = max(0, finish_tokens - start_tokens)
        return cls(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            start_tokens=start_tokens,
            finish_tokens=finish_tokens,
            delta=delta,
            cache_read=cache_read,
            cache_write=cache_write,
            explicit_provenance="start-finish-delta",
            captured_at=captured_at or datetime.now().astimezone().isoformat(),
        )

    @classmethod
    def interrupted_session(
        cls,
        session_id: str,
        issue: int,
        *,
        repository: str | None = None,
        pull_request: int | None = None,
    ) -> SessionUsage:
        """Create usage record for interrupted session"""
        return cls(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            start_tokens=None,
            finish_tokens=None,
            delta=None,
            interrupted=True,
            explicit_provenance="interrupted-record",
        )

    @classmethod
    def missing_session(
        cls,
        session_id: str,
        issue: int,
        *,
        repository: str | None = None,
        pull_request: int | None = None,
    ) -> SessionUsage:
        """Create usage record for missing session (no data available)"""
        return cls(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            start_tokens=None,
            finish_tokens=None,
            delta=None,
            missing=True,
            explicit_provenance="missing-session",
        )

    @classmethod
    def reset_session(
        cls,
        session_id: str,
        issue: int,
        *,
        repository: str | None = None,
        pull_request: int | None = None,
    ) -> SessionUsage:
        """Create usage record for reset session (tokens reset to zero)"""
        return cls(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            start_tokens=0,
            finish_tokens=0,
            delta=0,
            reset=True,
            explicit_provenance="reset-record",
        )


@dataclass(frozen=True)
class SessionSummary:
    """Aggregated session summary for dashboard display"""
    session_id: str
    issue: int
    repository: str | None = None
    pull_request: int | None = None
    environment: str | None = None
    total_tokens: int | None = None
    local_tokens: int | None = None
    frontier_tokens: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    operation_count: int = 0
    last_activity: str | None = None
    has_concurrent: bool = False
    is_interrupted: bool = False
    is_missing: bool = False
    is_reset: bool = False
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.session_id, "session_id")
        _validate_issue(self.issue, "issue")
        _validate_github_repo(self.repository, "repository")
        if self.pull_request is not None and self.pull_request <= 0:
            raise ValueError("pull_request must be positive")
        if self.environment is not None:
            _safe_atom(self.environment, "environment")
        if self.total_tokens is not None and self.total_tokens < 0:
            raise ValueError("total_tokens must be non-negative")
        if self.local_tokens is not None and self.local_tokens < 0:
            raise ValueError("local_tokens must be non-negative")
        if self.frontier_tokens is not None and self.frontier_tokens < 0:
            raise ValueError("frontier_tokens must be non-negative")
        if self.cache_read is not None and self.cache_read < 0:
            raise ValueError("cache_read must be non-negative")
        if self.cache_write is not None and self.cache_write < 0:
            raise ValueError("cache_write must be non-negative")
        if self.operation_count < 0:
            raise ValueError("operation_count must be non-negative")
        if self.last_activity is not None:
            _timestamp(self.last_activity)
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionSummary:
        return cls(**payload)


def aggregate_sessions(
    identities: Iterable[SessionIdentity],
    usages: Iterable[SessionUsage],
) -> tuple[SessionSummary, ...]:
    """Tuple-based aggregation for concurrent sessions sharing an issue number"""
    usage_map: dict[str, list[SessionUsage]] = {}
    for usage in usages:
        usage_map.setdefault(usage.session_id, []).append(usage)

    identity_map: dict[str, SessionIdentity | None] = {}
    for identity in identities:
        identity_map[identity.session_id] = identity

    summaries: list[SessionSummary] = []
    for session_id, usages_list in usage_map.items():
        # Aggregate usage data
        start_tokens_list = [u.start_tokens for u in usages_list if u.start_tokens is not None]
        finish_tokens_list = [u.finish_tokens for u in usages_list if u.finish_tokens is not None]
        cache_read_list = [u.cache_read for u in usages_list if u.cache_read is not None]
        cache_write_list = [u.cache_write for u in usages_list if u.cache_write is not None]
        delta_list = [u.delta for u in usages_list if u.delta is not None]

        total_tokens = None
        local_tokens = None
        frontier_tokens = None
        if start_tokens_list or finish_tokens_list:
            # Use the most recent values
            all_tokens = start_tokens_list + finish_tokens_list
            total_tokens = max(all_tokens) if all_tokens else None
            local_tokens = total_tokens
        if cache_read_list:
            cache_read = sum(cache_read_list)
        else:
            cache_read = None
        if cache_write_list:
            cache_write = sum(cache_write_list)
        else:
            cache_write = None

        # Detect session states
        is_interrupted = any(u.interrupted for u in usages_list)
        is_missing = any(u.missing for u in usages_list)
        is_reset = any(u.reset for u in usages_list)

        # Get identity info
        identity = identity_map.get(session_id)
        if identity:
            issue = identity.issue
            repository = identity.repository
            pull_request = identity.pull_request
            environment = identity.environment
        else:
            issue = usages_list[0].issue if usages_list else 0
            repository = usages_list[0].repository if usages_list and usages_list[0].repository else None
            pull_request = usages_list[0].pull_request if usages_list else None
            environment = None

        # Check for concurrent sessions with same issue
        has_concurrent = False
        for sid, us in usage_map.items():
            if sid != session_id and us[0].issue == issue:
                has_concurrent = True
                break

        # Get last activity timestamp
        last_activity = None
        for u in usages_list:
            if u.captured_at and (last_activity is None or u.captured_at > last_activity):
                last_activity = u.captured_at

        summary = SessionSummary(
            session_id=session_id,
            issue=issue,
            repository=repository,
            pull_request=pull_request,
            environment=environment,
            total_tokens=total_tokens,
            local_tokens=local_tokens,
            frontier_tokens=frontier_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
            operation_count=len(usages_list),
            last_activity=last_activity,
            has_concurrent=has_concurrent,
            is_interrupted=is_interrupted,
            is_missing=is_missing,
            is_reset=is_reset,
        )
        summaries.append(summary)

    return tuple(sorted(summaries, key=lambda s: (s.issue, s.session_id)))


def filter_lines_by_token_discriminator(log_content: str) -> list[str]:
    """Filter lines by literal token-count discriminator before JSON parsing"""
    # Look for lines containing "tokens" or "token" in context
    filtered_lines = []
    for line in log_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Check for token-related patterns
        if any(pattern in stripped.lower() for pattern in (
            '"tokens"', "'tokens'",
            'tokens:', 'tokens =',
            'input_tokens', 'output_tokens', 'reasoning_tokens',
        )):
            filtered_lines.append(stripped)
    return filtered_lines


def parse_token_count_from_line(line: str) -> int | None:
    """Parse numeric token count from a filtered line, returning None if unsupported"""
    # Try various patterns for extracting token counts
    import re
    
    # Pattern 1: JSON format with tokens field
    json_match = re.search(r'"tokens"\s*:\s*(\d+)', line)
    if json_match:
        return int(json_match.group(1))
    
    # Pattern 2: tokens = 123 or tokens: 123
    eq_match = re.search(r'tokens\s*[=:]\s*(\d+)', line)
    if eq_match:
        return int(eq_match.group(1))
    
    # Pattern 3: input_tokens=123, output_tokens=456, etc.
    input_match = re.search(r'input_tokens\s*[=:]\s*(\d+)', line)
    output_match = re.search(r'output_tokens\s*[=:]\s*(\d+)', line)
    reasoning_match = re.search(r'reasoning_tokens\s*[=:]\s*(\d+)', line)
    
    if input_match:
        try:
            return int(input_match.group(1))
        except ValueError:
            pass
    
    return None


def derive_delta(start: int | None, finish: int | None) -> int | None:
    """Derive safe delta between start and finish tokens"""
    if start is None or finish is None:
        return None
    # Handle reset cases where finish might be less than start
    if finish < start:
        # Reset to zero case
        if finish == 0:
            return start  # Complete usage from reset
        return None  # Something unexpected happened
    return finish - start


def read_token_usage_from_file(path: Path) -> tuple[SessionUsage, ...]:
    """Read and parse token usage from a rolling file, handling various edge cases"""
    if not path.exists():
        return ()
    
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    
    # Filter lines by token-count discriminator
    filtered_lines = filter_lines_by_token_discriminator(content)
    
    usages: list[SessionUsage] = []
    
    # Look for session_id and token patterns in the content
    # This is a simple parser - in practice, this would be more sophisticated
    
    # Detect if file contains single values or pairs
    has_start_finish = any(
        'start_tokens' in line.lower() or 'finish_tokens' in line.lower()
        for line in filtered_lines
    )
    
    # Simple line-by-line parsing
    for line in filtered_lines:
        if not line.strip():
            continue
        
        # Try to parse as JSON first
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                session_id = data.get('session_id', data.get('thread_id', data.get('id', 'unknown')))
                issue = data.get('issue', 0)
                
                # Extract token values
                start_tokens = data.get('start_tokens') or data.get('tokens', data.get('input_tokens'))
                finish_tokens = data.get('finish_tokens') or data.get('total_tokens')
                delta = data.get('delta')
                
                # Handle zero vs unsupported
                if start_tokens == 0 and 'start_tokens' not in data:
                    start_tokens = None
                if finish_tokens == 0 and 'finish_tokens' not in data:
                    finish_tokens = None
                    
                usage = SessionUsage(
                    session_id=str(session_id),
                    issue=issue,
                    start_tokens=start_tokens,
                    finish_tokens=finish_tokens,
                    delta=delta,
                    explicit_provenance="rolling-file-parsed",
                    captured_at=data.get('captured_at'),
                )
                usages.append(usage)
                continue
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        
        # Try parsing as key=value format
        tokens_match = re.search(r'(session_id|thread_id|id)=(\S+)', line)
        start_match = re.search(r'start_tokens?=(\d+)', line)
        finish_match = re.search(r'finish_tokens?=(\d+)', line)
        
        if tokens_match:
            session_id = tokens_match.group(2)
            start = int(start_match.group(1)) if start_match else None
            finish = int(finish_match.group(1)) if finish_match else None
            usage = SessionUsage(
                session_id=session_id,
                issue=1,  # Default issue, should be enhanced
                start_tokens=start,
                finish_tokens=finish,
                delta=derive_delta(start, finish),
                explicit_provenance="rolling-file-parsed",
            )
            usages.append(usage)
    
    return tuple(usages)


def append_usage(path: Path, usage: SessionUsage) -> bool:
    """Append one session usage record. Allows multiple records per session_id for snapshots."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(usage.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    
    with path.open("a+", encoding="utf-8") as handle:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0, 2)  # Seek to end for append
        handle.write(encoded)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def read_usages(path: Path) -> tuple[SessionUsage, ...]:
    """Read all session usage records from a file"""
    if not path.exists():
        return ()
    usages: list[SessionUsage] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("record is not an object")
            usages.append(SessionUsage.from_dict(payload))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid session usage record at line {line_number}: {exc}") from exc
    return tuple(usages)
