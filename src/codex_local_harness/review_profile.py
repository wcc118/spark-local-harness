"""Render scratch allowances in the agent's final permission layer."""
from __future__ import annotations

import json
from pathlib import Path


def render(template: str, checkout: Path, scratch: Path) -> str:
    # Markdown agent permissions are applied after JSON configuration. Keep the
    # deny default and exact workspace exceptions together so the former cannot
    # accidentally override the latter during OpenCode's layered merge.
    rules = {"*": "deny"}
    for root in (checkout, scratch):
        rules[str(root.resolve())] = "allow"
        rules[str(root.resolve()) + "/*"] = "allow"
    anchor = "  bash: allow\n"
    if template.count(anchor) != 1:
        raise ValueError("Unexpected reviewer template")
    return template.replace(anchor, anchor + "  external_directory: " + json.dumps(rules) + "\n")


if __name__ == "__main__":
    import sys
    template, output, checkout, scratch = map(Path, sys.argv[1:])
    # A frozen distribution may have read-only templates. Replace the disposable
    # runtime copy instead of depending on the inherited file mode being writable.
    rendered = render(template.read_text(), checkout, scratch)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(rendered)
    temporary.chmod(0o600)
    temporary.replace(output)
