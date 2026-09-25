"""Validated read-only host tool mounts for the worker container.

Workers sometimes need native tools that were built and verified on the host
(for example compiled analysis engines). This module turns an operator-owned
configuration into docker ``--mount`` values without widening the sandbox:

- every mount is read-only;
- sources are existing, symlink-free directories that are not credentials,
  configuration, harness state or system locations, and not an ancestor of any
  of those;
- targets are confined to the worker's XDG data directory (``~/.local/share``
  inside the worker home), so a mount cannot shadow the worker's own
  configuration, cache or the repository checkout.

Configuration (``~/.config/codex-local/sandbox-mounts.json``)::

    {"schema": 1,
     "readonly_mounts": [
       {"source": "/home/user/.local/share/flight-loads/engines",
        "target": "~/.local/share/flight-loads/engines"}]}
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sys

SCHEMA = 1
TARGET_ROOT = PurePosixPath('.local/share')
# Relative to the operator's home: never exposed, nor any directory containing them.
DENIED_HOME_PATHS = (
    '.ssh', '.gnupg', '.config', '.codex', '.claude', '.claude.json', '.aws', '.azure',
    '.docker', '.kube', '.netrc', '.git-credentials', '.password-store', '.pki',
    '.local/share/codex-local-harness', '.local/share/opencode-orchestration',
    '.local/share/keyrings',
)
DENIED_SYSTEM_PATHS = ('/', '/etc', '/root', '/proc', '/sys', '/dev', '/run', '/var/run',
                       '/boot', '/var/lib/docker', '/tmp')
FORBIDDEN_CHARACTERS = (',', '=', '\n', '\r', '\0')


class MountConfigError(ValueError):
    """The mount configuration is invalid; no mount is applied."""


def _is_within(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def _checked_text(value, field: str, index: int) -> str:
    if not isinstance(value, str) or not value:
        raise MountConfigError(f'readonly_mounts[{index}].{field} must be a non-empty string')
    if any(character in value for character in FORBIDDEN_CHARACTERS):
        raise MountConfigError(f'readonly_mounts[{index}].{field} contains a forbidden character')
    return value


def _source(value, index: int, home: Path, system_denied) -> PurePosixPath:
    text = _checked_text(value, 'source', index)
    path = PurePosixPath(text)
    if not path.is_absolute() or '..' in path.parts or str(path) != os.path.normpath(text):
        raise MountConfigError(f'readonly_mounts[{index}].source must be a normalized absolute path')
    real = Path(text)
    if not real.is_dir():
        raise MountConfigError(f'readonly_mounts[{index}].source is not an existing directory: {text}')
    if PurePosixPath(os.path.realpath(text)) != path:
        raise MountConfigError(f'readonly_mounts[{index}].source must not traverse symlinks: {text}')
    home_path = PurePosixPath(os.path.realpath(home))
    denied = [PurePosixPath(p) for p in system_denied if p != '/']
    denied += [home_path / p for p in DENIED_HOME_PATHS]
    if path == PurePosixPath('/') or _is_within(home_path, path):
        raise MountConfigError(f'readonly_mounts[{index}].source must not be the home directory or an ancestor of it')
    for blocked in denied:
        if _is_within(path, blocked) or _is_within(blocked, path):
            raise MountConfigError(f'readonly_mounts[{index}].source overlaps a protected location: {blocked}')
    return path


def _target(value, index: int, worker_home: Path) -> PurePosixPath:
    text = _checked_text(value, 'target', index)
    if not text.startswith('~/'):
        raise MountConfigError(f'readonly_mounts[{index}].target must start with ~/ (worker home)')
    relative = PurePosixPath(text[2:])
    if relative.is_absolute() or '..' in relative.parts or str(relative) != os.path.normpath(text[2:]):
        raise MountConfigError(f'readonly_mounts[{index}].target must be a normalized relative path')
    if relative == TARGET_ROOT or TARGET_ROOT not in relative.parents:
        raise MountConfigError(f'readonly_mounts[{index}].target must be inside ~/{TARGET_ROOT}/')
    return PurePosixPath(worker_home) / relative


def mount_arguments(config: dict, home: Path, worker_home: Path, *,
                    system_denied=DENIED_SYSTEM_PATHS) -> list[str]:
    """Return docker ``--mount`` values; raise MountConfigError on any invalid entry."""
    if not isinstance(config, dict) or set(config) != {'schema', 'readonly_mounts'}:
        raise MountConfigError('configuration requires exactly schema and readonly_mounts')
    if config['schema'] != SCHEMA or isinstance(config['schema'], bool):
        raise MountConfigError(f'unsupported schema: {config["schema"]!r}')
    entries = config['readonly_mounts']
    if not isinstance(entries, list):
        raise MountConfigError('readonly_mounts must be a list')
    mounts, targets = [], set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {'source', 'target'}:
            raise MountConfigError(f'readonly_mounts[{index}] requires exactly source and target')
        source = _source(entry['source'], index, home, system_denied)
        target = _target(entry['target'], index, worker_home)
        if any(_is_within(target, other) or _is_within(other, target) for other in targets):
            raise MountConfigError(f'readonly_mounts[{index}].target overlaps another mount')
        targets.add(target)
        mounts.append(f'type=bind,src={source},dst={target},readonly')
    return mounts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--worker-home', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        for mount in mount_arguments(config, args.home, args.worker_home):
            print(mount)
    except (OSError, json.JSONDecodeError, MountConfigError) as error:
        print(f'ERROR: sandbox mount configuration rejected: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
