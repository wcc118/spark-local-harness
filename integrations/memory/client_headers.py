"""Supply an MCP client credential without embedding it in client configuration.

Usage: client_headers.py [CLIENT]   (default: codex)
"""
import json
import sys
from pathlib import Path

if __name__ == '__main__':
    client = sys.argv[1] if len(sys.argv) > 1 else 'codex'
    database = sys.argv[2] if len(sys.argv) > 2 else 'knowledge'
    from memoryctl import private_directory
    config=json.loads((private_directory(database)/'service-auth.json').read_text())
    print(json.dumps({'Authorization':'Bearer '+config['clients'][client]['token']}))
