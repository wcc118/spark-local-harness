"""Deterministic case generation for role-suite v2.

Only the runner imports this module. Model-visible workspaces receive evidence,
allowed answer labels, and an output schema, but never the expected answers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Case:
    case_id: str
    suite: str
    category: str
    evidence: dict[str, Any]
    expected: tuple[str, ...]
    forbidden: tuple[str, ...] = ()
    timeout: int = 300


def _reviewer() -> list[Case]:
    defects = [
        ("NULL_GUARD", "def parse(item):\n    return item['name'].strip()", "item may be None"),
        ("AUTH_BYPASS", "if request.user or request.headers.get('X-Admin'):\n    allow()", "header is attacker controlled"),
        ("PATH_TRAVERSAL", "return open(root / user_path).read()", "user_path is unvalidated"),
        ("LOST_UPDATE", "value = load(); value += 1; save(value)", "read-modify-write has no lock"),
        ("WRONG_BOUNDARY", "if retries > max_retries: retry()", "allows one extra retry"),
        ("RESOURCE_LEAK", "f = open(path); return f.read()", "file is never closed"),
        ("CACHE_KEY", "cache[user.id] = price_for(user, region)", "region is absent from key"),
        ("ERROR_SWALLOW", "try: commit()\nexcept Exception: return True", "failure is reported as success"),
    ]
    cases: list[Case] = []
    labels = [d[0] for d in defects]
    for i, (label, patch, note) in enumerate(defects, 1):
        cases.append(Case(f"review-single-{i:02d}", "reviewer", "single-defect",
                          {"patch": patch, "context": note, "allowed_findings": labels}, (label,)))
    pairs = [(0, 5), (1, 7), (2, 6), (3, 4)]
    for i, (a, b) in enumerate(pairs, 1):
        cases.append(Case(f"review-interacting-{i:02d}", "reviewer", "interacting-defects",
                          {"patches": [defects[a][1], defects[b][1]],
                           "context": [defects[a][2], defects[b][2]], "allowed_findings": labels},
                          (defects[a][0], defects[b][0])))
    clean = [
        "def parse(item):\n    if item is None: return None\n    return item['name'].strip()",
        "with open(path) as f:\n    return f.read()",
        "if retries >= max_retries: stop()",
        "try: commit()\nexcept Exception: return False",
    ]
    for i, patch in enumerate(clean, 1):
        cases.append(Case(f"review-clean-{i:02d}", "reviewer", "clean-control",
                          {"patch": patch, "allowed_findings": labels}, (), tuple(labels)))
    for i, patch in enumerate([
        "def divide(a,b): return a/b\n# tests only cover b=2",
        "def decode(data): return json.loads(data)\n# tests only cover valid JSON",
    ], 1):
        cases.append(Case(f"review-tests-{i:02d}", "reviewer", "inadequate-tests",
                          {"patch": patch, "allowed_findings": ["MISSING_NEGATIVE_TEST"]},
                          ("MISSING_NEGATIVE_TEST",)))
    for i, patch in enumerate([
        "if key not in cache: cache[key] = build(key)",
        "state.ready = True\nstate.payload = payload",
    ], 1):
        cases.append(Case(f"review-concurrency-{i:02d}", "reviewer", "concurrency-state",
                          {"patch": patch, "allowed_findings": ["RACE_CONDITION"]},
                          ("RACE_CONDITION",)))
    return cases


def _investigator() -> list[Case]:
    rows = [
        ("failing-test", ["expected 3, got 2", "filter uses x > threshold", "threshold=2"], "BOUNDARY_FILTER"),
        ("logs", ["connect timeout", "DNS lookup 8.2s", "TCP connect not started"], "DNS_LATENCY"),
        ("cross-module", ["writer emits user_id", "reader expects userId"], "SCHEMA_MISMATCH"),
        ("regression", ["good commit enables normalization", "bad commit removes normalize()"], "NORMALIZATION_REMOVED"),
        ("dependency", ["library v3 removed legacy_open", "application calls legacy_open"], "REMOVED_API"),
        ("conflicting", ["service says write succeeded", "database shows rollback", "queue retry followed"], "FALSE_SUCCESS_LOG"),
        ("insufficient", ["one request failed", "no timestamp", "no component logs"], "INSUFFICIENT_EVIDENCE"),
        ("resource", ["latency rises with swap-in", "available memory 400MiB", "CPU idle 78%"], "MEMORY_PRESSURE"),
    ]
    allowed = [r[2] for r in rows]
    return [Case(f"investigate-{i:02d}", "investigator", cat,
                 {"observations": obs, "allowed_conclusions": allowed}, (answer,), timeout=300)
            for i, (cat, obs, answer) in enumerate(rows, 1)]


def _test_strategy() -> list[Case]:
    rows = [
        ("changed-behavior", "parser now accepts empty input", ("EMPTY_INPUT",)),
        ("untested-branch", "fallback activates only when primary raises TimeoutError", ("TIMEOUT_FALLBACK",)),
        ("edge-cases", "pagination accepts offset and limit", ("ZERO_LIMIT", "NEGATIVE_OFFSET")),
        ("regression", "fix changes case-insensitive identity matching", ("MIXED_CASE_IDENTITY",)),
        ("integration", "producer changed field from id to item_id", ("OLD_PRODUCER_NEW_CONSUMER",)),
        ("coupling", "tests assert private helper call count rather than output", ("BEHAVIORAL_ASSERTION",)),
    ]
    allowed = sorted({x for _, _, answers in rows for x in answers})
    return [Case(f"test-strategy-{i:02d}", "test-strategy", cat,
                 {"change": change, "allowed_tests": allowed}, answers, timeout=300)
            for i, (cat, change, answers) in enumerate(rows, 1)]


def _noncoding() -> list[Case]:
    templates = {
        "long-context": [
            (["S1: retention is 30 days", "S2: legal hold overrides deletion"], ("RETENTION_30_DAYS", "LEGAL_HOLD_OVERRIDE")),
            (["S1: API v1 retires June", "S2: client migration requires v2 token"], ("V1_RETIRES_JUNE", "V2_TOKEN_REQUIRED")),
            (["S1: backups hourly", "S2: restore objective four hours"], ("HOURLY_BACKUPS", "RTO_FOUR_HOURS")),
            (["S1: EU data stays EU", "S2: analytics export is global"], ("EU_RESIDENCY_CONFLICT",)),
        ],
        "architecture": [
            (["single queue", "no dead-letter queue", "unbounded retries"], ("RETRY_POISON_LOOP",)),
            (["shared mutable cache", "eight writers", "no versioning"], ("CACHE_WRITE_RACE",)),
            (["database migration", "no rollback", "mixed app versions"], ("MIXED_VERSION_RISK", "NO_ROLLBACK")),
            (["sync downstream call", "30s timeout", "request SLA 2s"], ("SLA_VIOLATION",)),
        ],
        "research": [
            (["A costs 4/unit and latency 10ms", "B costs 2/unit and latency 40ms", "limit 20ms"], ("CHOOSE_A",)),
            (["A supports ARM", "B does not", "deployment is ARM"], ("CHOOSE_A",)),
            (["A license Apache-2.0", "B license unknown", "require permissive verified license"], ("CHOOSE_A",)),
            (["A benchmark uses 100 samples", "B uses 3", "accuracy differs by 0.2%"], ("EVIDENCE_INSUFFICIENT",)),
        ],
        "specification": [
            (["export CSV", "must preserve timezone", "maximum 10k rows"], ("TIMEZONE_CRITERION", "ROW_LIMIT_CRITERION")),
            (["delete account", "30-day recovery", "legal holds"], ("RECOVERY_CRITERION", "LEGAL_HOLD_CRITERION")),
            (["upload images", "PNG/JPEG", "max 8MiB"], ("TYPE_CRITERION", "SIZE_CRITERION")),
            (["notify owners", "deduplicate per incident", "retry twice"], ("DEDUP_CRITERION", "RETRY_CRITERION")),
        ],
        "reconciliation": [
            (["ledger A=10", "ledger B=12", "two pending in B"], ("PENDING_EXPLAINS_DELTA",)),
            (["inventory SKU1=5", "shipment +3", "current SKU1=8"], ("INVENTORY_MATCH",)),
            (["Alice id=7", "A. Smith id=7"], ("DUPLICATE_IDENTITY",)),
            (["invoice 4 paid", "bank reference invoice 4", "ERP says open"], ("ERP_STALE",)),
        ],
        "operations": [
            (["deploy 12:00", "errors start 12:02", "rollback 12:10", "errors stop 12:11"], ("DEPLOY_REGRESSION",)),
            (["disk 99%", "writes fail ENOSPC", "CPU normal"], ("DISK_EXHAUSTION",)),
            (["cert expired 00:00", "TLS failures start 00:00"], ("CERT_EXPIRY",)),
            (["worker lag rises", "queue input triples", "worker count unchanged"], ("CAPACITY_SHORTFALL",)),
        ],
    }
    cases: list[Case] = []
    for category, items in templates.items():
        allowed = sorted({a for _, answers in items for a in answers})
        for i, (facts, expected) in enumerate(items, 1):
            cases.append(Case(f"{category}-{i:02d}", "non-coding", category,
                              {"facts": facts, "allowed_conclusions": allowed}, expected, timeout=360))
    return cases


def _end_to_end() -> list[Case]:
    rows = [
        (["bug reproduced", "boundary comparison wrong", "focused test available"], ("DIAGNOSE", "PATCH", "TEST", "REVIEW")),
        (["feature specified", "API compatibility required", "tests absent"], ("PLAN", "IMPLEMENT", "TEST", "REVIEW")),
        (["logs show DNS delay", "configuration owner identified"], ("INVESTIGATE", "PROPOSE", "VERIFY", "REVIEW")),
        (["clean patch", "all acceptance tests pass"], ("VERIFY", "APPROVE")),
    ]
    return [Case(f"workflow-{i:02d}", "end-to-end", "workflow",
                 {"state": facts, "allowed_steps": ["DIAGNOSE", "PATCH", "TEST", "REVIEW", "PLAN", "IMPLEMENT", "INVESTIGATE", "PROPOSE", "VERIFY", "APPROVE"]},
                 expected, timeout=480) for i, (facts, expected) in enumerate(rows, 1)]


def all_cases() -> list[Case]:
    return _reviewer() + _investigator() + _test_strategy() + _noncoding() + _end_to_end()
