"""Tests for systemd unit file syntax and content invariants."""

import re
from pathlib import Path


DEPLOYMENT_DIR = Path(__file__).parent.parent / "deployment"


def read_unit(name: str) -> str:
    """Read a unit file from the deployment directory."""
    path = DEPLOYMENT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Unit file not found: {path}")
    return path.read_text(encoding="utf-8")


def test_dashboard_backend_exists():
    """Verify dashboard-backend.service exists."""
    assert (DEPLOYMENT_DIR / "dashboard-backend.service").exists()


def test_socat_lan_proxy_exists():
    """Verify socat-lan-proxy.service exists."""
    assert (DEPLOYMENT_DIR / "socat-lan-proxy.service").exists()


def test_dashboard_backend_has_correct_description():
    """Verify dashboard-backend.service has appropriate description."""
    content = read_unit("dashboard-backend.service")
    assert "Read-only diagnostic dashboard" in content
    assert "loopback-only" in content


def test_dashboard_backend_binds_to_loopback():
    """Verify dashboard-backend.service binds to 127.0.0.1."""
    content = read_unit("dashboard-backend.service")
    assert "--host 127.0.0.1" in content
    assert "--port 8765" in content


def test_dashboard_backend_port_is_8765():
    """Verify dashboard uses the correct port."""
    content = read_unit("dashboard-backend.service")
    assert "--port 8765" in content


def test_dashboard_backend_health_paths_use_h_specifier():
    """Verify dashboard-backend.service uses %h for user home paths."""
    content = read_unit("dashboard-backend.service")
    health_base = "%h/.local/share/codex-local-harness/health/"
    assert f"{health_base}runs.jsonl" in content
    assert f"{health_base}adjudications.jsonl" in content
    assert f"{health_base}lifecycle.jsonl" in content
    assert f"{health_base}workflow.jsonl" in content
    assert f"{health_base}frontier.jsonl" in content
    assert f"{health_base}publication-failures.jsonl" in content
    assert f"{health_base}plans.jsonl" in content
    # Should NOT contain /home/%i/ pattern
    assert "/home/%i/" not in content


def test_dashboard_backend_python_path_points_to_runtime_source():
    """Verify dashboard-backend.service uses correct PYTHONPATH to installed runtime source."""
    content = read_unit("dashboard-backend.service")
    # PYTHONPATH should point to the installed runtime source
    assert "PYTHONPATH=%h/.local/share/codex-local-harness/runtime/current/src" in content
    # Should NOT contain the old /home/%i/.local/bin pattern
    assert "/home/%i/.local/bin" not in content


def test_dashboard_backend_exec_start():
    """Verify dashboard-backend.service uses correct ExecStart."""
    content = read_unit("dashboard-backend.service")
    assert "ExecStart=/usr/bin/python3 -m codex_local_harness.dashboard" in content


def test_dashboard_backend_security_hardening():
    """Verify dashboard-backend.service has security hardening options."""
    content = read_unit("dashboard-backend.service")
    # Check for key security directives
    assert "NoNewPrivileges=yes" in content
    assert "ProtectSystem=strict" in content
    assert "PrivateTmp=yes" in content


def test_user_units_avoid_capability_dropping_directives():
    """Keep the units runnable by the unprivileged user manager on the target host."""
    incompatible = (
        "ProtectKernelTunables=",
        "ProtectKernelModules=",
        "ProtectKernelLogs=",
        "ProtectClock=",
        "ProtectRuntimeDirectory=",
        "RestrictRealtime=",
    )
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert all(directive not in content for directive in incompatible)


def test_dashboard_backend_does_not_use_private_network():
    """Verify dashboard-backend.service does not use PrivateNetwork=yes (removes network isolation)."""
    content = read_unit("dashboard-backend.service")
    # PrivateNetwork=yes should NOT be present
    assert "PrivateNetwork=yes" not in content


def test_dashboard_backend_does_not_use_user_directive():
    """Verify dashboard-backend.service does not use User= directive."""
    content = read_unit("dashboard-backend.service")
    # User= directive should NOT be present
    assert re.search(r"^\s*User\s*=", content, re.MULTILINE) is None


