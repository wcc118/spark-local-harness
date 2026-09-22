"""Vision judge logic for reviewing dashboard screenshots and validating LM responses.

This module provides reusable functions for calling a vision model and validating
its JSON reply against a schema. It was extracted from qualification/vision/judge.py
so that MCP servers and other tools can share the same validation logic without
duplicating the code.

The functions here are pure and do not depend on any qualification-specific
logic (filesystem access, fixture iteration, lane orchestration, or GPU leasing).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

PROMPT = """You are reviewing a screenshot of a dashboard or data panel for defects.

Look for things that are actually wrong or inconsistent WITHIN this single image: numbers that
contradict each other, a chart that contradicts its own legend or axis, a value that does not
match the table it is next to, misaligned or overlapping content, placeholder or template text
left unresolved, implausible dates, or anything else a careful reviewer would flag. Only report
something you can point to concretely in this image; do not guess about data you cannot see. If
the page looks internally consistent and correct, report an empty findings list.

Separately, transcribe every clearly legible labelled numeric value you can read in the image
(for example "Damping ratio: 0.037500"), exactly as printed, including sign, units and precision.
If you are not confident of a digit, omit that field rather than guessing.

Reply with EXACTLY ONE JSON object and nothing else (no prose, no markdown fence), matching this
shape:
{"findings": [{"description": "...", "region": "...", "severity": "low|medium|high"}],
 "numeric_readings": [{"label": "...", "value": "..."}]}
"""

REQUIRED_TOP_KEYS = {"findings", "numeric_readings"}
FINDING_KEYS = {"description", "region", "severity"}
SEVERITIES = {"low", "medium", "high"}
READING_KEYS = {"label", "value"}
FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


def unwrap_fence(text: str) -> str:
    m = FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def validate_schema(obj: Any) -> str | None:
    """Return None if valid, else a short reason."""
    if not isinstance(obj, dict):
        return "top level is not an object"
    if set(obj.keys()) < REQUIRED_TOP_KEYS:
        return f"missing required key(s): {REQUIRED_TOP_KEYS - set(obj.keys())}"
    findings = obj["findings"]
    readings = obj["numeric_readings"]
    if not isinstance(findings, list):
        return "'findings' is not a list"
    if not isinstance(readings, list):
        return "'numeric_readings' is not a list"
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not FINDING_KEYS <= set(f.keys()):
            return f"findings[{i}] missing required keys"
        if not all(isinstance(f[k], str) for k in FINDING_KEYS):
            return f"findings[{i}] has a non-string field"
        if f["severity"] not in SEVERITIES:
            return f"findings[{i}].severity is not one of {SEVERITIES}"
    for i, r in enumerate(readings):
        if not isinstance(r, dict) or not READING_KEYS <= set(r.keys()):
            return f"numeric_readings[{i}] missing required keys"
        if not all(isinstance(r[k], str) for k in READING_KEYS):
            return f"numeric_readings[{i}] has a non-string field"
    return None


def call_model(base_url: str, model: str, png_bytes: bytes, timeout: float, max_tokens: int) -> dict[str, Any]:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    elapsed = time.monotonic() - started
    message = raw["choices"][0]["message"]
    content = message.get("content") or ""
    usage = raw.get("usage") or {}
    return {"raw_response": raw, "content": content, "elapsed_seconds": elapsed, "usage": usage}


def score_one_call(base_url: str, model: str, png_bytes: bytes, timeout: float, max_tokens: int) -> dict[str, Any]:
    attempts = []
    for attempt in range(2):  # one retry allowance for an unparsable/invalid reply
        try:
            result = call_model(base_url, model, png_bytes, timeout, max_tokens)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            attempts.append({"attempt": attempt, "outcome": "request-error", "error": str(exc)})
            continue
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            attempts.append({"attempt": attempt, "outcome": "malformed-http-response", "error": str(exc)})
            continue
        text = unwrap_fence(result["content"])
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            attempts.append({
                "attempt": attempt, "outcome": "invalid-json", "error": str(exc),
                "content": result["content"], "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
            })
            continue
        reason = validate_schema(parsed)
        if reason is not None:
            attempts.append({
                "attempt": attempt, "outcome": "schema-invalid", "reason": reason,
                "content": result["content"], "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
            })
            continue
        attempts.append({
            "attempt": attempt, "outcome": "ok", "parsed": parsed, "content": result["content"],
            "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
        })
        return {"outcome": "ok", "parsed": parsed, "attempts": attempts}
    final_outcome = attempts[-1]["outcome"] if attempts else "no-attempts"
    return {"outcome": final_outcome, "parsed": None, "attempts": attempts}
