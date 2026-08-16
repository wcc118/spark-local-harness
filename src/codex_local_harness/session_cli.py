from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from .sessions import (
    SessionIdentity,
    SessionUsage,
    append_usage,
    read_usages,
    aggregate_sessions,
    filter_lines_by_token_discriminator,
    parse_token_count_from_line,
    derive_delta,
)
from .diagnostics import append_record
from .health import RunHealth, TokenUsage, append_jsonl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture and manage session identity and token usage for Codex Local Harness"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Identity commands
    identity_parser = subparsers.add_parser("identity", help="Manage session identity")
    identity_subparsers = identity_parser.add_subparsers(dest="kind", required=True)

    identity_record = identity_subparsers.add_parser("record", help="Record session identity")
    identity_record.add_argument("--records", type=Path, required=True)
    identity_record.add_argument("--issue", type=int, required=True)
    identity_record.add_argument("--session-id", help="Session identifier (optional, derived from CODEX_THREAD_ID)")
    identity_record.add_argument("--repository", help="GitHub repository OWNER/REPO (optional)")
    identity_record.add_argument("--pull-request", type=int, help="GitHub pull request number (optional)")
    identity_record.add_argument("--environment", help="Environment name (optional)")
    identity_record.add_argument("--observed-at", help="ISO-8601 timestamp (optional)")

    identity_lookup = identity_subparsers.add_parser("lookup", help="Look up session identity from environment")
    identity_lookup.add_argument("--issue", type=int, required=True)
    identity_lookup.add_argument("--session-id-env", default="CODEX_THREAD_ID", help="Environment variable for session ID")
    identity_lookup.add_argument("--repository-env", default="CODEX_LOCAL_REPOSITORY", help="Environment variable for repository")
    identity_lookup.add_argument("--environment-env", default="CODEX_LOCAL_ENV", help="Environment variable for environment")

    identity_aggregate = identity_subparsers.add_parser("aggregate", help="Aggregate session data from usages")
    identity_aggregate.add_argument("--identity-records", type=Path, required=True)
    identity_aggregate.add_argument("--usage-records", type=Path, required=True)
    identity_aggregate.add_argument("--output", type=Path, required=True)

    # Usage commands
    usage_parser = subparsers.add_parser("usage", help="Manage session usage")
    usage_subparsers = usage_parser.add_subparsers(dest="kind", required=True)

    usage_record = usage_subparsers.add_parser("record", help="Record session usage")
    usage_record.add_argument("--records", type=Path, required=True)
    usage_record.add_argument("--session-id", required=True)
    usage_record.add_argument("--issue", type=int, required=True)
    usage_record.add_argument("--start-tokens", type=int, help="Starting token count")
    usage_record.add_argument("--finish-tokens", type=int, help="Finishing token count")
    usage_record.add_argument("--delta", type=int, help="Token delta")
    usage_record.add_argument("--repository", help="GitHub repository")
    usage_record.add_argument("--pull-request", type=int)
    usage_record.add_argument("--cache-read", type=int, help="Cache read tokens")
    usage_record.add_argument("--cache-write", type=int, help="Cache write tokens")
    usage_record.add_argument("--explicit-provenance", help="Source of this usage data")
    usage_record.add_argument("--captured-at", help="ISO-8601 timestamp")
    usage_record.add_argument("--interrupted", action="store_true")
    usage_record.add_argument("--missing", action="store_true")
    usage_record.add_argument("--reset", action="store_true")

    usage_from_file = usage_subparsers.add_parser("from-file", help="Parse token usage from rolling file")
    usage_from_file.add_argument("--records", type=Path, required=True)
    usage_from_file.add_argument("--file", type=Path, required=True, help="Rolling file to parse")
    usage_from_file.add_argument("--session-id", help="Session ID override")
    usage_from_file.add_argument("--issue", type=int, required=True)

    # Token capture commands
    token_parser = subparsers.add_parser("token", help="Capture Codex token snapshots")
    token_subparsers = token_parser.add_subparsers(dest="kind", required=True)

    token_record = token_subparsers.add_parser("record", help="Record token snapshot from CODEX_THREAD_ID file")
    token_record.add_argument("--records", type=Path, required=True)
    token_record.add_argument("--thread-id-file", type=Path, required=True, help="CODEX_THREAD_ID rollout file")
    token_record.add_argument("--session-id", required=True)
    token_record.add_argument("--issue", type=int, required=True)
    token_record.add_argument("--repository", help="GitHub repository")
    token_record.add_argument("--pull-request", type=int)
    token_record.add_argument("--discriminator", default="tokens", help="Token-count discriminator pattern")

    token_delta = token_subparsers.add_parser("delta", help="Calculate token delta between snapshots")
    token_delta.add_argument("--start-record", type=Path, required=True)
    token_delta.add_argument("--finish-record", type=Path, required=True)
    token_delta.add_argument("--output-format", choices=("json", "text"), default="json")

    # Start/finish boundary commands
    boundary_parser = subparsers.add_parser("boundary", help="Record explicit start/finish boundaries")
    boundary_subparsers = boundary_parser.add_subparsers(dest="kind", required=True)

    boundary_start = boundary_subparsers.add_parser("start", help="Record start boundary")
    boundary_start.add_argument("--records", type=Path, required=True)
    boundary_start.add_argument("--session-id", required=True)
    boundary_start.add_argument("--issue", type=int, required=True)
    boundary_start.add_argument("--repository", help="GitHub repository")
    boundary_start.add_argument("--pull-request", type=int)
    boundary_start.add_argument("--tokens", type=int, required=True)
    boundary_start.add_argument("--timestamp", help="ISO-8601 timestamp")

    boundary_finish = boundary_subparsers.add_parser("finish", help="Record finish boundary")
    boundary_finish.add_argument("--records", type=Path, required=True)
    boundary_finish.add_argument("--session-id", required=True)
    boundary_finish.add_argument("--issue", type=int, required=True)
    boundary_finish.add_argument("--start-record", type=Path, required=True, help="Start boundary record")
    boundary_finish.add_argument("--repository", help="GitHub repository")
    boundary_finish.add_argument("--pull-request", type=int)
    boundary_finish.add_argument("--tokens", type=int, required=True)
    boundary_finish.add_argument("--timestamp", help="ISO-8601 timestamp")

    args = parser.parse_args(argv)

    try:
        if args.command == "identity":
            _handle_identity_command(args)
        elif args.command == "usage":
            _handle_usage_command(args)
        elif args.command == "token":
            _handle_token_command(args)
        elif args.command == "boundary":
            _handle_boundary_command(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    return 0


def _handle_identity_command(args: argparse.Namespace) -> None:
    """Handle identity subcommands"""
    if args.kind == "record":
        identity = SessionIdentity(
            issue=args.issue,
            session_id=args.session_id,
            repository=args.repository,
            pull_request=args.pull_request,
            environment=args.environment,
            observed_at=args.observed_at,
        )
        # Write to identity records
        path = args.records
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        identity_name = "session_id" if identity.session_id else "issue"
        identity_value = identity.session_id or str(identity.issue)
        
        # Simple append (no dedup for identities yet)
        with path.open("a+", encoding="utf-8") as handle:
            handle.seek(0, 2)
            handle.write(encoded)
            handle.flush()
        
        print(json.dumps(identity.as_dict(), sort_keys=True))
    
    elif args.kind == "lookup":
        # Look up values from environment with defaults
        session_id = os.environ.get(args.session_id_env)
        repository = os.environ.get(args.repository_env)
        environment = os.environ.get(args.environment_env)
        
        identity = SessionIdentity(
            issue=args.issue,
            session_id=session_id,
            repository=repository,
            environment=environment,
        )
        print(json.dumps(identity.as_dict(), sort_keys=True))
    
    elif args.kind == "aggregate":
        # Aggregate sessions from identity and usage records
        identities = tuple(SessionIdentity.from_dict(json.loads(line)) 
                         for line in args.identity_records.read_text().splitlines() if line.strip())
        usages = tuple(SessionUsage.from_dict(json.loads(line)) 
                       for line in args.usage_records.read_text().splitlines() if line.strip())
        
        summaries = aggregate_sessions(identities, usages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "\n".join(json.dumps(s.as_dict(), sort_keys=True) for s in summaries) + "\n",
            encoding="utf-8"
        )
        print(f"Aggregated {len(summaries)} session summaries")


def _handle_usage_command(args: argparse.Namespace) -> None:
    """Handle usage subcommands"""
    if args.kind == "record":
        if sum([args.interrupted, args.missing, args.reset]) > 1:
            raise ValueError("Only one of interrupted, missing, or reset can be set")
        
        if args.delta is None and args.start_tokens is not None and args.finish_tokens is not None:
            args.delta = max(0, args.finish_tokens - args.start_tokens)
        
        usage = SessionUsage(
            session_id=args.session_id,
            issue=args.issue,
            repository=args.repository,
            pull_request=args.pull_request,
            start_tokens=args.start_tokens,
            finish_tokens=args.finish_tokens,
            delta=args.delta,
            cache_read=args.cache_read,
            cache_write=args.cache_write,
            explicit_provenance=args.explicit_provenance,
            captured_at=args.captured_at,
            interrupted=args.interrupted,
            missing=args.missing,
            reset=args.reset,
        )
        append_usage(args.records, usage)
        print(json.dumps(usage.as_dict(), sort_keys=True))
    
    elif args.kind == "from-file":
        usages = read_usages(args.file)
        for usage in usages:
            # Override session_id and issue if provided
            updated_usage = SessionUsage(
                session_id=args.session_id or usage.session_id,
                issue=args.issue or usage.issue,
                repository=usage.repository,
                pull_request=usage.pull_request,
                start_tokens=usage.start_tokens,
                finish_tokens=usage.finish_tokens,
                delta=usage.delta,
                cache_read=usage.cache_read,
                cache_write=usage.cache_write,
                explicit_provenance=usage.explicit_provenance or "file-parsed",
                captured_at=usage.captured_at,
                interrupted=usage.interrupted,
                missing=usage.missing,
                reset=usage.reset,
            )
            append_usage(args.records, updated_usage)
        print(f"Parsed {len(usages)} usage records from file")


def _handle_token_command(args: argparse.Namespace) -> None:
    """Handle token capture subcommands"""
    if args.kind == "record":
        # Read the CODEX_THREAD_ID rollout file
        if not args.thread_id_file.exists():
            raise ValueError(f"Thread ID file not found: {args.thread_id_file}")
        
        content = args.thread_id_file.read_text(encoding="utf-8")
        
        # Filter lines by token-count discriminator
        filtered_lines = filter_lines_by_token_discriminator(content)
        
        if not filtered_lines:
            raise ValueError("No token-count lines found in thread ID file")
        
        # Parse token counts (sum all found values for cumulative snapshot)
        token_values = []
        for line in filtered_lines:
            value = parse_token_count_from_line(line)
            if value is not None:
                token_values.append(value)
        
        if not token_values:
            raise ValueError("No valid token counts found in thread ID file")
        
        # Use the last value as the current total
        current_tokens = token_values[-1]
        
        usage = SessionUsage(
            session_id=args.session_id,
            issue=args.issue,
            repository=args.repository,
            pull_request=args.pull_request,
            start_tokens=current_tokens,  # Assume total snapshot
            finish_tokens=current_tokens,
            delta=0,
            explicit_provenance="thread-id-file-parsed",
            captured_at=datetime.now().astimezone().isoformat(),
        )
        append_usage(args.records, usage)
        print(json.dumps(usage.as_dict(), sort_keys=True))
    
    elif args.kind == "delta":
        # Parse start and finish records
        if not args.start_record.exists():
            raise ValueError(f"Start record not found: {args.start_record}")
        if not args.finish_record.exists():
            raise ValueError(f"Finish record not found: {args.finish_record}")
        
        start_data = json.loads(args.start_record.read_text(encoding="utf-8"))
        finish_data = json.loads(args.finish_record.read_text(encoding="utf-8"))
        
        start_tokens = start_data.get("finish_tokens") or start_data.get("tokens")
        finish_tokens = finish_data.get("finish_tokens") or finish_data.get("tokens")
        
        if start_tokens is None or finish_tokens is None:
            raise ValueError("Could not extract token values from records")
        
        delta = finish_tokens - start_tokens
        
        result = {
            "start_tokens": start_tokens,
            "finish_tokens": finish_tokens,
            "delta": delta,
            "reset": finish_tokens < start_tokens and finish_tokens == 0,
        }
        
        if args.output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"Start: {start_tokens} tokens")
            print(f"Finish: {finish_tokens} tokens")
            print(f"Delta: {delta} tokens")
            if result["reset"]:
                print("Note: Token count reset detected")


