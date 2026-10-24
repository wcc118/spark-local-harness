"""Explicit disposable test profile; this module never reads production secrets."""
import base64
import ipaddress
import json
from pathlib import Path
import urllib.parse
import urllib.request


class DisposableDatabase:
    def __init__(self, profile, database):
        config = json.loads(Path(profile).read_text())
        if set(config) != {'schema','disposable','endpoint','namespace','username','password'} or type(config['schema']) is not int or config['schema'] != 1 or config['disposable'] is not True:
            raise ValueError('Invalid disposable test database profile')
        url = urllib.parse.urlsplit(config['endpoint'])
        ip = ipaddress.ip_address(url.hostname)
        if url.scheme != 'http' or url.port != 8000 or url.path or url.query or url.fragment or url.username or url.password or ip.version != 4 or not ip.is_private or ip.is_loopback:
            raise ValueError('Test profile must address the isolated container, never production loopback')
        if config['namespace'] != 'memory_test' or config['username'] != 'memory_test' or not isinstance(config['password'],str) or len(config['password']) < 32:
            raise ValueError('Invalid synthetic test identity')
        if not database.startswith('memory_test_') or not database.replace('_','').isalnum():
            raise ValueError('Invalid test database name')
        self.config, self.database = config, database

    def query(self, sql, parameters=None, *, root=False):
        c = self.config
        h = {'Content-Type':'application/json','Accept':'application/json',
             'Authorization':'Basic '+base64.b64encode((c['username']+':'+c['password']).encode()).decode()}
        if not root:
            h.update({'surreal-ns':c['namespace'],'surreal-db':self.database})
        req = urllib.request.Request(c['endpoint']+'/rpc', headers=h,
            data=json.dumps({'id':1,'method':'query','params':[sql,parameters or {}]}).encode())
        with urllib.request.urlopen(req,timeout=25) as response:
            result = json.load(response)
        if 'error' in result or any(x['status']!='OK' for x in result.get('result',[])):
            raise RuntimeError('Disposable database query failed: '+str(result))
        return [x['result'] for x in result['result']]

    def create(self):
        self.query(f'DEFINE NAMESPACE IF NOT EXISTS memory_test; USE NS memory_test; DEFINE DATABASE {self.database};',root=True)

    def remove(self):
        self.query(f'USE NS memory_test; REMOVE DATABASE {self.database};',root=True)
