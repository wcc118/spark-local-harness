"""Supply the Codex MCP credential without embedding it in client configuration."""
import json
from pathlib import Path

if __name__ == '__main__':
    config=json.loads((Path(__file__).resolve().parent/'.local/service-auth.json').read_text())
    print(json.dumps({'Authorization':'Bearer '+config['clients']['codex']['token']}))
