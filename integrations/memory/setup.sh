#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
mkdir -p data backups .local
python3 - <<'PY'
import os,secrets
from pathlib import Path
p=Path('.env')
if not p.exists():
    p.write_text(f'MEMORY_UID={os.getuid()}\nMEMORY_GID={os.getgid()}\nSURREAL_USER=admin\nSURREAL_PASS={secrets.token_urlsafe(36)}\n')
p.chmod(0o600)
PY
docker compose up -d --wait --wait-timeout 60
python3 memoryctl.py bootstrap
