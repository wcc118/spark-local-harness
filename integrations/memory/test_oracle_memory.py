"""Oracle profile isolation; real-DB tests require the disposable capability."""
import base64
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from starlette.responses import JSONResponse
from starlette.testclient import TestClient
import memoryctl as ctl
import memory_service as service


class ProfileTests(unittest.TestCase):
    def test_private_files_and_backups_are_separate(self):
        self.assertNotEqual(ctl.private_directory('knowledge'), ctl.private_directory('oracle'))
        self.assertNotEqual(ctl.backup_directory('knowledge'), ctl.backup_directory('oracle'))
        self.assertEqual(ctl.private_directory(), ctl.ROOT / '.local')
        with self.assertRaises(ValueError): ctl.private_directory('../knowledge')
        with self.assertRaises(ValueError): service.Database('unregistered')

    def test_database_auth_scope_matches_selected_profile(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"result":[{"status":"OK","result":[]}]}'
        with patch.object(service,'credentials',return_value=('memory_writer','synthetic')) as credentials, patch.object(service.urllib.request,'urlopen',return_value=Response()) as request:
            service.Database('oracle').query('SELECT * FROM memory')
            credentials.assert_called_once_with('memory_writer','oracle')
            headers={k.lower():v for k,v in request.call_args.args[0].header_items()}
            self.assertEqual(headers['surreal-db'],'oracle')
            self.assertEqual(headers['surreal-auth-db'],'oracle')

    def test_oracle_refuses_workers_and_issuer_even_with_valid_secrets(self):
        config={'issuer_token':'i'*40,'signing_key':'s'*40,'clients':{
            'codex':{'role':'frontier','token':'f'*40},
            'worker':{'role':'coder','token':'w'*40}}}
        async def app(scope,receive,send):
            if scope['type'] == 'lifespan':
                while True:
                    event = await receive()
                    if event['type'] == 'lifespan.startup':
                        await send({'type':'lifespan.startup.complete'})
                    elif event['type'] == 'lifespan.shutdown':
                        await send({'type':'lifespan.shutdown.complete'})
                        return
            await JSONResponse({'role':service.CURRENT.get().role})(scope,receive,send)
        with TestClient(service.AuthenticatedApp(app,config,allow_workers=False)) as client:
            self.assertEqual(client.get('/mcp',headers={'Authorization':'Bearer '+'f'*40}).json(),{'role':'frontier'})
            self.assertEqual(client.get('/mcp',headers={'Authorization':'Bearer '+'w'*40}).status_code,401)
            self.assertEqual(client.get('/mcp',headers={'Authorization':'Bearer '+'x'*40}).status_code,401)
            token=service.signed_token(dict(role='coder',actor='coder',run_id='test',repository='a/b',issue='a/b#1',expires=int(time.time())+100),config['signing_key'])
            self.assertEqual(client.get('/mcp',headers={'Authorization':'Bearer '+token}).status_code,401)
            self.assertEqual(client.post('/sessions',headers={'Authorization':'Bearer '+config['issuer_token']},json={'role':'coder','run_id':'test'}).status_code,403)
            self.assertEqual(client.get('/health').status_code,200)

    def test_provisioned_oracle_has_independent_frontier_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ctl,'ROOT',Path(tmp)):
            shared=service.provision(); oracle=service.provision('oracle')
            self.assertNotEqual(shared['signing_key'],oracle['signing_key'])
            self.assertNotEqual(shared['clients']['codex']['token'],oracle['clients']['codex']['token'])
            self.assertEqual(set(oracle['clients']),{'codex','claude'})
            self.assertTrue(all(v['role']=='frontier' for v in oracle['clients'].values()))
            self.assertEqual(service.provision('oracle'),oracle)


@unittest.skipUnless(os.environ.get('MEMORY_TEST_PROFILE'), 'requires disposable database profile')
class RealIsolationTests(unittest.TestCase):
    def test_bootstrap_isolation_backup_and_keyword_fallback(self):
        # Validate the host-provisioned profile before any I/O; no production fallback.
        from test_database import DisposableDatabase
        profile=Path(os.environ['MEMORY_TEST_PROFILE'])
        db=DisposableDatabase(profile,'memory_test_oracle_'+secrets.token_hex(4))
        cfg=db.config
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.env').write_text('SURREAL_USER='+cfg['username']+'\nSURREAL_PASS='+cfg['password']+'\n')
            (root/'schema.surql').write_bytes(Path(__file__).with_name('schema.surql').read_bytes())
            with patch.object(ctl,'ROOT',root),patch.object(ctl,'URL',cfg['endpoint']),patch.object(ctl,'NS',cfg['namespace']),patch.object(service,'URL',cfg['endpoint']),patch.object(service,'NS',cfg['namespace']):
                try:
                    for name in ('knowledge','oracle'):
                        with patch.object(ctl,'DB',name):
                            ctl.bootstrap();ctl.bootstrap()
                            store=service.MemoryStore(service.Database(name),embedder=lambda *a,**k:None)
                            result=store.write(service.Principal(role='frontier',actor='codex'),'Unique isolation sentinel '+name,'synthetic-test','seed-'+name)
                            self.assertEqual(store.search('sentinel',limit=5)['search_mode'],'keyword')
                            store.embedder=lambda text, query=False: [1.0, 0.0, 0.0]
                            store.write(service.Principal(role='frontier',actor='codex'),'Independent vector sentinel '+name,'synthetic-test','vector-'+name)
                            self.assertEqual(store.search('semantic probe',limit=5)['search_mode'],'hybrid')
                            import maintenance
                            self.assertIn('memories: 2', maintenance.stats(db=service.Database(name)))
                            path=ctl.backup();report=ctl.verify_backup(path)
                            self.assertTrue(report['ok'])
                            self.assertEqual(report['restored']['memory'],2)
                            self.assertTrue(path.is_relative_to(ctl.backup_directory(name)))
                    for origin,target in [('knowledge','oracle'),('oracle','knowledge')]:
                        headers=ctl.headers('memory_writer',origin)
                        headers['surreal-db']=target
                        headers['Content-Type']='application/json'
                        req=urllib.request.Request(cfg['endpoint']+'/rpc',headers=headers,data=json.dumps({'id':1,'method':'query','params':['SELECT * FROM memory;',{}]}).encode())
                        try:
                            with urllib.request.urlopen(req,timeout=10) as response: payload=json.load(response)
                        except urllib.error.HTTPError as error:
                            self.assertIn(error.code,(401,403))
                        else:
                            self.assertTrue('error' in payload or any(r.get('status')!='OK' for r in payload.get('result',[])), 'cross-database query was not rejected')
                finally:
                    ctl.query('USE NS memory_test; REMOVE DATABASE IF EXISTS knowledge; REMOVE DATABASE IF EXISTS oracle;')


if __name__=='__main__':unittest.main()
