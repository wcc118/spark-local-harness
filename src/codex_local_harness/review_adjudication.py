"""Bind an explicit primary audit decision to immutable review evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .diagnostics import Adjudication


def evidence_reference(envelope: Any, log: Path, result: dict) -> str:
    payload = json.dumps({'envelope': envelope.as_dict(), 'result': result,
                          'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()},
                         sort_keys=True, separators=(',', ':')).encode()
    return 'review-audit:' + hashlib.sha256(payload).hexdigest()


def allowed(path: Path | None, run_id: str, reference: str) -> bool:
    if path is None or not path.exists():
        return False
    records = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        record = Adjudication.from_dict(json.loads(line))
        if record.run_id == run_id:
            records.append(record)
    if not records:
        return False
    latest = max(records, key=lambda r: (r.observed_at, r.adjudication_id))
    return (latest.source in {'codex', 'operator'} and latest.verdict == 'passed'
            and latest.codex_decision == 'allow-review-audit'
            and reference in latest.evidence_refs)


def unquoted_command(command: str) -> str:
    """Mask quoted literals for textual command checks, not a shell safety proof.

    Substitution syntax is separately flagged by the caller. An unterminated
    quote retains the original command for conservative audit handling.
    """
    quote = None
    escaped = False
    output = []
    for character in command:
        if escaped:
            output.append(' ' if quote else character)
            escaped = False
        elif character == '\\' and quote != "'":
            escaped = True
            output.append(' ')
        elif quote:
            if character == quote:
                quote = None
            output.append(' ')
        elif character in {'"', "'"}:
            quote = character
            output.append(' ')
        else:
            output.append(character)
    return command if quote else ''.join(output)
