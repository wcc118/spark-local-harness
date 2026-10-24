"""Integration tests use an isolated database and never mutate production knowledge."""
import base64
import json
import os
from pathlib import Path
import secrets
import time
import unittest
import urllib.request

from starlette.testclient import TestClient
from memory_service import (AuthenticatedApp, MemoryStore, MemoryError, Principal,
                            build_server, signed_token, verify_token)
from memoryctl import credentials, query as admin_query


class TokenTests(unittest.TestCase):
    def test_signed_identity_and_expiration(self):
        claims = dict(role='reviewer',actor='reviewer',run_id='run-1',repository='a/b',issue='1',expires=time.time()+60)
        token = signed_token(claims,'key')
        self.assertEqual(verify_token(token,'key').run_id,'run-1')
        self.assertIsNone(verify_token(token+'0','key'))
        claims['expires']=0
        self.assertIsNone(verify_token(signed_token(claims,'key'),'key'))
        claims.update(role='frontier',expires=time.time()+60)
        self.assertIsNone(verify_token(signed_token(claims,'key'),'key'))


class TestDatabase:
    def __init__(self,name): self.name=name
    def query(self,sql,parameters=None):
        user,password=credentials('admin')
        headers={'Content-Type':'application/json','Accept':'application/json',
                 'Authorization':'Basic '+base64.b64encode(f'{user}:{password}'.encode()).decode(),
                 'surreal-ns':'personal','surreal-db':self.name}
        req=urllib.request.Request('http://127.0.0.1:9600/rpc',headers=headers,
            data=json.dumps({'id':1,'method':'query','params':[sql,parameters or {}]}).encode())
        with urllib.request.urlopen(req,timeout=10) as r: result=json.load(r)
        if 'error' in result or any(x['status']!='OK' for x in result.get('result',[])):
            raise MemoryError('Test database rejected query: '+str(result))
        return [x['result'] for x in result['result']]