def _handle_boundary_command(args: argparse.Namespace) -> None:
    """Handle boundary subcommands"""
    if args.kind == "start":
        usage = SessionUsage(
            session_id=args.session_id,
            issue=args.issue,
            repository=args.repository,
            pull_request=args.pull_request,
            start_tokens=args.tokens,
            finish_tokens=None,
            delta=None,
            explicit_provenance="explicit-start-boundary",
            captured_at=args.timestamp or datetime.now().astimezone().isoformat(),
        )
        append_usage(args.records, usage)
        print(json.dumps(usage.as_dict(), sort_keys=True))
    
    elif args.kind == "finish":
        if not args.start_record.exists():
            raise ValueError(f"Start record not found: {args.start_record}")
        
        start_data = json.loads(args.start_record.read_text(encoding="utf-8"))
        start_tokens = start_data.get("finish_tokens") or start_data.get("start_tokens") or start_data.get("tokens")
        
        if start_tokens is None:
            raise ValueError("Could not extract start tokens from start record")
        
        delta = args.tokens - start_tokens
        
        usage = SessionUsage(
            session_id=args.session_id,
            issue=args.issue,
            repository=args.repository,
            pull_request=args.pull_request,
            start_tokens=start_tokens,
            finish_tokens=args.tokens,
            delta=delta,
            explicit_provenance="explicit-finish-boundary",
            captured_at=args.timestamp or datetime.now().astimezone().isoformat(),
        )
        append_usage(args.records, usage)
        print(json.dumps(usage.as_dict(), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
