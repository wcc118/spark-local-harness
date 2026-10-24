#!/usr/bin/env python3
"""Native todo_write tool qualification probe.

Tests the actual installed Qwen0.22.0 TodoWriteTool (from
/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks/todoWrite-*.js).

 Runs in a separate temp home/process, with no configured hooks, and verifies
a malformed task is rejected without corrupting valid prior state.

Key behaviors verified:
1. create(in_progress) succeeds for valid todos
2. update(completed) succeeds for valid todos
3. readback returns persisted state
4. malformed todo is rejected and prior state is preserved
5. no hooks configured (hooks absent)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def find_todo_chunk() -> Path:
    """Locate the installed todoWrite chunk, resolving the hash dynamically."""
    chunks_dir = Path("/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks")
    matches = list(chunks_dir.glob("todoWrite-*.js"))
    if not matches:
        raise RuntimeError("No todoWrite chunk found")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple todoWrite chunks found: {matches}")
    return matches[0]


def run_node_test(home: str, test_script: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a Node.js test script in an isolated environment.
    
    Returns (exit_code, stdout, stderr).
    """
    env = os.environ.copy()
    env["HOME"] = home
    env["QWEN_HOME"] = home
    env["QWEN_RUNTIME_DIR"] = home
    # Ensure no hooks are loaded
    env.pop("QWEN_HOOKS", None)
    
    proc = subprocess.run(
        ["node", "--experimental-vm-modules", "-e", test_script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_full_test_script(
    session_id: str,
    operation: str,
    todos: list[dict[str, Any]] | None = None,
) -> str:
    """Write a complete Node.js test script that exercises the actual TodoWriteTool.
    
    This uses file:// URLs to import from absolute paths.
    """
    chunk_path = "/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks/todoWrite-FJJTY2TI.js"
    storage_chunk = "/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks/chunk-A5F2YNO6.js"
    
    return f"""
import {{ TodoWriteTool, readTodosForSession, listTodoSessions }} from "file://{chunk_path}";
import {{ Storage }} from "file://{storage_chunk}";

const fs = require("fs");
const path = require("path");

// Set up runtime directory
const home = process.env.QWEN_HOME || process.env.HOME;
const todoDir = path.join(home, "todos");
fs.mkdirSync(todoDir, {{ recursive: true }});

// Configure Storage runtime base dir
Storage.setRuntimeBaseDir(home, home);

const todoFile = path.join(todoDir, "{session_id}.json");

async function runTest() {{
  try {{
    console.log("Loaded todoWrite from file:// url");
    console.log("Storage.runtimeBaseDir:", Storage.getRuntimeBaseDir());
    console.log("Todo dir:", todoDir);
    
    // Create a mock config for the tool
    const config = {{
      getSessionId: () => "{session_id}",
      getHookSystem: () => null, // No hooks
      setActiveTodoReminder: () => {{
        // no-op
      }},
    }};
    
    const tool = new TodoWriteTool(config);
    
    const operation = "{operation}";
    
    if (operation === "read") {{
      if (fs.existsSync(todoFile)) {{
        const content = fs.readFileSync(todoFile, "utf8");
        console.log("READ_SUCCESS:", content);
      }} else {{
        console.log("READ_SUCCESS: []");
      }}
      return 0;
    }}
    
    if (operation === "list") {{
      const sessions = await listTodoSessions();
      console.log("LIST_SUCCESS:", JSON.stringify(sessions));
      return 0;
    }}
    
    if (operation === "create" || operation === "update") {{
      const todosArg = {json.dumps(todos)};

      // Validate params before invocation
      const validationError = tool.validateToolParams({{ todos: todosArg }});
      if (validationError) {{
        console.log("VALIDATION_ERROR:", validationError);
        return 1;
      }}

      // Create invocation
      const params = {{ todos: todosArg }};

      // Execute the tool
      const result = await tool.createInvocation(params).execute(null);
      console.log("EXEC_RESULT:", JSON.stringify(result));
      return 0;
    }}
    
  }} catch (err) {{
    console.log("ERROR:", err.message);
    console.log("STACK:", err.stack);
    return 1;
  }}
}}

runTest().then(code => process.exit(code)).catch(err => {{
  console.log("FATAL:", err.message);
  process.exit(1);
}});
"""


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Native todo_write qualification probe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=Path("native-todo-results.json"),
        help="Output JSON file for results",
    )
    parser.add_argument(
        "--temp-home", "-t", type=Path, default=None,
        help="Temp home directory (default: create new)",
    )
    parser.add_argument(
        "--cleanup", "-c", action="store_true",
        help="Clean up temp home on exit",
    )
    args = parser.parse_args()
    
    results: dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": None,
        "temp_home": None,
        "todo_chunk": None,
        "tests": [],
    }
    
    try:
        # Find the todoWrite chunk
        chunk_path = find_todo_chunk()
        results["todo_chunk"] = str(chunk_path)
        print(f"Found todoWrite chunk: {chunk_path}")
        
        # Setup temp home
        temp_home = args.temp_home
        should_cleanup = args.cleanup
        if temp_home is None:
            temp_home, should_cleanup = Path(tempfile.mkdtemp(prefix="qwen-todo-probe-")), True
        results["temp_home"] = str(temp_home)
        home = str(temp_home)
        print(f"Using temp home: {home}")
        
        session_id = "probe-session"
        
        # Test 1: Create valid todos
        print("\n[Test 1] Create valid todos...")
        test_script = write_full_test_script(
            session_id,
            "create",
            todos=[
                {"id": "task-1", "content": "First task", "status": "pending"},
                {"id": "task-2", "content": "Second task", "status": "in_progress"},
            ],
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        if stderr:
            print(f"  Stderr: {stderr}")
        
        test1_pass = exit_code == 0 and "EXEC_RESULT" in stdout
        results["tests"].append({
            "name": "create_valid_todos",
            "pass": test1_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Test 2: Read back the todos
        print("\n[Test 2] Read back todos...")
        test_script = write_full_test_script(
            session_id,
            "read",
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        
        test2_pass = exit_code == 0 and "READ_SUCCESS" in stdout
        results["tests"].append({
            "name": "read_todos",
            "pass": test2_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Test 3: Update todos
        print("\n[Test 3] Update todos...")
        test_script = write_full_test_script(
            session_id,
            "update",
            todos=[
                {"id": "task-1", "content": "First task", "status": "completed"},
                {"id": "task-2", "content": "Second task", "status": "completed"},
            ],
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        
        test3_pass = exit_code == 0 and "EXEC_RESULT" in stdout
        results["tests"].append({
            "name": "update_todos",
            "pass": test3_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Test 4: Validate malformed todo (missing id)
        print("\n[Test 4] Validate malformed todo (missing id)...")
        test_script = write_full_test_script(
            session_id + "-malformed",
            "create",
            todos=[
                {"content": "Task without id", "status": "pending"},  # Missing id
            ],
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        
        # Should fail validation (exit code 1) with VALIDATION_ERROR
        test4_pass = exit_code == 1 and "VALIDATION_ERROR" in stdout
        results["tests"].append({
            "name": "validate_malformed_missing_id",
            "pass": test4_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Test 5: Validate malformed todo (invalid status)
        print("\n[Test 5] Validate malformed todo (invalid status)...")
        test_script = write_full_test_script(
            session_id + "-malformed2",
            "create",
            todos=[
                {"id": "task-1", "content": "Task with invalid status", "status": "invalid_status"},
            ],
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        
        test5_pass = exit_code == 1 and "VALIDATION_ERROR" in stdout
        results["tests"].append({
            "name": "validate_malformed_invalid_status",
            "pass": test5_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Test 6: Malformed todo preserves prior state
        print("\n[Test 6] Verify malformed todo preserves prior valid state...")
        # First, read current state (should have task-1 completed, task-2 completed)
        test_script = write_full_test_script(
            session_id,
            "read",
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        prior_content = stdout.strip()
        print(f"  Prior state before malformed: {prior_content}")
        
        # Try malformed todo
        test_script = write_full_test_script(
            session_id,
            "create",
            todos=[
                {"id": "task-1", "content": "Bad task", "status": "notvalid"},  # Invalid status
            ],
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Malformed attempt exit: {exit_code}, stdout: {stdout}")
        
        # Read state again - should be unchanged
        test_script = write_full_test_script(
            session_id,
            "read",
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        after_content = stdout.strip()
        print(f"  State after malformed: {after_content}")
        
        test6_pass = prior_content == after_content
        results["tests"].append({
            "name": "malformed_preserves_prior_state",
            "pass": test6_pass,
            "exit_code": exit_code,
            "prior_state": prior_content,
            "after_state": after_content,
        })
        
        # Test 7: List sessions
        print("\n[Test 7] List todo sessions...")
        test_script = write_full_test_script(
            session_id,
            "list",
        )
        exit_code, stdout, stderr = run_node_test(home, test_script)
        print(f"  Exit code: {exit_code}")
        print(f"  Stdout: {stdout}")
        
        test7_pass = exit_code == 0 and "LIST_SUCCESS" in stdout and session_id in stdout
        results["tests"].append({
            "name": "list_sessions",
            "pass": test7_pass,
            "exit_code": exit_code,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })
        
        # Timestamp
        from datetime import datetime, timezone
        results["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Summary
        passed = sum(1 for t in results["tests"] if t["pass"])
        total = len(results["tests"])
        results["summary"] = {
            "passed": passed,
            "total": total,
            "success_rate": passed / total if total > 0 else 0,
        }
        
        print(f"\n{'='*60}")
        print(f"Results: {passed}/{total} tests passed")
        print(f"{'='*60}")
        
        # Write results
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results written to: {args.output}")
        
        return 0 if passed == total else 1
        
    finally:
        # Cleanup temp home if requested and we created it
        if temp_home and should_cleanup and temp_home.exists():
            print(f"\nCleaning up temp home: {temp_home}")
            shutil.rmtree(temp_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
