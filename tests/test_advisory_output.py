import json

import pytest

from codex_local_harness.runtime.advisory_output import extract_advisory_object


def write_log(path, texts):
    events = [{"type": "text", "part": {"text": text}} for text in texts]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_extracts_fenced_object_before_trailing_continuation(tmp_path):
    path = tmp_path / "events.jsonl"
    write_log(path, [
        "I will inspect the repository.",
        '```json\n{"schema_version":1,"findings":[]}\n```',
        "The previous task is complete. I need a new task.",
    ])
    assert json.loads(extract_advisory_object(path)) == {"schema_version": 1, "findings": []}


def test_prefers_last_schema_shaped_object(tmp_path):
    path = tmp_path / "events.jsonl"
    write_log(path, ['{"version":0}', '{"version":1}'])
    assert json.loads(extract_advisory_object(path)) == {"version": 1}


def test_rejects_prose_wrapped_or_non_object_results(tmp_path):
    path = tmp_path / "events.jsonl"
    write_log(path, ['Here: ```json\n{"value":1}\n```', '[1,2,3]'])
    with pytest.raises(ValueError, match="no schema-shaped"):
        extract_advisory_object(path)


def test_rejects_malformed_event_with_line_number(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        extract_advisory_object(path)

