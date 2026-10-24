#!/usr/bin/env python3
"""Local administration; credentials are read from ignored files, never logged."""
import argparse
import base64
import datetime
import json
import os
from pathlib import Path
import secrets
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:9600'
NS, DB = 'personal', 'knowledge'


def credentials(role):
    if role == 'admin':
        env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines())
        return env['SURREAL_USER'], env['SURREAL_PASS']
    return role, json.loads((ROOT / '.local/accounts.json').read_text())[role]


def headers(role='admin'):
    user, password = credentials(role)
    h = {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode(),
         'Accept': 'application/json', 'surreal-ns': NS, 'surreal-db': DB}
    if role != 'admin':
        h.update({'surreal-auth-ns': NS, 'surreal-auth-db': DB})
    return h


def query(sql, role='admin'):
    req = urllib.request.Request(URL + '/sql', data=sql.encode(), headers=headers(role))
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if any(item.get('status') != 'OK' for item in result):
        # Do not echo SQL: user provisioning includes passwords.
        raise RuntimeError('Database rejected a statement: ' + '; '.join(str(x.get('result')) for x in result if x.get('status') != 'OK'))
    return result


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    path.chmod(0o600)


def bootstrap():
    query(f'DEFINE NAMESPACE IF NOT EXISTS {NS}; USE NS {NS}; DEFINE DATABASE IF NOT EXISTS {DB};')
    query((ROOT / 'schema.surql').read_text())
    path = ROOT / '.local/accounts.json'
    if not path.exists():
        private_write(path, json.dumps({r: secrets.token_urlsafe(36) for r in ['memory_writer', 'memory_reader']}, indent=2))
    accounts = json.loads(path.read_text())
    for name, role in [('memory_writer', 'EDITOR'), ('memory_reader', 'VIEWER')]:
        query(f'DEFINE USER IF NOT EXISTS {name} ON DATABASE PASSWORD {json.dumps(accounts[name])} ROLES {role};')
    print('Schema and database-scoped reader/writer accounts ready.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bootstrap', 'query', 'backup', 'headers', 'connection'])
    parser.add_argument('--role', choices=['admin', 'memory_writer', 'memory_reader'], default='memory_reader')
    args = parser.parse_args()
    if args.command == 'bootstrap':
        bootstrap()
    elif args.command == 'query':
        print(json.dumps(query(sys.stdin.read(), args.role), indent=2))
    elif args.command == 'headers':
        print(json.dumps(headers(args.role)))
    elif args.command == 'connection':
        user, password = credentials(args.role)
        print(f'Endpoint: {URL}\nNamespace: {NS}\nDatabase: {DB}\nAuthentication: '+('Root' if args.role == 'admin' else 'Database'))
        print(f'Username: {user}\nPassword: {password}')
    elif args.command == 'backup':
        h = headers('admin'); h['Accept'] = 'application/octet-stream'
        req = urllib.request.Request(URL + '/export', headers=h)
        name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        path = ROOT / 'backups' / (name + '.surql')
        with urllib.request.urlopen(req, timeout=60) as response:
            private_write(path, response.read().decode())
        print(path)


if __name__ == '__main__':
    main()
