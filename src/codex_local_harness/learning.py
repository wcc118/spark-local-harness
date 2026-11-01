"""Durable assignment ledger for issue-bound worker dispatch."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PLAN_VERSION = 1


def validate_repository(repo: str) -> tuple[str, str]:
    """Validate repository format and return (owner, name)."""
    if not repo or "/" not in repo:
        raise ValueError("repository must be in OWNER/REPO format")
    parts = repo.split("/", 1)
    owner, name = parts[0], parts[1]
    if not owner.strip() or not name.strip():
        raise ValueError("repository must be in OWNER/REPO format")
    return owner, name


def validate_positive_int(value: Any) -> int:
    """Validate that value is a positive integer (not bool)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("issue must be a positive integer")
    if value <= 0:
        raise ValueError("issue must be positive")
    return value


def validate_nonempty_string(value: Any, name: str) -> str:
    """Validate that value is a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _count_prior_eligible(records: tuple[dict[str, Any], ...]) -> int:
    """Count prior eligible assignments globally in the ledger."""
    count = 0
    for record in records:
        if record.get("decision") == "eligible":
            count += 1
    return count


def _get_models(config: dict[str, Any]) -> tuple[str, str]:
    """Get standard and deepseek model names from config."""
    routes = config.get("routes", {})
    standard_model = routes.get("coder/standard", {}).get("model")
    deepseek_model = routes.get("coder/deepseek", {}).get("model")

    if not standard_model or not isinstance(standard_model, str) or not standard_model.strip():
        raise ValueError("config.routes['coder/standard'].model must be a non-empty string")
    if not deepseek_model or not isinstance(deepseek_model, str) or not deepseek_model.strip():
        raise ValueError("config.routes['coder/deepseek'].model must be a non-empty string")
    if standard_model == deepseek_model:
        raise ValueError("standard and deepseek models must be distinct")

    return standard_model.strip(), deepseek_model.strip()


def _build_role_env(sequence: int, standard_model: str, deepseek_model: str, is_exception: bool) -> dict[str, str] | None:
    """Build role_env dict for the assignment."""
    if is_exception:
        return None

    if sequence % 2 == 0:
        # Even: coder=standard, reviewer=deepseek
        return {
            "CODEX_LOCAL_TIER": "standard",
            "CODEX_LOCAL_MODEL": standard_model,
            "CODEX_LOCAL_REVIEWER_TIER": "standard",
            "CODEX_LOCAL_REVIEWER_MODEL": deepseek_model,
        }
    else:
        # Odd: coder=deepseek, reviewer=standard
        return {
            "CODEX_LOCAL_TIER": "deepseek",
            "CODEX_LOCAL_MODEL": deepseek_model,
            "CODEX_LOCAL_REVIEWER_TIER": "standard",
            "CODEX_LOCAL_REVIEWER_MODEL": standard_model,
        }


def _build_assignment_record(
    repository: str,
    issue: int,
    task_class: str,
    decision: str,
    reason: str,
    sequence: int,
    standard_model: str,
    deepseek_model: str,
    is_exception: bool,
) -> dict[str, Any]:
    """Build the assignment record dict."""
    now = datetime.now(timezone.utc).isoformat()

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_version": PLAN_VERSION,
        "repository": repository,
        "issue": issue,
        "task_class": task_class,
        "decision": decision,
        "reason": reason,
        "observed_at": now,
    }

    if is_exception:
        record["sequence"] = None
        record["coder_model"] = None
        record["reviewer_model"] = None
        record["role_env"] = None
    else:
        record["sequence"] = sequence
        record["coder_model"] = deepseek_model if sequence % 2 == 1 else standard_model
        record["reviewer_model"] = deepseek_model if sequence % 2 == 0 else standard_model
        record["role_env"] = _build_role_env(sequence, standard_model, deepseek_model, False)

    return record


def assign(
    path: Path,
    config: dict[str, Any],
    repository: str,
    issue: int,
    task_class: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Assign a worker to an issue and record it in the ledger.

    Returns the recorded assignment dict.
    """
    # Validate inputs
    validate_nonempty_string(repository, "repository")
    validate_nonempty_string(task_class, "task_class")
    validate_nonempty_string(reason, "reason")
    validate_positive_int(issue)
    validate_repository(repository)

    if decision not in ("eligible", "exception"):
        raise ValueError("decision must be exactly 'eligible' or 'exception'")

    is_exception = decision == "exception"

    # Get models from config (raises if unavailable)
    standard_model, deepseek_model = _get_models(config)

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Read existing records with shared lock
    existing_records: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        existing_records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # Check for existing record for this repository/issue
    matching_records = [
        r for r in existing_records
        if r.get("repository") == repository and r.get("issue") == issue
    ]

    if matching_records:
        # Check if it's an exact repeat of eligible
        for record in matching_records:
            if (record.get("decision") == decision and
                record.get("task_class") == task_class and
                record.get("reason") == reason):
                # Exact repeat - return existing record (even if it's exception, we don't overwrite)
                # But per spec, repeat eligible returns stored record
                if decision == "eligible":
                    return record

        # Conflicting assignment exists
        raise ValueError(
            f"conflicting assignment exists for {repository}/{issue}: "
            f"existing decision={matching_records[0].get('decision')}, "
            f"task_class={matching_records[0].get('task_class')}"
        )

    # Count prior eligible assignments for sequence
    sequence = _count_prior_eligible(tuple(existing_records))

    # Build new record
    record = _build_assignment_record(
        repository=repository,
        issue=issue,
        task_class=task_class,
        decision=decision,
        reason=reason,
        sequence=sequence,
        standard_model=standard_model,
        deepseek_model=deepseek_model,
        is_exception=is_exception,
    )

    # Append with exclusive lock - need to open for read+write to allow both reading and appending
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            # Read current content to check for race conditions
            handle.seek(0)
            current_content = handle.read()

            # Re-parse to check for concurrent additions
            concurrent_records: list[dict[str, Any]] = []
            for line_number, line in enumerate(current_content.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    concurrent_records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc

            # Check for conflicts after concurrent additions
            matching_after_concurrent = [
                r for r in concurrent_records
                if r.get("repository") == repository and r.get("issue") == issue
            ]

            if matching_after_concurrent:
                raise ValueError(
                    f"conflicting assignment added concurrently for {repository}/{issue}"
                )

            # Write new record at end
            handle.seek(0, 2)  # End of file
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # Set file permissions
    os.chmod(path, 0o600)

    return record


def list_assignments(path: Path) -> tuple[dict[str, Any], ...]:
    """List all assignment records from the ledger."""
    if not path.exists():
        return ()

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return tuple(records)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage issue-bound worker assignments in a durable ledger"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # assign command
    assign_parser = subparsers.add_parser("assign", help="Record a new assignment")
    assign_parser.add_argument("--ledger", type=Path, required=True, help="Path to JSONL ledger file")
    assign_parser.add_argument("--config", type=Path, required=True, help="Path to worker config JSON file")
    assign_parser.add_argument("--repository", required=True, help="Repository in OWNER/REPO format")
    assign_parser.add_argument("--issue", type=int, required=True, help="Issue number")
    assign_parser.add_argument("--task-class", required=True, help="Task classification string")
    assign_parser.add_argument("--decision", required=True, choices=["eligible", "exception"], help="Assignment decision")
    assign_parser.add_argument("--reason", required=True, help="Reason for the assignment")

    # list command
    list_parser = subparsers.add_parser("list", help="List all assignment records")
    list_parser.add_argument("--ledger", type=Path, required=True, help="Path to JSONL ledger file")

    args = parser.parse_args(argv)

    try:
        if args.command == "assign":
            # Load config
            if not args.config.exists():
                raise ValueError(f"config file not found: {args.config}")
            config_content = args.config.read_text(encoding="utf-8")
            config = json.loads(config_content)

            result = assign(
                path=args.ledger,
                config=config,
                repository=args.repository,
                issue=args.issue,
                task_class=args.task_class,
                decision=args.decision,
                reason=args.reason,
            )
            print(json.dumps(result, sort_keys=True))
            return 0

        elif args.command == "list":
            records = list_assignments(args.ledger)
            for record in records:
                print(json.dumps(record, sort_keys=True))
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
