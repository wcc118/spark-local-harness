"""Tests for the Phase 1 vision-gate tooling (qualification/vision/).

Covers the pure logic that does not require a browser or the 3090: manifest
integrity, schema validation and fence-unwrapping in judge.py, and the
keyword/numeric matching and gate arithmetic in score.py. Rendering itself is
covered by tests/test_visual_render.py; this file does not re-test it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VISION_DIR = ROOT / "qualification" / "vision"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def judge():
    return _load("vision_judge", VISION_DIR / "judge.py")


@pytest.fixture(scope="module")
def score_mod():
    return _load("vision_score", VISION_DIR / "score.py")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((VISION_DIR / "manifest.json").read_text())


# --- manifest integrity -----------------------------------------------------


def test_manifest_is_valid_json_with_required_top_keys(manifest):
    assert set(manifest.keys()) >= {"schema_version", "viewport", "frozen", "dev"}


def test_every_frozen_fixture_id_is_unique(manifest):
    ids = [e["id"] for e in manifest["frozen"]]
    assert len(ids) == len(set(ids))


def test_every_frozen_page_reference_exists_on_disk(manifest):
    pages_dir = VISION_DIR / "pages"
    for entry in manifest["frozen"]:
        assert (pages_dir / entry["page"]).is_file(), entry["id"]


def test_model_only_defects_have_match_keywords(manifest):
    for entry in manifest["frozen"]:
        if entry["category"] != "model-only":
            continue
        assert len(entry["defects"]) == 1, entry["id"]
        keywords = entry["defects"][0].get("match_keywords")
        assert keywords, f"{entry['id']} has no match_keywords for scoring"


def test_clean_fixtures_have_no_defects(manifest):
    for entry in manifest["frozen"]:
        if entry["category"] == "clean":
            assert entry["defects"] == [], entry["id"]
            assert entry["model_only"] is False


def test_frozen_set_has_at_least_eight_model_only_defects(manifest):
    model_only = [e for e in manifest["frozen"] if e["category"] == "model-only"]
    assert len(model_only) >= 8


def test_dev_fixtures_are_disjoint_from_frozen_ids(manifest):
    frozen_ids = {e["id"] for e in manifest["frozen"]}
    dev_ids = {e["id"] for e in manifest["dev"]}
    assert frozen_ids.isdisjoint(dev_ids)


# --- judge.py: schema validation and fence unwrapping -----------------------


def test_unwrap_fence_strips_a_single_json_fence(judge):
    text = '```json\n{"a": 1}\n```'
    assert judge.unwrap_fence(text) == '{"a": 1}'


def test_unwrap_fence_leaves_unfenced_text_alone(judge):
    text = '{"a": 1}'
    assert judge.unwrap_fence(text) == text


def test_validate_schema_accepts_a_well_formed_reply(judge):
    obj = {
        "findings": [{"description": "x", "region": "y", "severity": "low"}],
        "numeric_readings": [{"label": "a", "value": "1"}],
    }
    assert judge.validate_schema(obj) is None


def test_validate_schema_accepts_empty_lists(judge):
    obj = {"findings": [], "numeric_readings": []}
    assert judge.validate_schema(obj) is None


def test_validate_schema_rejects_non_object(judge):
    assert judge.validate_schema([1, 2, 3]) is not None


def test_validate_schema_rejects_missing_key(judge):
    assert judge.validate_schema({"findings": []}) is not None


def test_validate_schema_rejects_bad_severity(judge):
    obj = {"findings": [{"description": "x", "region": "y", "severity": "critical"}], "numeric_readings": []}
    assert judge.validate_schema(obj) is not None


def test_validate_schema_rejects_non_string_field(judge):
    obj = {"findings": [{"description": 1, "region": "y", "severity": "low"}], "numeric_readings": []}
    assert judge.validate_schema(obj) is not None


def test_validate_schema_rejects_missing_finding_key(judge):
    obj = {"findings": [{"description": "x", "severity": "low"}], "numeric_readings": []}
    assert judge.validate_schema(obj) is not None


def test_validate_schema_rejects_bad_numeric_reading(judge):
    obj = {"findings": [], "numeric_readings": [{"label": "a"}]}
    assert judge.validate_schema(obj) is not None


# --- score.py: matching and gate arithmetic ---------------------------------


def test_match_defect_finds_a_keyword_hit(score_mod):
    defect = {"match_keywords": ["72", "sum"]}
    findings = [{"description": "The total is 72.0 but should be 60", "region": "footer"}]
    assert score_mod.match_defect(defect, findings) is True


def test_match_defect_is_case_insensitive(score_mod):
    defect = {"match_keywords": ["ORANGE"]}
    findings = [{"description": "bar is orange", "region": ""}]
    assert score_mod.match_defect(defect, findings) is True


def test_match_defect_returns_false_with_no_hit(score_mod):
    defect = {"match_keywords": ["zzz-not-present"]}
    findings = [{"description": "everything looks fine", "region": ""}]
    assert score_mod.match_defect(defect, findings) is False


def test_match_defect_returns_false_with_no_findings(score_mod):
    defect = {"match_keywords": ["72"]}
    assert score_mod.match_defect(defect, []) is False


def test_match_numeric_reading_ignores_whitespace_and_case(score_mod):
    readings = [{"label": "Damping ratio", "value": "0.037500"}]
    assert score_mod.match_numeric_reading("Damping ratio", "0.037500", readings) is True


def test_match_numeric_reading_rejects_a_different_value(score_mod):
    readings = [{"label": "Damping ratio", "value": "0.04"}]
    assert score_mod.match_numeric_reading("Damping ratio", "0.037500", readings) is False


def _fake_run(tmp_path: Path, fixture_id: str, records: list[dict]) -> Path:
    fdir = tmp_path / fixture_id
    fdir.mkdir(parents=True)
    for i, rec in enumerate(records):
        (fdir / f"repeat-{i}.json").write_text(json.dumps({"repeat": i, **rec}))
    return tmp_path


def test_score_model_only_recall_counts_fixtures_found_in_any_repeat(score_mod, tmp_path):
    fixtures = {
        "d1": {
            "category": "model-only", "model_only": True,
            "defects": [{"id": "x", "match_keywords": ["boom"]}],
        }
    }
    run_dir = tmp_path / "run"
    _fake_run(
        run_dir, "d1",
        [
            {"outcome": "ok", "parsed": {"findings": [{"description": "no issue", "region": ""}], "numeric_readings": []}},
            {"outcome": "ok", "parsed": {"findings": [{"description": "there is a boom here", "region": ""}], "numeric_readings": []}},
            {"outcome": "ok", "parsed": {"findings": [], "numeric_readings": []}},
        ],
    )
    result = score_mod.score(fixtures, run_dir)
    assert result["per_fixture"]["d1"]["found_in_any_repeat"] is True
    assert result["gate"]["1_model_only_recall_ge_60pct_and_ge_8_defects"]["value"] == 1.0


def test_score_false_positive_rate_averages_over_clean_repeats(score_mod, tmp_path):
    fixtures = {"c1": {"category": "clean", "model_only": False, "defects": [], "numeric_fields": []}}
    run_dir = tmp_path / "run"
    _fake_run(
        run_dir, "c1",
        [
            {"outcome": "ok", "parsed": {"findings": [{"description": "a", "region": ""}, {"description": "b", "region": ""}], "numeric_readings": []}},
            {"outcome": "ok", "parsed": {"findings": [], "numeric_readings": []}},
        ],
    )
    result = score_mod.score(fixtures, run_dir)
    assert result["gate"]["2_false_positives_le_0.5_per_clean_screenshot"]["value"] == pytest.approx(1.0)


def test_score_numeric_accuracy(score_mod, tmp_path):
    fixtures = {
        "n1": {
            "category": "numeric-reading", "model_only": False, "defects": [],
            "numeric_fields": [{"label": "a", "value": "1.0"}, {"label": "b", "value": "2.0"}],
        }
    }
    run_dir = tmp_path / "run"
    _fake_run(
        run_dir, "n1",
        [{"outcome": "ok", "parsed": {"findings": [], "numeric_readings": [{"label": "a", "value": "1.0"}]}}],
    )
    result = score_mod.score(fixtures, run_dir)
    assert result["gate"]["3_numeric_accuracy_ge_90pct"]["value"] == pytest.approx(0.5)


def test_score_stability_flags_a_flip_flopping_verdict(score_mod, tmp_path):
    fixtures = {
        "d1": {"category": "model-only", "model_only": True, "defects": [{"id": "x", "match_keywords": ["boom"]}]}
    }
    run_dir = tmp_path / "run"
    _fake_run(
        run_dir, "d1",
        [
            {"outcome": "ok", "parsed": {"findings": [{"description": "boom", "region": ""}], "numeric_readings": []}},
            {"outcome": "ok", "parsed": {"findings": [], "numeric_readings": []}},
            {"outcome": "ok", "parsed": {"findings": [{"description": "boom", "region": ""}], "numeric_readings": []}},
        ],
    )
    result = score_mod.score(fixtures, run_dir)
    assert result["per_fixture"]["d1"]["stable_across_repeats"] is False
    assert result["gate"]["4_stability_ge_80pct"]["value"] == pytest.approx(0.0)


def test_score_gate_five_fails_on_a_request_error(score_mod, tmp_path):
    fixtures = {"c1": {"category": "clean", "model_only": False, "defects": [], "numeric_fields": []}}
    run_dir = tmp_path / "run"
    _fake_run(run_dir, "c1", [{"outcome": "request-error", "parsed": None}])
    result = score_mod.score(fixtures, run_dir)
    assert result["gate"]["5_zero_hangs_no_silent_repair_lane_always_available"]["pass"] is False


def test_score_missing_repeats_counted_in_infra_and_gate_five(score_mod, tmp_path):
    fixtures = {"c1": {"category": "clean", "model_only": False, "defects": [], "numeric_fields": []}}
    run_dir = tmp_path / "run"
    # only 1 of 3 expected repeats present
    _fake_run(run_dir, "c1", [{"outcome": "ok", "parsed": {"findings": [], "numeric_readings": []}}])
    result = score_mod.score(fixtures, run_dir)
    assert result["infra_counts"]["missing"] == 2
    assert result["gate"]["5_zero_hangs_no_silent_repair_lane_always_available"]["pass"] is False


def test_score_overall_pass_requires_every_gate(score_mod, tmp_path):
    fixtures = {"c1": {"category": "clean", "model_only": False, "defects": [], "numeric_fields": []}}
    run_dir = tmp_path / "run"
    _fake_run(run_dir, "c1", [{"outcome": "request-error", "parsed": None}])
    result = score_mod.score(fixtures, run_dir)
    assert result["gate"]["overall_pass"] is False
