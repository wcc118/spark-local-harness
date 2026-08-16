"""Tests for systemd unit file syntax and content invariants."""

import re
from pathlib import Path

import pytest


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


def test_dashboard_backend_health_paths():
    """Verify dashboard-backend.service uses correct health paths."""
    content = read_unit("dashboard-backend.service")
    health_base = "/home/%i/.local/share/codex-local-harness/health/"
    assert f"{health_base}runs.jsonl" in content
    assert f"{health_base}adjudications.jsonl" in content
    assert f"{health_base}lifecycle.jsonl" in content
    assert f"{health_base}workflow.jsonl" in content
    assert f"{health_base}frontier.jsonl" in content
    assert f"{health_base}publication-failures.jsonl" in content


def test_dashboard_backend_python_path():
    """Verify dashboard-backend.service uses correct Python path."""
    content = read_unit("dashboard-backend.service")
    assert "ExecStart=/usr/bin/python3 -m codex_local_harness.dashboard" in content


def test_dashboard_backend_security_hardening():
    """Verify dashboard-backend.service has security hardening options."""
    content = read_unit("dashboard-backend.service")
    # Check for key security directives
    assert "NoNewPrivileges=yes" in content
    assert "ProtectSystem=strict" in content
    assert "PrivateTmp=yes" in content


def test_dashboard_backend_private_network():
    """Verify dashboard-backend.service uses PrivateNetwork for loopback enforcement."""
    content = read_unit("dashboard-backend.service")
    assert "PrivateNetwork=yes" in content


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
    """Verify dashboard-backend.service uses v0.1.3 release."""
    content = read_unit("dashboard-backend.service")
    assert "--release v0.1.3" in content


def test_dashboard_backend_live_mode():
    """Verify dashboard-backend.service uses live mode."""
    content = read_unit("dashboard-backend.service")
    assert "--mode live" in content


def test_socat_lan_proxy_uses_root_user():
    """Verify socat-lan-proxy.service runs as root for binding."""
    content = read_unit("socat-lan-proxy.service")
    assert "User=root" in content


def test_dashboard_backend_uses_user_i():
    """Verify dashboard-backend.service uses %i for user context."""
    content = read_unit("dashboard-backend.service")
    assert "User=%i" in content
    assert "/home/%i/" in content


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


def test_units_have_protect_kernel_tunables():
    """Verify both units protect kernel tunables."""
    for name in ("dashboard-backend.service", "socat-lan-proxy.service"):
        content = read_unit(name)
        assert "ProtectKernelTunables=yes" in content


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
    # PrivateNetwork=yes ensures no external network access
    assert "PrivateNetwork=yes" in content
