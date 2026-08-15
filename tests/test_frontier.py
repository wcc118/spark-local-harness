import pytest

from codex_local_harness.frontier import FrontierStatus, latest_status


def test_unavailable_status_never_infers_context_or_plan():
    status = FrontierStatus("status-1", "2026-08-15T18:00:00Z", "unavailable", note_code="official-interface-unavailable")
    assert status.context_used is None
    assert status.plan_remaining_percent is None
    with pytest.raises(ValueError, match="cannot contain inferred"):
        FrontierStatus("status-2", "2026-08-15T18:00:00Z", "unavailable", context_used=10)


def test_operator_report_is_timestamped_and_latest_is_derived():
    old = FrontierStatus("old", "2026-08-15T18:00:00Z", "operator-reported", context_used=100, context_window=1000)
    new = FrontierStatus("new", "2026-08-15T18:01:00Z", "operator-reported", plan_remaining_percent=90, plan_refresh_at="2026-08-16T00:00:00Z")
    assert latest_status([new, old]) == new
