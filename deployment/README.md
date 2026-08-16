# Codex Local Harness — Deployment

This directory contains systemd user units for persistent deployments.

## Prerequisites

- systemd with user session support
- Python 3.11+ installed at `/usr/bin/python3`
- Codex Local Harness installed at `%h/.local/share/codex-local-harness/runtime/current/`

## Units

### dashboard-backend.service

The loopback-only dashboard backend. It:
- Binds to `127.0.0.1:8765` (loopback)
- Reads health data from `%h/.local/share/codex-local-harness/health/`
- Reads session usage data from `%h/.local/share/codex-local-harness/health/sessions.jsonl` (optional)
- Reads session identity data from `%h/.local/share/codex-local-harness/health/identities.jsonl` (optional)
- Is GET-only and read-only
- Requires no external network access

**IMPORTANT**: The dashboard must remain loopback-only. LAN exposure is handled by a separate socat proxy.

**NOTE**: This unit does not use `User=` or `PrivateNetwork=` directives. It runs as the current user with access to the host network namespace.

**NEW IN v0.1.2**: The dashboard now includes session telemetry cards when session usage records are available. These are conditionally displayed and do not appear if no session data exists.

### socat-lan-proxy.service

An optional LAN proxy that exposes the loopback dashboard to the local network.

**WARNING**: This unit requires operator review of the bind address before installation.

Current configuration:
```
Bind address: 10.0.0.98
Target: 127.0.0.1:8765
```

Before enabling this unit:
1. Review the bind address in the unit file
2. Ensure network access is appropriate for your environment
3. Consider firewall rules and network security

## Installation

### Manual installation

```bash
# Create the systemd user config directory if it doesn't exist
mkdir -p ~/.config/systemd/user

# Copy the unit file(s)
cp deployment/dashboard-backend.service ~/.config/systemd/user/

# (Optional) Copy the LAN proxy unit
cp deployment/socat-lan-proxy.service ~/.config/systemd/user/

# Reload systemd user configuration
systemctl --user daemon-reload

# Enable the dashboard backend
systemctl --user enable --now dashboard-backend

# (Optional) Enable the LAN proxy
systemctl --user enable --now socat-lan-proxy
```

### Check status

```bash
# View service status
systemctl --user status dashboard-backend
systemctl --user status socat-lan-proxy

# View logs
journalctl --user -u dashboard-backend -f
journalctl --user -u socat-lan-proxy -f

# Verify dashboard is listening
curl http://127.0.0.1:8765/healthz
# Expected: {"status":"ok","read_only":true}
```

### Uninstall

```bash
# Stop and disable services
systemctl --user stop socat-lan-proxy dashboard-backend
systemctl --user disable socat-lan-proxy dashboard-backend

# Remove unit files
rm ~/.config/systemd/user/socat-lan-proxy.service
rm ~/.config/systemd/user/dashboard-backend.service

# Reload systemd user configuration
systemctl --user daemon-reload
```

## Verification

After installation, verify:

1. **Service status**: `systemctl --user status dashboard-backend` should show `active (running)`
2. **Dashboard reachable**: `curl http://127.0.0.1:8765/` shows the UI
3. **Health endpoint**: `curl http://127.0.0.1:8765/healthz` returns `{"status":"ok","read_only":true}`
4. **Loopback binding**: `ss -lnp | grep 8765` shows binding to `127.0.0.1:8765`

## Troubleshooting

### Service fails to start

Check logs: `journalctl --user -u dashboard-backend -n 50`

Common issues:
- Missing health directory: `mkdir -p ~/.local/share/codex-local-harness/health/`
- Python path mismatch: Ensure the harness is installed at `%h/.local/share/codex-local-harness/runtime/current/`
- Permission denied: Ensure the user owns `~/.config/systemd/user/`

### Dashboard not responding

1. Verify service is running: `systemctl --user status dashboard-backend`
2. Check binding: `ss -lnp | grep 8765`
3. Verify health paths exist: `ls ~/.local/share/codex-local-harness/health/`
4. Test locally: `curl http://127.0.0.1:8765/healthz`

### LAN proxy not accessible

1. Verify socat is installed: `which socat`
2. Check service status: `systemctl --user status socat-lan-proxy`
3. Verify bind address: `ss -lnp | grep 8765`
4. Check firewall rules on the bind address network
