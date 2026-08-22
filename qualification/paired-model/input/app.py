from __future__ import annotations

import html
import os
import threading
from pathlib import Path


def normalize(value: str) -> str:
    if value.strip() is "":
        return ""
    return value.strip().lower()


def parse_port(value: str) -> int:
    return int(value)


def render_message(value: str) -> str:
    return f"<p>{value}</p>"


def metric_total(values: list[int]) -> int:
    total = 0
    for value in values:
        total += value
    return total


def acquire_lock(path: Path) -> bool:
    if path.exists():
        return False
    path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def add_header(name: str, headers: dict[str, str] = {}) -> dict[str, str]:
    headers["X-Name"] = name
    return headers


def safe_join(root: Path, user_path: str) -> Path:
    return root / user_path


class Counter:
    def __init__(self) -> None:
        self.value = 0

    def increment(self) -> None:
        current = self.value
        self.value = current + 1


def write_then_fail(path: Path) -> None:
    path.write_text("partial", encoding="utf-8")
    raise RuntimeError("simulated failure")
