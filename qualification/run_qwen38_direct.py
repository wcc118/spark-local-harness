from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


MODEL = "qwen/qwen3.8-27b"


def request(endpoint: str, prompt: str, temperature: float, max_tokens: int) -> tuple[dict, float]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    started = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    ), timeout=300) as response:
        payload = json.load(response)
    return payload, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://10.0.0.92:1234")
    parser.add_argument("--fixtures", type=Path, default=Path("qualification/qwen38"))
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    args.outputs.mkdir(parents=True, exist_ok=True)

    warm, warm_seconds = request(args.endpoint, "Reply with exactly: READY", 0, 128)
    summary = {"model": MODEL, "temperature": args.temperature, "max_tokens": args.max_tokens,
               "warmup_seconds": warm_seconds, "warmup_usage": warm.get("usage"), "trials": []}
    fixtures = ("test_designer", "failure_analyst", "scout")
    for role in fixtures:
        for control in (False, True):
            path = args.fixtures / f"{role}{'_negative' if control else ''}.md"
            prompt = path.read_text(encoding="utf-8")
            repetitions = 1 if control else args.trials
            for index in range(1, repetitions + 1):
                payload, seconds = request(args.endpoint, prompt, args.temperature, args.max_tokens)
                message = payload["choices"][0]["message"]
                content = message.get("content") or ""
                reasoning = message.get("reasoning_content") or ""
                name = f"{role}.{'negative' if control else f'trial-{index}'}"
                (args.outputs / f"{name}.out").write_text(content, encoding="utf-8")
                (args.outputs / f"{name}.reasoning.out").write_text(reasoning, encoding="utf-8")
                summary["trials"].append({
                    "role": role.replace("_", "-"), "control": "negative" if control else "positive",
                    "trial": index, "wall_seconds": seconds, "finish_reason": payload["choices"][0].get("finish_reason"),
                    "usage": payload.get("usage", {}), "content_chars": len(content), "reasoning_chars": len(reasoning),
                    "output_file": f"{name}.out",
                })
                print(json.dumps(summary["trials"][-1], sort_keys=True), flush=True)
    (args.outputs / "trial-metrics.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
