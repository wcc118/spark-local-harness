"""Supply an MCP client credential without embedding it in client configuration.

Usage: client_headers.py [CLIENT]   (default: codex)
"""
import json
import sys
from pathlib import Path

if __name__ == '__main__':
    client = sys.argv[1] if len(sys.argv) > 1 else 'codex'
    config=json.loads((Path(__file__).resolve().parent/'.local/service-auth.json').read_text())
    print(json.dumps({'Authorization':'Bearer '+config['clients'][client]['token']}))
