# Optional service-test capability; production worker image remains unchanged.
FROM codex-local-worker:0.3.0
RUN python3 -m pip install --no-cache-dir --break-system-packages \
      'mcp==2.2.0' 'uvicorn==0.41.0' \
    && python3 -c 'from mcp.server.mcpserver import MCPServer; from starlette.testclient import TestClient; import uvicorn'
