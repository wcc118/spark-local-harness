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

    def worker(self,role='coder',run_id=None):
        response=self.client.post('/sessions',headers={'Authorization':'Bearer test-issuer'},
            json={'role':role,'run_id':run_id or secrets.token_hex(8),'repository':'test/repo','issue':'214'})
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
        run_id='use-'+secrets.token_hex(8)
        coder=self.worker(run_id=run_id);reviewer=self.worker('reviewer');helper=self.worker('helper')
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
        self.assertEqual(use_result['event']['run_id'],run_id)
        self.assertEqual(use_result['event']['repository'],'test/repo')
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
        self.assertEqual(inspected['memory'],first['memory'])
        frontier=self.value(self.call('test-frontier','record_memory_use',memory_id=ident,
            outcome='unhelpful',explanation='Did not apply in this environment',source='primary observation',request_id='frontier-use'))
        self.assertEqual(frontier['event']['role'],'frontier')
        self.assertEqual(frontier['event']['actor'],'codex')
        self.assertEqual(self.value(self.call(coder,'get_memory',memory_id=ident))['memory'],first['memory'])
        # The same retry key is scoped to the authenticated principal, not global.
        another=self.value(self.call(reviewer,'record_memory_use',**use_args))
        self.assertNotEqual(another['event']['id'],use_result['event']['id'])

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

    def test_record_memory_use_helper_discovery_does_not_grant_write(self):
        helper=self.worker('helper')
        names={t['name'] for t in self.rpc(helper,'tools/list',{})['result']['tools']}
        self.assertEqual(names,{'search_memory','get_memory','record_memory','correct_memory','record_memory_use','resolve_memory','discover_topics','enrich_memory'})
        coder=self.worker()
        ident=self.value(self.call(coder,'record_memory',text='Helper denial target',source='test',request_id='helper-target'))['memory']['id']
        before=self.value(self.call(coder,'get_memory',memory_id=ident))
        self.assertTrue(self.call(helper,'record_memory_use',memory_id=ident,outcome='helpful',explanation='test',source='test',request_id='test')['isError'])
        # Helper can discover topics but cannot enrich
        discover_result=self.value(self.call(helper,'discover_topics'))
        self.assertIn('topics',discover_result)
        self.assertTrue(self.call(helper,'enrich_memory',memory_id=ident,topics=['new'],explanation='test',source='test',request_id='enrich')['isError'])
        self.assertEqual(self.value(self.call(coder,'get_memory',memory_id=ident)),before)

    def test_api_has_no_delete_sql_or_role_spoof(self):
        token=self.worker()
        names={t['name'] for t in self.rpc(token,'tools/list',{})['result']['tools']}
        self.assertEqual(names,{'search_memory','get_memory','record_memory','correct_memory','record_memory_use','resolve_memory','discover_topics','enrich_memory'})
        self.assertEqual(self.client.post('/mcp',json={}).status_code,401)
        self.assertEqual(self.client.post('/sessions',headers={'Authorization':'Bearer '+token},json={'role':'frontier'}).status_code,401)
        self.assertEqual(self.client.post('/sessions',headers={'Authorization':'Bearer test-issuer'},json={'role':'frontier','run_id':'x'}).status_code,400)
        r=self.call(token,'record_memory',text='Injection is literal: "; REMOVE TABLE memory; --',source='test',request_id='literal',topics=["'; REMOVE TABLE topic; --"])
        ident=self.value(r)['memory']['id']
        self.assertIn('REMOVE TABLE',self.value(self.call(token,'get_memory',memory_id=ident))['memory']['text'])
        self.assertTrue(self.call(token,'get_memory',memory_id='memory:x; DELETE memory;')['isError'])

    def test_discover_topics_basic_and_case_insensitive(self):
        coder=self.worker()
        # Add a memory with a topic first
        self.value(self.call(coder,'record_memory',text='Test finding',source='test',request_id='base-topic',topics=['Performance']))
        # Discover all topics
        result=self.value(self.call(coder,'discover_topics'))
        self.assertIn('topics',result)
        self.assertTrue(len(result['topics'])>=1)
        topic_names={t['name'] for t in result['topics']}
        self.assertIn('Performance',topic_names)
        # Case-insensitive search
        result_lower=self.value(self.call(coder,'discover_topics',query='performance'))
        self.assertIn('Performance',[t['name'] for t in result_lower['topics']])
        # Empty query returns all
        result_empty=self.value(self.call(coder,'discover_topics',query=''))
        self.assertEqual(len(result_empty['topics']),len(result['topics']))
        # Limit validation
        result_limited=self.value(self.call(coder,'discover_topics',limit=1))
        self.assertLessEqual(len(result_limited['topics']),1)

    def test_discover_topics_helper_allowed(self):
        helper=self.worker('helper')
        result=self.value(self.call(helper,'discover_topics'))
        self.assertIn('topics',result)
        self.assertIsInstance(result['topics'],list)

    def test_discover_topics_invalid_limit_and_empty_query(self):
        coder=self.worker()
        r=self.call(coder,'discover_topics',limit=0)
        self.assertTrue(r['isError'])
        r=self.call(coder,'discover_topics',limit=101)
        self.assertTrue(r['isError'])
        r=self.call(coder,'discover_topics',limit=True)
        self.assertTrue(r['isError'])
        r=self.call(coder,'discover_topics',query='x'*101)
        self.assertTrue(r['isError'])

    def test_discover_topics_excludes_missing_topics(self):
        coder=self.worker()
        # Query for non-existent topic
        result=self.value(self.call(coder,'discover_topics',query='nonexistent-topic-x'))
        self.assertEqual(result['topics'],[])

    def test_enrich_memory_adds_topics_and_audit_event(self):
        coder=self.worker()
        reviewer=self.worker('reviewer',run_id='enrichment-review')
        # Create initial memory
        args=dict(text='Original finding',source='initial source',request_id='original',topics=['topic-a'],project='project-x',conditions='v1')
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        self.assertEqual(first['memory']['topics'],['topic-a'])
        # Enrich with additional topics
        enrich_result=self.value(self.call(reviewer,'enrich_memory',memory_id=ident,topics=['topic-b','topic-c'],explanation='Adding related topics',source='reviewer analysis',request_id='enrich-1'))
        self.assertEqual(enrich_result['memory']['id'],ident)
        # Topics should be unioned
        updated_topics=sorted(enrich_result['memory']['topics'])
        self.assertEqual(updated_topics,['topic-a','topic-b','topic-c'])
        # Event should have action=enrich (singular event, not plural)
        event=enrich_result.get('event')
        self.assertIsNotNone(event)
        self.assertEqual(event['action'],'enrich')
        self.assertEqual(event['topics'],['topic-b','topic-c'])
        self.assertEqual(event['explanation'],'Adding related topics')
        self.assertEqual(event['source'],'reviewer analysis')
        self.assertEqual(event['role'],'reviewer')
        self.assertEqual(event['run_id'],'enrichment-review')
        self.assertEqual(event['repository'],'test/repo')
        self.assertEqual(event['issue'],'test/repo#214')
        self.assertEqual(event['target'],ident)
        self.assertEqual(event['added_topics'],['topic-b','topic-c'])
        for key,value in first['memory'].items():
            if key != 'topics':
                self.assertEqual(enrich_result['memory'][key],value,key)
        # Verify audit event is also visible via get_memory
        inspected=self.value(self.call(coder,'get_memory',memory_id=ident))
        enrich_events=[e for e in inspected['events'] if e.get('action')=='enrich']
        self.assertTrue(len(enrich_events)>=1)
        # Search by new topic
        found=self.value(self.call(reviewer,'search_memory',query='Original',topics=['topic-b']))
        self.assertIn(ident,[m['id'] for m in found['memories']])

    def test_enrich_memory_idempotent_replay_and_rejection(self):
        coder=self.worker()
        args=dict(text='Replay test',source='test',request_id='replay-base',topics=['initial'])
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        # First enrichment
        enrich1=self.value(self.call(coder,'enrich_memory',memory_id=ident,topics=['added'],explanation='First enrichment',source='test',request_id='enrich-replay'))
        self.assertEqual(enrich1['memory']['id'],ident)
        self.assertIn('added',enrich1['memory']['topics'])
        # Replay with same request_id should return replayed=True
        enrich2=self.value(self.call(coder,'enrich_memory',memory_id=ident,topics=['added'],explanation='First enrichment',source='test',request_id='enrich-replay'))
        self.assertTrue(enrich2['replayed'])
        self.assertEqual(enrich2['memory']['id'],ident)
        self.assertIn('added',enrich2['memory']['topics'])
        # Different content with same request_id should fail
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=['different'],explanation='First enrichment',source='test',request_id='enrich-replay')
        self.assertTrue(r['isError'])
        # Different content with same request_id should fail - even with same topics
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=['added'],explanation='Different explanation',source='test',request_id='enrich-replay')
        self.assertTrue(r['isError'])

    def test_enrich_memory_deduplication_of_overlap_topics(self):
        coder=self.worker()
        args=dict(text='Overlap test',source='test',request_id='overlap-base',topics=['topic-a'])
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        # Enrich with overlapping topic (add b to existing a)
        enrich1=self.value(self.call(coder,'enrich_memory',memory_id=ident,topics=['topic-a','topic-b','topic-b'],explanation='Add b',source='test',request_id='enrich-overlap-1'))
        # Topics should be deduplicated (no duplicates, additive union)
        self.assertEqual(sorted(enrich1['memory']['topics']),['topic-a','topic-b'])
        # Record the initial concerns edge count
        initial_inspected=self.value(self.call(coder,'get_memory',memory_id=ident))
        initial_concerns=[e for e in initial_inspected['relations'] if e['id'].startswith('concerns:')]
        self.assertEqual(len(initial_concerns),2)
        # Enrich again with just the already-present topic
        enrich2=self.value(self.call(coder,'enrich_memory',memory_id=ident,topics=['topic-a'],explanation='Add a again',source='test',request_id='enrich-overlap-2'))
        # Should preserve existing topics (additive, no reduction)
        self.assertEqual(sorted(enrich2['memory']['topics']),['topic-a','topic-b'])
        # Should not create duplicate concerns edge
        after_inspected=self.value(self.call(coder,'get_memory',memory_id=ident))
        after_concerns=[e for e in after_inspected['relations'] if e['id'].startswith('concerns:')]
        self.assertEqual(len(after_concerns),len(initial_concerns))

    def test_enrich_memory_helper_denied_no_side_effect(self):
        helper=self.worker('helper')
        coder=self.worker()
        args=dict(text='Helper denial target',source='test',request_id='helper-target',topics=['target-topic'])
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        before_topics=sorted(first['memory']['topics'])
        before_events=self.value(self.call(coder,'get_memory',memory_id=ident))['events']
        # Helper cannot enrich
        r=self.call(helper,'enrich_memory',memory_id=ident,topics=['new-topic'],explanation='test',source='test',request_id='helper-enrich')
        self.assertTrue(r['isError'])
        # No side effects
        after=self.value(self.call(coder,'get_memory',memory_id=ident))
        self.assertEqual(sorted(after['memory']['topics']),before_topics)
        self.assertEqual(len(after['events']),len(before_events))

    def test_enrich_memory_rejects_missing_empty_invalid_topics(self):
        coder=self.worker()
        # Create a memory first
        args=dict(text='Target',source='test',request_id='target',topics=['original'])
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        # Missing target
        r=self.call(coder,'enrich_memory',memory_id='memory:nonexistent',topics=['x'],explanation='test',source='test',request_id='bad-target')
        self.assertTrue(r['isError'])
        # Empty topics
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=[],explanation='test',source='test',request_id='empty-topics')
        self.assertTrue(r['isError'])
        # Invalid topic format
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=['x'*101],explanation='test',source='test',request_id='long-topic')
        self.assertTrue(r['isError'])
        # Invalid topics type
        r=self.call(coder,'enrich_memory',memory_id=ident,topics='not-a-list',explanation='test',source='test',request_id='bad-type')
        self.assertTrue(r['isError'])
        # Empty explanation
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=['x'],explanation='',source='test',request_id='empty-explanation')
        self.assertTrue(r['isError'])
        # Empty source
        r=self.call(coder,'enrich_memory',memory_id=ident,topics=['x'],explanation='test',source='',request_id='empty-source')
        self.assertTrue(r['isError'])

    def test_enrich_memory_preserves_original_text_source_status(self):
        coder=self.worker()
        # Create with specific text/source/status
        args=dict(text='Original content that must persist',source='original source that persists',request_id='preserve-base',topics=['topic-a'],project='project-y')
        first=self.value(self.call(coder,'record_memory',**args))
        ident=first['memory']['id']
        original_text=first['memory']['text']
        original_source=first['memory']['source']
        original_status=first['memory']['status']
        original_project=first['memory']['project']
        # Enrich
        self.value(self.call(coder,'enrich_memory',memory_id=ident,topics=['topic-b'],explanation='Add topic',source='enrich source',request_id='enrich-preserve'))
        # Original fields preserved
        inspected=self.value(self.call(coder,'get_memory',memory_id=ident))
        self.assertEqual(inspected['memory']['text'],original_text)
        self.assertEqual(inspected['memory']['source'],original_source)
        self.assertEqual(inspected['memory']['status'],original_status)
        self.assertEqual(inspected['memory']['project'],original_project)


if __name__=='__main__':unittest.main()
