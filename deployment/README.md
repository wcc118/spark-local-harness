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
- Is GET-only and read-only
- Requires no external network access

**IMPORTANT**: The dashboard must remain loopback-only. LAN exposure is handled by a separate socat proxy.

**NOTE**: This unit does not use `User=` or `PrivateNetwork=` directives. It runs as the current user with access to the host network namespace.

### vision-mcp-backend.service

The vision-review MCP server (issue #205). It:
- Binds to `127.0.0.1:8790` (loopback)
- Calls out to the RTX 3090's LM Studio (`LMSTUDIO_BASE_URL`) through the lane
  guard/lease from `src/codex_local_harness/lane.py`; never touches this
  repository's checkout beyond reading a screenshot the caller names
- Exposes exactly one MCP tool, `review_screenshot`, restricted to a fixed
  allowlist of local directories (see `vision_mcp_server.py`'s module
  docstring); it is advisory only and holds no repository or GitHub authority

**NOTE**: Requires the `mcp` package (`pip install mcp==2.2.0`, or install this
project with its declared dependencies) for the Python interpreter the unit
invokes. Requires network reachability to the 3090's LM Studio; if the 3090
is off, calls fail soft (the tool reports `"available": false"`, it does not
crash the caller).

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

# (Optional) Copy the vision-review MCP server unit
cp deployment/vision-mcp-backend.service ~/.config/systemd/user/

# Reload systemd user configuration
systemctl --user daemon-reload

# Enable the dashboard backend
systemctl --user enable --now dashboard-backend

# (Optional) Enable the LAN proxy
systemctl --user enable --now socat-lan-proxy

# (Optional) Enable the vision-review MCP server
systemctl --user enable --now vision-mcp-backend
```

Production `promote-v4-worker` runs also install the dashboard unit, reload the
user manager, and restart the service if it is already active. The prior unit is
stored in the same promotion backup and restored by `rollback-v4-worker`.
Isolated tests and non-production prefixes skip unit installation by default;
set `CODEX_LOCAL_INSTALL_UNITS=1` with explicit unit/systemctl paths to exercise it.

### Check status

```bash
# View service status
systemctl --user status dashboard-backend
systemctl --user status socat-lan-proxy
systemctl --user status vision-mcp-backend

# View logs
journalctl --user -u dashboard-backend -f
journalctl --user -u socat-lan-proxy -f
journalctl --user -u vision-mcp-backend -f

# Verify dashboard is listening
curl http://127.0.0.1:8765/healthz
# Expected: {"status":"ok","read_only":true}

# Verify the vision-review MCP server is listening (a bare GET is expected to
# 400 -- MCP negotiates a session over POST; this only confirms the port is up)
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/mcp
```

### Uninstall

```bash
# Stop and disable services
systemctl --user stop socat-lan-proxy dashboard-backend vision-mcp-backend
systemctl --user disable socat-lan-proxy dashboard-backend vision-mcp-backend

# Remove unit files
rm ~/.config/systemd/user/socat-lan-proxy.service
rm ~/.config/systemd/user/dashboard-backend.service
rm ~/.config/systemd/user/vision-mcp-backend.service

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
