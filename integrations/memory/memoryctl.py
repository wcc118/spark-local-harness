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


TABLES = ['memory', 'memory_event', 'topic', 'project', 'concerns', 'discovered_in', 'supersedes', 'challenges',
          'memory_embedding']


def counts(database=DB, tables=TABLES):
    sql = f'USE NS {NS} DB {database}; ' + ' '.join(f'SELECT count() FROM {t} GROUP ALL;' for t in tables)
    rows = query(sql)[1:]
    return {t: (r['result'][0]['count'] if r['result'] else 0) for t, r in zip(tables, rows)}


def backup():
    """Export with a count snapshot; retry if writes landed during the export."""
    for _ in range(3):
        before = counts()
        h = headers('admin'); h['Accept'] = 'application/octet-stream'
        req = urllib.request.Request(URL + '/export', headers=h)
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode()
        if counts() == before:
            break
    else:
        raise RuntimeError('Knowledge changed during every export attempt; no consistent snapshot taken')
    name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = ROOT / 'backups' / (name + '.surql')
    private_write(path, body)
    private_write(path.with_suffix('.counts.json'), json.dumps(before, indent=2))
    return path


def verify_backup(path):
    """Import into a throwaway database, compare counts with the export snapshot, then remove it."""
    path = Path(path)
    expected = json.loads(path.with_suffix('.counts.json').read_text())
    database = 'restore_check_' + secrets.token_hex(6)
    query(f'USE NS {NS}; DEFINE DATABASE {database};')
    try:
        h = headers('admin'); h['surreal-db'] = database; h['Content-Type'] = 'text/plain'
        req = urllib.request.Request(URL + '/import', data=path.read_bytes(), headers=h, method='POST')
        with urllib.request.urlopen(req, timeout=120) as response:
            response.read()
        # A backup taken before a table existed recorded no count for it; count only what it recorded.
        restored = counts(database, list(expected))
    finally:
        query(f'USE NS {NS}; REMOVE DATABASE {database};')
    result = {'backup': path.name, 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'expected': expected, 'restored': restored, 'ok': restored == expected}
    private_write(path.with_suffix('.verify.json'), json.dumps(result, indent=2))
    if not result['ok']:
        raise RuntimeError(f'Restore check failed for {path.name}: expected {expected}, restored {restored}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bootstrap', 'query', 'backup', 'verify-backup', 'headers', 'connection',
                                            'backfill', 'stats', 'review', 'prune', 'offsite', 'link-similar'])
    parser.add_argument('path', nargs='?', help='verify-backup: export to check (default: newest)')
    parser.add_argument('--verify', action='store_true', help='backup: restore-check the new export')
    parser.add_argument('--apply', action='store_true', help='prune: actually delete (default prints the plan)')
    parser.add_argument('--min', type=float, default=0.85, help='link-similar: minimum cosine')
    parser.add_argument('--refresh', action='store_true', help='backfill: re-embed memories that already have a vector')
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
    elif args.command in {'backfill', 'stats', 'review', 'prune', 'offsite', 'link-similar'}:
        import maintenance
        if args.command == 'backfill':
            print(json.dumps(maintenance.backfill(refresh=args.refresh)))
        elif args.command == 'prune':
            print(json.dumps(maintenance.prune(apply=args.apply)))
        elif args.command == 'offsite':
            print(json.dumps(maintenance.offsite()))
        elif args.command == 'link-similar':
            print(json.dumps(maintenance.link_similar(minimum=args.min)))
        else:
            print(getattr(maintenance, args.command)())
    elif args.command == 'backup':
        path = backup()
        print(path)
        if args.verify:
            print('Restore check passed:', json.dumps(verify_backup(path)['restored']))
    elif args.command == 'verify-backup':
        path = args.path or max((ROOT / 'backups').glob('*.surql'))
        print('Restore check passed:', json.dumps(verify_backup(path)['restored']))


if __name__ == '__main__':
    main()
