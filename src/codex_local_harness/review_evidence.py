"""Host-derived review facts; model judgment is validated by review.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize(raw: dict[str, Any], envelope: Any, log: Path | None) -> tuple[dict, dict]:
    result = dict(raw)
    aliases = {
        'repository': ('repository', 'repo'),
        'issue': ('issue',),
        'pull_request': ('pull_request', 'pr'),
        'reviewed_head_sha': ('reviewed_head_sha', 'head', 'head_sha', 'exact_head'),
    }
    expected = dict(repository=envelope.repository, issue=envelope.issue,
                    pull_request=envelope.pull_request, reviewed_head_sha=envelope.head_sha)
    nested = result.pop('identity', {})
    if isinstance(nested, str):
        repo, sep, number = nested.partition('#')
        nested = {'repository': repo, 'pull_request': number} if sep else {'repository': nested}
    if not isinstance(nested, dict):
        nested = {'repository': nested}
    mismatches = []
    for field, names in aliases.items():
        values = [result.pop(name) for name in names if name in result]
        values += [nested[name] for name in names if name in nested]
        if any(type(value) is not type(expected[field]) or value != expected[field] for value in values):
            mismatches.append(field)
        result[field] = expected[field]
    diagnostics = {'identity_echo_mismatch': mismatches, 'derived_tests': 0, 'unverified_tests': 0}
    events: dict[str, list[Any]] = {}
    if log is not None:
        for line in log.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('type') != 'tool_use':
                continue
            part = event.get('part') or {}
            state = part.get('state') or {}
            if part.get('tool') != 'bash' or state.get('status') != 'completed':
                continue
            command = (state.get('input') or {}).get('command')
            if isinstance(command, str) and command.strip():
                events.setdefault(command, []).append((state.get('metadata') or {}).get('exit'))
    tests = result.get('tests', [])
    mapping = isinstance(tests, dict)
    if mapping:
        tests = [{'command': str(k), 'outcome': str(v)} for k, v in tests.items()]
    elif not isinstance(tests, list):
        tests = [tests]
    normalized = []
    for item in tests:
        entry = dict(item) if isinstance(item, dict) else {'command': item, 'outcome': 'not independently executed'} if isinstance(item, str) else {'outcome': str(item)}
        command = entry.get('command')
        statuses = events.get(command, []) if isinstance(command, str) else []
        # Multiple executions can disagree or represent separate runs. Do not pick one.
        observed = (not mapping and entry.get('provenance') == 'reviewer-observed'
                    and len(statuses) == 1 and type(statuses[0]) is int and 0 <= statuses[0] <= 255)
        entry.pop('exit_code', None)
        if observed:
            entry.update(provenance='reviewer-observed', exit_code=statuses[0])
            entry['outcome'] = 'Host observed whole-command exit status; model interpretation is not verified'
            diagnostics['derived_tests'] += 1
        else:
            entry.update(provenance='task-reported', unverified=True)
            entry['outcome'] = str(entry.get('outcome', '')) + '; unverified, not independently executed'
            diagnostics['unverified_tests'] += 1
        normalized.append(entry)
    result['tests'] = normalized
    findings = result.get('findings', [])
    if isinstance(findings, list):
        result['findings'] = [dict(item, message=next((item[k] for k in ('message', 'description', 'finding', 'summary')
                                                     if isinstance(item.get(k), str) and item[k].strip()), 'Unspecified finding'))
                              if isinstance(item, dict) else item for item in findings]
    return result, diagnostics


def record_counts(path: Path | None, run_id: str, diagnostics: dict, *, published: bool) -> None:
    if path is None:
        return
    from dataclasses import dataclass, asdict
    from .diagnostics import append_record

    @dataclass(frozen=True)
    class Counts:
        record_id: str
        run_id: str
        identity_echo_mismatches: int
        derived_tests: int
        unverified_tests: int
        audit_flagged: bool
        audit_adjudicated: bool
        published: bool

        def as_dict(self):
            return asdict(self)

    adjudicated = bool(diagnostics.get('audit_adjudicated'))
    suffix = 'published' if published else 'adjudicated' if adjudicated else 'retained'
    append_record(path, Counts(run_id + ':' + suffix, run_id,
        len(diagnostics.get('identity_echo_mismatch', [])), diagnostics.get('derived_tests', 0),
        diagnostics.get('unverified_tests', 0), bool(diagnostics.get('audit_flagged')),
        adjudicated, published))