@unittest.skipUnless(os.environ.get('MEMORY_INTEGRATION')=='1','requires local test database')
class MemoryIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name='memory_test_'+secrets.token_hex(6)
        admin_query(f'DEFINE DATABASE {cls.name};')
        cls.db=TestDatabase(cls.name)
        cls.db.query(Path('schema.surql').read_text())
        cls.store=MemoryStore(cls.db)
        cls.config={'issuer_token':'test-issuer','signing_key':'test-signing',
                    'clients':{'codex':{'role':'frontier','token':'test-frontier'}}}
        cls.server=build_server(cls.store)
        cls.app=AuthenticatedApp(cls.server.streamable_http_app(stateless_http=True,json_response=True),cls.config)
        cls.client=TestClient(cls.app,base_url='http://127.0.0.1:9602')
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)
        admin_query(f'REMOVE DATABASE {cls.name};')

    def worker(self,role='coder'):
        response=self.client.post('/sessions',headers={'Authorization':'Bearer test-issuer'},
            json={'role':role,'run_id':'run-1','repository':'test/repo','issue':'214'})
        self.assertEqual(response.status_code,200)
        return response.json()['token']

    def rpc(self,token,method,params):
        r=self.client.post('/mcp',headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'},
            json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def call(self,token,name,**args):
        return self.rpc(token,'tools/call',{'name':name,'arguments':args})['result']

    def value(self,result):
        self.assertFalse(result.get('isError'),result)
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    def test_lifecycle_and_role_enforcement(self):
        coder=self.worker();reviewer=self.worker('reviewer');helper=self.worker('helper')
        args=dict(text='Crossprojectwidget has a reproducible boundary failure',source='test evidence',request_id='discovery',topics=['boundary'],project='project-a',conditions='version one')
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        self.assertEqual(first['memory']['role'],'coder')
        again=self.value(self.call(coder,'record_memory',**args))
        self.assertTrue(again['replayed']);self.assertEqual(again['memory']['id'],ident)
        self.assertTrue(self.call(coder,'record_memory',**dict(args,text='Different'))['isError'])
        found=self.value(self.call(reviewer,'search_memory',query='Crossprojectwidget',topics=['boundary']))
        self.assertIn(ident,[m['id'] for m in found['memories']])
        filtered=self.value(self.call(reviewer,'search_memory',query='Crossprojectwidget',exclude_issue='test/repo#214'))
        self.assertFalse(filtered['memories'])
        self.assertTrue(self.call(helper,'record_memory',**args)['isError'])
        fix=self.value(self.call(reviewer,'correct_memory',target_id=ident,text='Crossprojectwidget works when its boundary is normalized',source='independent reproduction',request_id='correction'))['memory']['id']
        inspected=self.value(self.call(coder,'get_memory',memory_id=ident))
        self.assertEqual(inspected['memory']['status'],'disputed')
        self.assertTrue(inspected['relations']);self.assertTrue(inspected['events'])
        self.assertTrue(self.call(reviewer,'resolve_memory',target_id=ident,replacement_id=fix,rationale='verified',request_id='resolve')['isError'])
        resolved=self.value(self.call('test-frontier','resolve_memory',target_id=ident,replacement_id=fix,rationale='verified correction',request_id=ident))
        self.assertEqual(resolved['target']['status'],'superseded')
        active=self.value(self.call(coder,'search_memory',query='Crossprojectwidget'))
        self.assertNotIn(ident,[m['id'] for m in active['memories']])
        history=self.value(self.call(coder,'search_memory',query='Crossprojectwidget',include_history=True))
        self.assertIn(ident,[m['id'] for m in history['memories']])
        self.assertTrue(self.call('test-frontier','resolve_memory',target_id=fix,replacement_id=ident,rationale='cycle',request_id='cycle')['isError'])

    def test_record_memory_use_lifecycle_and_role_enforcement(self):
        coder=self.worker();reviewer=self.worker('reviewer');helper=self.worker('helper')
        args=dict(text='Crossprojectwidget has a reproducible boundary failure',source='test evidence',request_id='discovery',topics=['boundary'],project='project-a',conditions='version one')
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        use_args=dict(memory_id=ident,outcome='helpful',explanation='Reproduced in test run 2026-10-24',source='worker observation',request_id='use-1')
        use_result=self.value(self.call(coder,'record_memory_use',**use_args))
        self.assertEqual(use_result['event']['action'],'use')
        self.assertEqual(use_result['event']['outcome'],'helpful')
        self.assertEqual(use_result['event']['role'],'coder')
        self.assertEqual(use_result['event']['evidence_kind'],'agent_report')
        self.assertEqual(use_result['event']['actor'],use_result['event']['role'])
        self.assertEqual(use_result['event']['run_id'],'run-1')
        self.assertEqual(use_result['event']['issue'],'test/repo#214')
        replayed=self.value(self.call(coder,'record_memory_use',**use_args))
        self.assertTrue(replayed['replayed']);self.assertEqual(replayed['event']['id'],use_result['event']['id'])
        self.assertTrue(self.call(coder,'record_memory_use',**dict(use_args,outcome='unhelpful'))['isError'])
        inspected=self.value(self.call(reviewer,'get_memory',memory_id=ident))
        self.assertTrue(inspected['events'])
        self.assertIn('use',[e.get('action') for e in inspected['events']])
        self.assertTrue(self.call(helper,'record_memory_use',**use_args)['isError'])
        use_coder=self.value(self.call(coder,'record_memory_use',memory_id=ident,outcome='uncertain',explanation='Mixed results in two runs',source='worker observation',request_id='use-2'))
        self.assertEqual(use_coder['event']['outcome'],'uncertain')
        use_reviewer=self.value(self.call(reviewer,'record_memory_use',memory_id=ident,outcome='misleading',explanation='Root cause differs from initial report',source='reviewer analysis',request_id='use-3'))
        self.assertEqual(use_reviewer['event']['outcome'],'misleading')
        inspected=self.value(self.call(reviewer,'get_memory',memory_id=ident))
        self.assertTrue(inspected['events'])
        self.assertEqual(len([e for e in inspected['events'] if e.get('action')=='use']),3)

    def test_record_memory_use_invalid_outcome_and_missing_target(self):
        coder=self.worker()
        r=self.call(coder,'record_memory_use',memory_id='memory:nonexistent',outcome='helpful',explanation='test',source='test',request_id='bad-target')
        self.assertTrue(r['isError'])
        r=self.call(coder,'record_memory_use',memory_id='memory:x',outcome='invalid',explanation='test',source='test',request_id='bad-outcome')
        self.assertTrue(r['isError'])
        r=self.call(coder,'record_memory_use',memory_id='memory:x',outcome='helpful',explanation='',source='test',request_id='empty-explanation')
        self.assertTrue(r['isError'])
        r=self.call(coder,'record_memory_use',memory_id='memory:x',outcome='helpful',explanation='test',source='',request_id='empty-source')
        self.assertTrue(r['isError'])

    def test_record_memory_use_not_in_tools_list_for_helper(self):
        helper=self.worker('helper')
        names={t['name'] for t in self.rpc(helper,'tools/list',{})['result']['tools']}
        self.assertEqual(names,{'search_memory','get_memory','record_memory','correct_memory','record_memory_use','resolve_memory'})
        self.assertTrue(self.call(helper,'record_memory_use',memory_id='memory:x',outcome='helpful',explanation='test',source='test',request_id='test')['isError'])

    def test_api_has_no_delete_sql_or_role_spoof(self):
        token=self.worker()
        names={t['name'] for t in self.rpc(token,'tools/list',{})['result']['tools']}
        self.assertEqual(names,{'search_memory','get_memory','record_memory','correct_memory','record_memory_use','resolve_memory'})
        self.assertEqual(self.client.post('/mcp',json={}).status_code,401)
        self.assertEqual(self.client.post('/sessions',headers={'Authorization':'Bearer '+token},json={'role':'frontier'}).status_code,401)
        self.assertEqual(self.client.post('/sessions',headers={'Authorization':'Bearer test-issuer'},json={'role':'frontier','run_id':'x'}).status_code,400)
        r=self.call(token,'record_memory',text='Injection is literal: "; REMOVE TABLE memory; --',source='test',request_id='literal',topics=["'; REMOVE TABLE topic; --"])
        ident=self.value(r)['memory']['id']
        self.assertIn('REMOVE TABLE',self.value(self.call(token,'get_memory',memory_id=ident))['memory']['text'])
        self.assertTrue(self.call(token,'get_memory',memory_id='memory:x; DELETE memory;')['isError'])


if __name__=='__main__':unittest.main()