def test_socat_lan_proxy_does_not_use_user_root():
    """Verify socat-lan-proxy.service does not use User=root (invalid for user manager)."""
    content = read_unit("socat-lan-proxy.service")
    # User=root should NOT be present
    assert "User=root" not in content
    # User= directive should NOT be present for user units
    assert re.search(r"^\s*User\s*=", content, re.MULTILINE) is None


def test_socat_lan_proxy_has_warning():
    """Verify socat-lan-proxy.service has operator review warning."""
    content = read_unit("socat-lan-proxy.service")
    assert "OPERATOR REVIEW REQUIRED" in content
    assert "BEFORE ENABLING" in content


def test_socat_lan_proxy_binds_to_correct_address():
    """Verify socat-lan-proxy.service binds to the documented address."""
    content = read_unit("socat-lan-proxy.service")
    # The default bind address should be documented in both the comment and ExecStart
    assert "bind=10.0.0.98" in content


def test_socat_lan_proxy_connects_to_loopback():
    """Verify socat-lan-proxy.service connects to loopback dashboard."""
    content = read_unit("socat-lan-proxy.service")
    assert "TCP4:127.0.0.1:8765" in content


def test_socat_lan_proxy_requires_network_access():
    """Verify socat-lan-proxy.service has PrivateNetwork=no for network access."""
    content = read_unit("socat-lan-proxy.service")
    assert "PrivateNetwork=no" in content


def test_both_units_have_systemd_install_section():
    """Verify both units have Install section with WantedBy=default.target."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "[Install]" in content
        assert "WantedBy=default.target" in content


def test_units_have_documentation_link():
    """Verify units have documentation links to quickstart."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "Documentation=" in content
        assert "mvp-quickstart.md" in content


def test_units_have_restart_policy():
    """Verify units have restart policy for resilience."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "Restart=" in content
        assert "RestartSec=" in content


def test_units_have_syslog_identifier():
    """Verify units have syslog identifiers for log filtering."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "SyslogIdentifier=" in content


def test_dashboard_backend_requires_dashboard_backend_service():
    """Verify socat-lan-proxy waits for dashboard-backend.service."""
    content = read_unit("socat-lan-proxy.service")
    assert "After=network.target dashboard-backend.service" in content
    assert "Wants=dashboard-backend.service" in content


def test_dashboard_backend_release_version():
    """Verify dashboard-backend.service uses the current release."""
    content = read_unit("dashboard-backend.service")
    assert "--release v0.5.0" in content


def test_dashboard_backend_live_mode():
    """Verify dashboard-backend.service uses live mode."""
    content = read_unit("dashboard-backend.service")
    assert "--mode live" in content


def test_units_have_standard_output_redirection():
    """Verify units have proper logging configuration."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "StandardOutput=journal" in content
        assert "StandardError=journal" in content


def test_releasable_unit_file_names():
    """Verify unit file names are consistent and version-independent."""
    unit_dir = DEPLOYMENT_DIR
    service_files = list(unit_dir.glob("*.service"))
    service_names = {f.name for f in service_files}
    expected = {"dashboard-backend.service", "socat-lan-proxy.service"}
    assert service_names == expected


def test_units_have_no_new_privileges():
    """Verify both units have NoNewPrivileges for security."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "NoNewPrivileges=yes" in content


def test_units_have_restrict_suid_sgid():
    """Verify both units restrict SUID/SGID for security."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "RestrictSUIDSGID=yes" in content


def test_units_have_remove_ipc():
    """Verify both units remove IPC resources for security."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "RemoveIPC=yes" in content


def test_units_have_lock_personality():
    """Verify both units lock personality for security."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "LockPersonality=yes" in content


def test_dashboard_backend_requires_loopback_host_validation():
    """Verify dashboard service only allows loopback binding (in code already enforced)."""
    # The unit file sets --host 127.0.0.1, and the Python code enforces loopback-only
    content = read_unit("dashboard-backend.service")
    assert "--host 127.0.0.1" in content
    # The actual binding is enforced in the Python code (see dashboard.py line 310-311)
    # This test confirms the unit file aligns with that constraint


def test_units_do_not_expose_dashboard_directly_to_lan():
    """Verify dashboard unit itself doesn't bind to LAN (only socat does that)."""
    content = read_unit("dashboard-backend.service")
    # Dashboard should only bind to loopback
    assert "--host 127.0.0.1" in content
    # Should NOT have any LAN bind address
    assert "bind=" not in content
