import json

import pytest

from codex_local_harness.plan_cli import main
from codex_local_harness.plan_event import read_plan_records


def test_plan_cli_publishes_validated_revision(tmp_path, capsys):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({
        "event_id": "plan-1", "operation_id": "issue-55", "issue": 55,
        "objective": "Expose the public running plan",
        "steps": [{"step_id": "build", "summary": "Build it", "state": "active"}],
        "observed_at": "2026-08-16T06:00:00Z", "source": "codex",
        "session_id": "thread-a", "repository": "owner/repo",
    }), encoding="utf-8")
    records = tmp_path / "plans.jsonl"
    assert main(["--records", str(records), "--event-file", str(event_file)]) == 0
    assert read_plan_records(records)[0].objective == "Expose the public running plan"
    assert json.loads(capsys.readouterr().out)["event_id"] == "plan-1"
    with pytest.raises(SystemExit):
        main(["--records", str(records), "--event-file", str(event_file)])
