"""Role-authenticated memory MCP. No arbitrary SQL, overwrite or deletion tools."""
from __future__ import annotations

import base64
import contextvars
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import StrictInt
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

from memoryctl import ROOT, credentials, private_write


@dataclass(frozen=True)
class Principal:
    role: str
    actor: str
    run_id: str = ''
    repository: str = ''
    issue: str = ''


CURRENT = contextvars.ContextVar('memory_principal')
READ_TOOLS = ['search_memory', 'get_memory', 'discover_topics']
WRITE_TOOLS = ['record_memory', 'correct_memory', 'record_memory_use', 'enrich_memory']
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS + ['resolve_memory']
ID = re.compile(r'^memory:[A-Za-z0-9_]+$')


class MemoryError(ToolError, ValueError):
    pass


def bounded(value, name, maximum=16000, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise MemoryError(f'{name} must be a nonempty string of at most {maximum} characters' if required else f'Invalid {name}')
    return value.strip()


def declared_work_context(value):
    """Validate author-supplied context; never merge it into a Principal."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not value or set(value) - {'repository', 'issue', 'session_id'}:
        raise MemoryError('declared_context must contain repository, issue and/or session_id only')
    context = {key: bounded(item, 'declared_context.' + key, 200)
               for key, item in sorted(value.items())}
    repository_pattern = r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
    if 'repository' in context and not re.fullmatch(repository_pattern, context['repository']):
        raise MemoryError('declared_context.repository must be OWNER/REPO')
    if 'issue' in context:
        if not re.fullmatch(repository_pattern + r'#[1-9][0-9]*', context['issue']):
            raise MemoryError('declared_context.issue must be OWNER/REPO#N')
        if 'repository' in context and context['issue'].rsplit('#', 1)[0] != context['repository']:
            raise MemoryError('declared_context repository and issue must agree')
    # Omission leaves old fingerprints byte-for-byte compatible. Context is a
    # separate author claim, not authenticated attribution or an authorization.
    return {'declared_context': context}


def record_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise MemoryError('Expected a memory record ID returned by this service')
    return value


def signed_token(claims, key):
    body = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode()).decode().rstrip('=')
    return body + '.' + hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


def verify_token(token, key):
    try:
        body, signature = token.split('.')
        if not hmac.compare_digest(signature, hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if claims['expires'] < time.time() or claims['role'] not in {'coder', 'reviewer', 'helper'}:
            return None
        return Principal(**{k: claims[k] for k in ['role', 'actor', 'run_id', 'repository', 'issue']})
    except (ValueError, KeyError, TypeError):
        return None


class Database:
    def __init__(self, database='knowledge'):
        self.database = database

    def query(self, sql, parameters=None):
        user, password = credentials('memory_writer')
        headers = {
            'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode(),
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'surreal-ns': 'personal', 'surreal-db': self.database,
            'surreal-auth-ns': 'personal', 'surreal-auth-db': 'knowledge',
        }
        payload = {'id': 'memory', 'method': 'query', 'params': [sql, parameters or {}]}
        req = urllib.request.Request('http://127.0.0.1:9600/rpc', data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                result = json.load(response)
        except (OSError, ValueError) as exc:
            raise MemoryError('Memory database unavailable; continue your task and retry later') from exc
        if 'error' in result or any(r.get('status') != 'OK' for r in result.get('result', [])):
            raise MemoryError('Database operation failed; no success is claimed')
        return [r.get('result') for r in result['result']]


class MemoryStore:
    def __init__(self, database=None):
        self.db = database or Database()
        self.lock = threading.RLock()

    def get_record(self, ident):
        ident = record_id(ident)
        rows = self.db.query('SELECT * FROM type::record($id);', {'id': ident})[0]
        if not rows:
            raise MemoryError('Memory not found')
        return rows[0]

    def search(self, query, project='', topics=None, include_history=False, limit=8, exclude_issue=''):
        query = bounded(query, 'query', 1000)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            raise MemoryError('limit must be between 1 and 30')
        project = bounded(project, 'project', 200, False)
        topics = self.topics(topics or [])
        # Full text plus literal substring, parameter-bound (never query interpolation).
        rows = self.db.query('''SELECT *, search::score(0) AS relevance FROM memory
            WHERE ($history OR status IN ['active', 'disputed'])
            AND (text @0@ $q OR string::contains(string::lowercase(text), string::lowercase($q)))
            AND ($project = '' OR project = $project)
            AND (array::len($topics) = 0 OR array::any(topics, |$t| $t IN $topics))
            AND ($exclude = '' OR ((issue = NONE OR issue != $exclude)
                AND (declared_context.issue = NONE OR declared_context.issue != $exclude)))
            ORDER BY relevance DESC LIMIT $limit;''',
            {'q': query, 'project': project, 'topics': topics, 'history': include_history,
             'exclude': bounded(exclude_issue, 'exclude_issue', 200, False), 'limit': limit})[0]
        return {'memories': rows, 'hint': 'Disputed records are unresolved evidence. Inspect links before applying. Project filters are optional. declared_context is author-supplied, not authenticated task attribution.'}

    @staticmethod
    def topics(values):
        if not isinstance(values, list) or len(values) > 20:
            raise MemoryError('topics must be a list with at most 20 entries')
        return list(dict.fromkeys(bounded(v, 'topic', 100) for v in values))

    def discover_topics(self, query='', limit=30):
        query = bounded(query, 'query', 100, False)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise MemoryError('limit must be between 1 and 100')
        rows = self.db.query('''SELECT id, name FROM topic
            WHERE $q = '' OR string::contains(string::lowercase(name), string::lowercase($q))
            ORDER BY name ASC LIMIT $limit;''', {'q': query, 'limit': limit})[0]
        return {'topics': rows, 'hint': 'Existing names are suggestions, not a fixed taxonomy. Inspect applicability; similar names need not mean the same thing.'}

    def enrich(self, principal, memory_id, topics, explanation, source, request_id, declared_context=None):
        """Add topic metadata and audit its source without replacing a claim."""
        if principal.role not in {'coder', 'reviewer', 'frontier'}:
            raise MemoryError('This identity has read-only memory access')
        memory_id = record_id(memory_id)
        topics = self.topics(topics)
        if not topics:
            raise MemoryError('Supply at least one topic to add')
        explanation = bounded(explanation, 'explanation', 4000)
        source = bounded(source, 'source', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        payload = dict(memory_id=memory_id, topics=topics, explanation=explanation, source=source)
        context = declared_work_context(declared_context)
        payload.update(context)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([principal.__dict__, request_id, 'enrich'], sort_keys=True).encode()).hexdigest()[:32]
        event_id = 'memory_event:' + key
        with self.lock:
            previous = self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0]
            if previous:
                if previous[0].get('fingerprint') != fingerprint:
                    raise MemoryError('request_id already used for different enrichment')
                return {'memory': self.get_record(memory_id), 'event': previous[0], 'replayed': True}
            record = self.get_record(memory_id)
            linked = {edge['out'] for edge in self.db.query(
                'SELECT out FROM concerns WHERE in = type::record($id);', {'id': memory_id})[0]}
            event = dict(action='enrich', topics=topics,
                         added_topics=[topic for topic in topics if topic not in record.get('topics', [])],
                         explanation=explanation, source=source, fingerprint=fingerprint,
                         actor=principal.actor, role=principal.role, run_id=principal.run_id,
                         repository=principal.repository, issue=principal.issue, **context)
            params = dict(id=memory_id, topics=topics, event=event, event_id=event_id)
            statements = ['BEGIN TRANSACTION;',
                          'UPDATE type::record($id) SET topics=array::union(topics, $topics);']
            for i, topic in enumerate(topics):
                topic_id = 'topic:' + hashlib.sha256(topic.encode()).hexdigest()[:24]
                params[f'topic_id_{i}'] = topic_id
                params[f'topic_{i}'] = topic
                statements.append(f'UPSERT type::record($topic_id_{i}) SET name=$topic_{i};')
                if topic_id not in linked:
                    statements.append(f'RELATE (type::record($id))->concerns->(type::record($topic_id_{i}));')
            statements += ['CREATE type::record($event_id) CONTENT $event;',
                           'UPDATE type::record($event_id) SET target=type::record($id), created_at=time::now();',
                           'COMMIT TRANSACTION;']
            self.db.query('\n'.join(statements), params)
            stored = self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0][0]
            return {'memory': self.get_record(memory_id), 'event': stored, 'replayed': False}

    def inspect(self, memory_id):
        record = self.get_record(memory_id)
        edges = self.db.query('''SELECT * FROM discovered_in, concerns, supersedes, challenges
            WHERE in = type::record($id) OR out = type::record($id);
            SELECT * FROM memory_event WHERE target = type::record($id) ORDER BY created_at DESC LIMIT 50;''', {'id': memory_id})
        neighbors = set()
        for edge in edges[0]:
            neighbors.update(v for k,v in edge.items() if k in {'in','out'} and isinstance(v,str))
        related = []
        for ident in sorted(neighbors - {memory_id})[:30]:
            related.extend(self.db.query('SELECT * FROM type::record($id);', {'id': ident})[0])
        return {'memory': record, 'relations': edges[0], 'related': related, 'events': edges[1]}

    def write(self, principal, text, source, request_id, topics=None, project='', kind='finding', conditions='', target=None, relation=None, declared_context=None):
        if principal.role not in {'coder','reviewer','frontier'}:
            raise MemoryError('This identity has read-only memory access')
        text = bounded(text, 'text')
        source = bounded(source, 'source', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        project = bounded(project, 'project', 200, False)
        conditions = bounded(conditions, 'conditions', 4000, False)
        topics = self.topics(topics or [])
        if kind not in {'finding','decision','preference','hypothesis','correction','review'}:
            raise MemoryError('Invalid memory kind')
        payload = dict(text=text, source=source, topics=topics, project=project, kind=kind, conditions=conditions,
                       target=target, relation=relation)
        context = declared_work_context(declared_context)
        payload.update(context)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([principal.__dict__,request_id],sort_keys=True).encode()).hexdigest()[:32]
        ident = 'memory:' + key
        with self.lock:
            existing = self.db.query('SELECT * FROM type::record($id);', {'id': ident})[0]
            if existing:
                if existing[0].get('fingerprint') != fingerprint:
                    raise MemoryError('request_id already used for different content; use a new request_id')
                return {'memory': existing[0], 'replayed': True}
            if target:
                self.get_record(target)
            data = dict(text=text, source=source, topics=topics, kind=kind, status='active', conditions=conditions,
                        actor=principal.actor, role=principal.role, run_id=principal.run_id,
                        repository=principal.repository, issue=principal.issue, fingerprint=fingerprint, **context)
            if project:
                data['project'] = project
            params = {'id': ident, 'data': data}
            statements = ['BEGIN TRANSACTION;', 'CREATE type::record($id) CONTENT $data;']
            if project:
                pid = 'project:' + hashlib.sha256(project.encode()).hexdigest()[:24]
                params.update(project_id=pid, project_name=project)
                statements += ['UPSERT type::record($project_id) SET name=$project_name;',
                               'RELATE (type::record($id))->discovered_in->(type::record($project_id));']
            for i, topic in enumerate(topics):
                params[f'topic_id_{i}'] = 'topic:' + hashlib.sha256(topic.encode()).hexdigest()[:24]
                params[f'topic_{i}'] = topic
                statements += [f'UPSERT type::record($topic_id_{i}) SET name=$topic_{i};',
                               f'RELATE (type::record($id))->concerns->(type::record($topic_id_{i}));']
            if target:
                params['target'] = target
                # A worker can challenge, but cannot unilaterally supersede another claim.
                statements += ['RELATE (type::record($id))->challenges->(type::record($target));',
                               "UPDATE type::record($target) SET status='disputed' WHERE status='active';"]
            params['event'] = dict(action=relation or 'record', actor=principal.actor, role=principal.role,
                                   run_id=principal.run_id, issue=principal.issue, source=source, **context)
            statements += ['CREATE memory_event SET action=$event.action, actor=$event.actor, role=$event.role, run_id=$event.run_id, issue=$event.issue, source=$event.source, declared_context=$event.declared_context, target=type::record($id), created_at=time::now();', 'COMMIT TRANSACTION;']
            self.db.query('\n'.join(statements), params)
            return {'memory': self.get_record(ident), 'replayed': False}

    def record_use(self, principal, memory_id, outcome, explanation, source, request_id, declared_context=None):
        """Append an attributed observation; reporting use never promotes a claim."""
        if principal.role not in {'coder', 'reviewer', 'frontier'}:
            raise MemoryError('This identity has read-only memory access')
        memory_id = record_id(memory_id)
        if outcome not in {'helpful', 'unhelpful', 'misleading', 'uncertain'}:
            raise MemoryError('outcome must be helpful, unhelpful, misleading or uncertain')
        explanation = bounded(explanation, 'explanation', 4000)
        source = bounded(source, 'source', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        payload = dict(memory_id=memory_id, outcome=outcome, explanation=explanation, source=source)
        context = declared_work_context(declared_context)
        payload.update(context)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([principal.__dict__, request_id, 'use'], sort_keys=True).encode()).hexdigest()[:32]
        event_id = 'memory_event:' + key
        with self.lock:
            previous = self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0]
            if previous:
                if previous[0].get('fingerprint') != fingerprint:
                    raise MemoryError('request_id already used for a different use observation')
                return {'event': previous[0], 'replayed': True}
            self.get_record(memory_id)
            event = dict(action='use', outcome=outcome, explanation=explanation, source=source,
                         evidence_kind='agent_report', fingerprint=fingerprint,
                         actor=principal.actor, role=principal.role, run_id=principal.run_id,
                         repository=principal.repository, issue=principal.issue, **context)
            self.db.query('''BEGIN TRANSACTION;
                CREATE type::record($event_id) CONTENT $event;
                UPDATE type::record($event_id) SET target=type::record($target), created_at=time::now();
                COMMIT TRANSACTION;''',
                dict(event_id=event_id, event=event, target=memory_id))
            return {'event': self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0][0],
                    'replayed': False}

    def resolve(self, principal, target_id, replacement_id, rationale, request_id, declared_context=None):
        if principal.role != 'frontier':
            raise MemoryError('Only the frontier reconciliation identity may supersede memories')
        record_id(target_id); record_id(replacement_id)
        rationale = bounded(rationale, 'rationale', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        if target_id == replacement_id:
            raise MemoryError('A memory cannot supersede itself')
        key = hashlib.sha256(json.dumps([principal.__dict__,request_id,'resolve'],sort_keys=True).encode()).hexdigest()[:32]
        context = declared_work_context(declared_context)
        payload = [target_id, replacement_id, rationale]
        if context:
            payload.append(context)
        fingerprint = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        event_id = 'memory_event:' + key
        with self.lock:
            previous = self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0]
            if previous:
                if previous[0]['fingerprint'] != fingerprint:
                    raise MemoryError('request_id already used for a different reconciliation')
                return {'event': previous[0], 'replayed': True}
            target, replacement = self.get_record(target_id), self.get_record(replacement_id)
            if target['status'] == 'superseded' or replacement['status'] not in {'active', 'disputed'}:
                raise MemoryError('Target must not already be superseded; replacement must be active or disputed')
            event = dict(action='supersede', actor=principal.actor, role=principal.role, rationale=rationale,
                         fingerprint=fingerprint, run_id=principal.run_id, issue=principal.issue, **context)
            self.db.query('''BEGIN TRANSACTION;
                UPDATE type::record($target) SET status='superseded';
                UPDATE type::record($replacement) SET status='active';
                RELATE (type::record($replacement))->supersedes->(type::record($target));
                CREATE type::record($event_id) SET action=$event.action, actor=$event.actor, role=$event.role, rationale=$event.rationale, fingerprint=$event.fingerprint, run_id=$event.run_id, issue=$event.issue, declared_context=$event.declared_context, target=type::record($target), replacement=type::record($replacement), created_at=time::now();
                COMMIT TRANSACTION;''', dict(target=target_id,replacement=replacement_id,event_id=event_id,event=event))
            return {'target': self.get_record(target_id), 'replacement': replacement_id, 'replayed': False}


def build_server(store):
    server = MCPServer(name='memory', version='1.0.0', instructions='Optional declared_context on writes is author-supplied task metadata, never authenticated identity. Shared cross-project lessons. Choose your own searches; inspect evidence and conditions. Record useful findings. No SQL or deletion tool. Memory failures do not block your task.')

    @server.tool()
    def search_memory(query: str, project: str = '', topics: list[str] | None = None, include_history: bool = False, limit: int = 8, exclude_issue: str = '') -> dict:
        """Search across projects by default. Active and disputed findings are returned; history is opt-in. exclude_issue can preserve current-review independence."""
        return store.search(query, project, topics, include_history, limit, exclude_issue)

    @server.tool()
    def get_memory(memory_id: str) -> dict:
        """Inspect a memory, provenance, graph neighbors and recent events, including attributed use reports. Reports are not independent verification."""
        return store.inspect(memory_id)

    @server.tool()
    def discover_topics(query: str = '', limit: StrictInt = 30) -> dict:
        """Discover existing topic names with optional case-insensitive substring matching. Reuse names when applicable; this is not a fixed taxonomy or semantic alias resolver."""
        return store.discover_topics(query, limit)

    @server.tool()
    def enrich_memory(memory_id: str, topics: list[str], explanation: str, source: str, request_id: str, declared_context: dict[str, str] | None = None) -> dict:
        """Add justified topics and graph links to an existing lesson with an audit trail. Preserves original text/source/status; no removal. Reuse request_id only for the identical retry."""
        return store.enrich(CURRENT.get(), memory_id, topics, explanation, source, request_id, declared_context)

    @server.tool()
    def record_memory(text: str, source: str, request_id: str, topics: list[str] | None = None, project: str = '', kind: str = 'finding', conditions: str = '', declared_context: dict[str, str] | None = None) -> dict:
        """Record reusable knowledge with evidence and applicability. Caller attribution is authenticated. Reuse request_id only to retry the identical write."""
        return store.write(CURRENT.get(), text, source, request_id, topics, project, kind, conditions, declared_context=declared_context)

    @server.tool()
    def correct_memory(target_id: str, text: str, source: str, request_id: str, topics: list[str] | None = None, project: str = '', conditions: str = '', declared_context: dict[str, str] | None = None) -> dict:
        """Add contrary evidence or a proposed correction. Preserve the original and mark it disputed; no approval is required to contribute."""
        return store.write(CURRENT.get(), text, source, request_id, topics, project, 'correction', conditions, record_id(target_id), 'challenge', declared_context=declared_context)

    @server.tool()
    def record_memory_use(memory_id: str, outcome: str, explanation: str, source: str, request_id: str, declared_context: dict[str, str] | None = None) -> dict:
        """Report actual lesson use as helpful/unhelpful/misleading/uncertain, with evidence and limits. Append-only attributed report, not proof or a vote; does not change the lesson. Retry identical content with the same request_id."""
        return store.record_use(CURRENT.get(), memory_id, outcome, explanation, source, request_id, declared_context)

    @server.tool()
    def resolve_memory(target_id: str, replacement_id: str, rationale: str, request_id: str, declared_context: dict[str, str] | None = None) -> dict:
        """Frontier reconciliation only: supersede an outdated claim with a supported active/disputed replacement. Preserves history; never deletes."""
        return store.resolve(CURRENT.get(), target_id, replacement_id, rationale, request_id, declared_context)

    return server


class AuthenticatedApp:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        if request.url.path == '/health' and request.method == 'GET':
            return await JSONResponse({'status':'ok'})(scope, receive, send)
        token = request.headers.get('authorization','').removeprefix('Bearer ')
        if not token.isascii():
            return await JSONResponse({'error':'unauthorized'},status_code=401)(scope,receive,send)
        if request.url.path == '/sessions' and request.method == 'POST':
            if not hmac.compare_digest(token, self.config['issuer_token']):
                return await JSONResponse({'error':'unauthorized'},status_code=401)(scope,receive,send)
            try:
                body = await request.body()
                if len(body) > 8192: raise MemoryError('Request too large')
                data = json.loads(body)
                if not isinstance(data, dict): raise MemoryError('Expected an object')
                if data.get('role') not in {'coder','reviewer','helper'}: raise MemoryError('Invalid worker role')
                claims = {k:bounded(data.get(k,''),k,500, k in {'role','run_id'}) for k in ['role','run_id','repository','issue']}
                if claims['repository'] and claims['issue']:
                    claims['issue'] = claims['repository'] + '#' + claims['issue']
                claims['actor'] = claims['role']
                claims['expires'] = int(time.time()) + 7*86400
                result = {'token':signed_token(claims,self.config['signing_key']), 'expires':claims['expires']}
                return await JSONResponse(result)(scope,receive,send)
            except (ValueError,TypeError) as exc:
                return await JSONResponse({'error':str(exc)},status_code=400)(scope,receive,send)
        principal = None
        for name, value in self.config['clients'].items():
            if hmac.compare_digest(token,value['token']):
                principal = Principal(role=value['role'],actor=name)
                break
        principal = principal or verify_token(token, self.config['signing_key'])
        if principal is None:
            return await JSONResponse({'error':'unauthorized'},status_code=401)(scope,receive,send)
        reset = CURRENT.set(principal)
        try:
            await self.app(scope,receive,send)
        finally:
            CURRENT.reset(reset)


def provision():
    path = ROOT/'.local/service-auth.json'
    if not path.exists():
        private_write(path,json.dumps({'issuer_token':secrets.token_urlsafe(40),'signing_key':secrets.token_urlsafe(48),
            'clients':{'codex':{'role':'frontier','token':secrets.token_urlsafe(40)},
                       'opencode':{'role':'coder','token':secrets.token_urlsafe(40)}}},indent=2))
    return json.loads(path.read_text())


def main():
    config = provision()
    server = build_server(MemoryStore())
    app = server.streamable_http_app(stateless_http=True,json_response=True)
    uvicorn.run(AuthenticatedApp(app,config),host='127.0.0.1',port=9602,access_log=False)


if __name__ == '__main__':
    main()
