"""CLI for LM Studio lane health check and GPU lease management."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .lane import LaneStatus, check_lane, GpuLease


def cmd_health(args: argparse.Namespace) -> int:
    """Execute health command."""
    base_url = args.base_url or os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    token = os.environ.get("LMSTUDIO_API_TOKEN")

    result = check_lane(
        base_url=base_url,
        expected_model=args.model,
        min_context=args.min_context or 0,
        timeout=args.timeout or 8.0,
        token=token,
    )

    if args.json:
        print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    else:
        # Human-readable output
        if result.status == "ok":
            print(f"OK: {result.detail}")
        else:
            print(f"{result.status.upper()}: {result.detail}")

    # Map status to exit codes
    exit_map = {
        "ok": 0,
        "unreachable": 10,
        "model-not-loaded": 11,
        "context-too-small": 12,
        "error": 13,
    }
    return exit_map.get(result.status, 13)


def cmd_lease_status(args: argparse.Namespace) -> int:
    """Execute lease-status command."""
    name = args.name or "default"
    lease = GpuLease(name=name, purpose="lease-status-check")
    holder = lease.holder()

    if holder is None:
        print("free")
    else:
        print(f"pid={holder['pid']}, purpose={holder['purpose']}, started_at={holder['started_at']}")

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute run command."""
    name = args.name
    purpose = args.purpose
    lease_timeout = args.lease_timeout or 30.0

    lease = GpuLease(name=name, purpose=purpose)

    if not lease.acquire(timeout=lease_timeout):
        print(f"ERROR: Failed to acquire lease '{name}' within {lease_timeout}s", file=sys.stderr)
        return 75

    try:
        # Run the command
        result = subprocess.run(args.cmd, check=False)
        return result.returncode
    finally:
        lease.release()


def cmd_guard(args: argparse.Namespace) -> int:
    """Execute guard command."""
    base_url = args.base_url or os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    token = os.environ.get("LMSTUDIO_API_TOKEN")

    result = check_lane(
        base_url=base_url,
        expected_model=args.model,
        min_context=args.min_context or 0,
        timeout=args.timeout or 8.0,
        token=token,
    )

    if result.status != "ok":
        # Fail-open: print skip message and exit 0, or exit with error code if strict
        print(f"LANE-SKIPPED: {result.status} {result.detail}", file=sys.stderr)
        if args.strict:
            exit_map = {
                "ok": 0,
                "unreachable": 10,
                "model-not-loaded": 11,
                "context-too-small": 12,
                "error": 13,
            }
            return exit_map.get(result.status, 13)
        return 0

    # Lane is healthy, run the command
    result = subprocess.run(args.cmd, check=False)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    """Main entry point for lane CLI."""
    parser = argparse.ArgumentParser(
        prog="codex-local-lane",
        description="LM Studio lane health check and GPU lease management",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # health subcommand
    health_parser = subparsers.add_parser("health", help="Check LM Studio lane health")
    health_parser.add_argument("--base-url", "-b", help="LM Studio base URL (default: env LMSTUDIO_BASE_URL)")
    health_parser.add_argument("--model", "-m", required=True, help="Expected model ID")
    health_parser.add_argument("--min-context", "-c", type=int, help="Minimum required context length")
    health_parser.add_argument("--timeout", "-t", type=float, help="Request timeout in seconds (default: 8.0)")
    health_parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    # lease-status subcommand
    lease_parser = subparsers.add_parser("lease-status", help="Show current lease holder")
    lease_parser.add_argument("--name", "-n", default="default", help="Lease name (default: default)")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run a command while holding the lease")
    run_parser.add_argument("--name", "-n", required=True, help="Lease name")
    run_parser.add_argument("--purpose", "-p", required=True, help="Purpose description")
    run_parser.add_argument("--lease-timeout", "-T", type=float, help="Lease acquisition timeout (default: 30s)")
    run_parser.add_argument("cmd", nargs="+", help="Command to run")

    # guard subcommand
    guard_parser = subparsers.add_parser("guard", help="Guard command with health check")
    guard_parser.add_argument("--base-url", "-b", help="LM Studio base URL (default: env LMSTUDIO_BASE_URL)")
    guard_parser.add_argument("--model", "-m", required=True, help="Expected model ID")
    guard_parser.add_argument("--min-context", "-c", type=int, help="Minimum required context length")
    guard_parser.add_argument("--timeout", "-t", type=float, help="Request timeout in seconds (default: 8.0)")
    guard_parser.add_argument("--strict", action="store_true", help="Fail with health exit code if lane unhealthy")
    guard_parser.add_argument("cmd", nargs="+", help="Command to run")

    args = parser.parse_args(argv)

    try:
        if args.command == "health":
            return cmd_health(args)
        elif args.command == "lease-status":
            return cmd_lease_status(args)
        elif args.command == "run":
            return cmd_run(args)
        elif args.command == "guard":
            return cmd_guard(args)
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
