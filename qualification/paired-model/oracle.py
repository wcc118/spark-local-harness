from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import threading
from pathlib import Path


def load(workspace: Path):
    spec = importlib.util.spec_from_file_location("bench_app", workspace / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def check(case: str, workspace: Path) -> None:
    app = load(workspace)
    if case == "investigation":
        text = (workspace / "FINDINGS.md").read_text(encoding="utf-8").lower()
        assert "identity" in text and "normalize" in text
    elif case == "focused-fix":
        assert app.normalize("") == "" and app.normalize("  HELLO ") == "hello"
        tree = ast.parse((workspace / "app.py").read_text())
        assert not any(isinstance(node, ast.Compare) and any(isinstance(op, ast.Is) for op in node.ops) for node in ast.walk(tree))
    elif case == "test-authoring":
        test = (workspace / "test_app.py").read_text(encoding="utf-8")
        assert "parse_port" in test
        result = __import__("subprocess").run([sys.executable, "-m", "unittest", "-q"], cwd=workspace)
        assert result.returncode == 0
    elif case == "ui":
        assert app.render_message('<script>alert("x")</script>') == "<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;</p>"
    elif case == "refactor":
        assert app.metric_total([1, 2, 3]) == 6 and app.metric_total([]) == 0
        source = (workspace / "app.py").read_text()
        assert "sum(values)" in source
    elif case == "diagnosis":
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "worker.lock"
            lock.write_text("999999999", encoding="utf-8")
            assert app.acquire_lock(lock) is True
    elif case == "hygiene":
        first = app.add_header("one")
        second = app.add_header("two")
        assert first is not second and first["X-Name"] == "one" and second["X-Name"] == "two"
    elif case == "review":
        payload = json.loads((workspace / "REVIEW.json").read_text(encoding="utf-8"))
        joined = json.dumps(payload).lower()
        assert "path" in joined and ("travers" in joined or "escape" in joined) and "safe_join" in joined
    elif case == "concurrency":
        counter = app.Counter()
        threads = [threading.Thread(target=lambda: [counter.increment() for _ in range(1000)]) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        assert counter.value == 8000
        assert hasattr(counter, "_lock")
    elif case == "cleanup":
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "partial.txt"
            try: app.write_then_fail(target)
            except RuntimeError: pass
            assert not target.exists()
    else:
        raise AssertionError(f"unknown case: {case}")


if __name__ == "__main__":
    check(sys.argv[1], Path(sys.argv[2]).resolve())
