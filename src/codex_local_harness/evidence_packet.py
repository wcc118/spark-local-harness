from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_CHANGED_FILES = 100
MAX_DIFF_STAT_CHARS = 4_000
MAX_PATCH_CHARS = 32_000
MAX_PACKET_BYTES = 64_000
MAX_PATH_CHARS = 512
MAX_REPORTED_TASK_CHARS = 24_000
MAX_REVIEW_PROMPT_CHARS = 100_000
MAX_RISK_SIGNALS = 16
MAX_RISK_SIGNAL_CHARS = 160
SHA = re.compile(r"^[0-9a-f]{40}$")
Run = Callable[[Sequence[str], Path | None], str]


@dataclass(frozen=True)
class ReviewerEvidencePacket:
    schema_version: int
    provenance: str
    repository: str
    issue: int
    pull_request: int
    base_tip_sha: str
    comparison_base_sha: str
    head_sha: str
    changed_file_count: int
    changed_files: tuple[str, ...]
    changed_files_truncated: bool
    diff_stat: str
    diff_stat_truncated: bool
    patch: str
    patch_truncated: bool
    patch_sha256: str
    test_executables: tuple[tuple[str, str], ...]
    advisory_risk_signals: tuple[str, ...] = ()
    verification_command: str = "git diff --check <comparison-base>..<head>"
    verification_exit_code: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.provenance != "harness-observed":
            raise ValueError("invalid evidence packet metadata")
        if "/" not in self.repository or self.issue <= 0 or self.pull_request <= 0:
            raise ValueError("invalid evidence packet identity")
        if not all(SHA.fullmatch(value) for value in (self.base_tip_sha, self.comparison_base_sha, self.head_sha)):
            raise ValueError("evidence packet SHAs must be full lowercase Git SHAs")
        if self.changed_file_count <= 0 or not self.changed_files:
            raise ValueError("evidence packet requires changed files")
        if len(self.changed_files) > MAX_CHANGED_FILES or len(set(self.changed_files)) != len(self.changed_files):
            raise ValueError("invalid changed-file inventory")
        for item in self.changed_files:
            path = PurePosixPath(item)
            if not item or len(item) > MAX_PATH_CHARS or path.is_absolute() or ".." in path.parts:
                raise ValueError("invalid changed-file path")
        if len(self.diff_stat) > MAX_DIFF_STAT_CHARS or len(self.patch) > MAX_PATCH_CHARS:
            raise ValueError("evidence packet field exceeds bound")
        if not re.fullmatch(r"[0-9a-f]{64}", self.patch_sha256):
            raise ValueError("invalid patch digest")
        if len(set(name for name, _ in self.test_executables)) != len(self.test_executables):
            raise ValueError("duplicate test executable")
        if (
            len(self.advisory_risk_signals) > MAX_RISK_SIGNALS
            or len(set(self.advisory_risk_signals)) != len(self.advisory_risk_signals)
            or any(not value or len(value) > MAX_RISK_SIGNAL_CHARS for value in self.advisory_risk_signals)
        ):
            raise ValueError("invalid advisory risk signals")
        if self.verification_exit_code != 0:
            raise ValueError("review preflight verification did not pass")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_json(self) -> str:
        value = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        if len(value.encode("utf-8")) > MAX_PACKET_BYTES:
            raise ValueError("evidence packet exceeds serialized size bound")
        return value


def discover_test_executables(names: Sequence[str] = ("python3", "pytest", "node", "npm")) -> tuple[tuple[str, str], ...]:
    observed = []
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            observed.append((name, str(Path(resolved).resolve())))
    return tuple(observed)


def build_reviewer_evidence_packet(
    checkout: Path,
    repository: str,
    issue: int,
    pull_request: int,
    base_sha: str,
    head_sha: str,
    run: Run,
    test_executables: Mapping[str, str],
) -> ReviewerEvidencePacket:
    comparison_base_sha = run(["git", "merge-base", base_sha, head_sha], checkout).strip()
    if not SHA.fullmatch(comparison_base_sha):
        raise ValueError("Git returned an invalid comparison base SHA")
    revision = f"{comparison_base_sha}..{head_sha}"
    raw_files = tuple(line for line in run(["git", "diff", "--name-only", revision], checkout).splitlines() if line)
    if len(set(raw_files)) != len(raw_files):
        raise ValueError("Git returned duplicate changed files")
    for item in raw_files:
        path = PurePosixPath(item)
        if len(item) > MAX_PATH_CHARS or path.is_absolute() or ".." in path.parts:
            raise ValueError("Git returned an unsafe changed-file path")
    diff_stat_raw = run(["git", "diff", "--stat", "--compact-summary", revision], checkout).strip()
    patch_raw = run(["git", "diff", "--no-ext-diff", "--unified=40", revision], checkout)
    run(["git", "diff", "--check", revision], checkout)
    if not diff_stat_raw or not patch_raw:
        raise ValueError("Git returned empty review evidence")
    diff_stat, stat_truncated = _bounded(diff_stat_raw, MAX_DIFF_STAT_CHARS, "\n...[diff stat truncated]")
    patch, patch_truncated = _bounded(patch_raw, MAX_PATCH_CHARS, "\n...[patch truncated; verify against digest]")
    packet = ReviewerEvidencePacket(
        schema_version=SCHEMA_VERSION,
        provenance="harness-observed",
        repository=repository,
        issue=issue,
        pull_request=pull_request,
        base_tip_sha=base_sha,
        comparison_base_sha=comparison_base_sha,
        head_sha=head_sha,
        changed_file_count=len(raw_files),
        changed_files=raw_files[:MAX_CHANGED_FILES],
        changed_files_truncated=len(raw_files) > MAX_CHANGED_FILES,
        diff_stat=diff_stat,
        diff_stat_truncated=stat_truncated,
        patch=patch,
        patch_truncated=patch_truncated,
        patch_sha256=hashlib.sha256(patch_raw.encode("utf-8")).hexdigest(),
        test_executables=tuple(sorted(test_executables.items())),
        advisory_risk_signals=detect_advisory_risk_signals(patch_raw, raw_files),
    )
    packet.compact_json()
    return packet


def detect_advisory_risk_signals(patch: str, changed_files: Sequence[str]) -> tuple[str, ...]:
    """Return bounded, answer-neutral reviewer cues from added Python lines."""
    if not any(path.endswith(".py") for path in changed_files):
        return ()
    added = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    signals: list[str] = []
    if any(re.search(r"\bos\.sync\s*\(", line) for line in added):
        signals.append("python-portability: direct os.sync() call added; verify platform support and durability claim")

    guard = re.compile(r"\bhasattr\s*\(\s*os\s*,\s*['\"]O_DIRECTORY['\"]\s*\)")
    direct = re.compile(r"\bos\.O_DIRECTORY\b")
    for index, line in enumerate(added):
        if not direct.search(line):
            continue
        nearby = added[max(0, index - 3):index + 1]
        if not any(guard.search(candidate) for candidate in nearby):
            signals.append(
                "python-portability: unguarded os.O_DIRECTORY access added; verify a portable fallback"
            )
            break
    return tuple(signals)


def _bounded(value: str, limit: int, marker: str) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[: limit - len(marker)] + marker, True
