"""Role-authenticated memory MCP. No arbitrary SQL, overwrite or deletion tools."""
from __future__ import annotations

import base64
import contextvars
import difflib
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

import embedding
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


SEMANTIC_MIN = 0.58       # cosine floor for any semantic hit; nomic scores unrelated English text 0.55-0.63
SEMANTIC_ONLY_MIN = 0.65  # a hit the keyword search missed must clear this higher bar
SEMANTIC_BAND = 0.10      # and every hit must be within this of the best hit
STOPWORDS = frozenset('a an and are as at be by do does for from how i in is it of on or that the this to was what when which why with'.split())
DUPLICATE_MIN = 0.88      # cosine above which a new memory is reported as a near-duplicate
RRF_K = 60                # reciprocal-rank-fusion constant for blending keyword and semantic ranks


def make_title(text, limit=90):
    """A short readable label: the first sentence or line, cut at a word boundary."""
    text = re.sub(r'^[\s*#_>-]*(?:title|summary)\s*:\s*[*_]*\s*', '', text.strip(), flags=re.I)  # markdown label
    first = re.split(r'(?<=[.!?])\s|\n', text.strip(), maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(' ', 1)[0].rstrip(',;:-') + '…'


BRIEF_FIELDS = ('id', 'title', 'kind', 'status', 'project', 'matched_by', 'similarity', 'use_reports')


def brief(row):
    """A compact result for small context windows; get_memory returns the full record."""
    out = {k: row[k] for k in BRIEF_FIELDS if k in row}
    out['snippet'] = row['text'][:160] + ('…' if len(row['text']) > 160 else '')
    if row.get('conditions'):
        out['conditions'] = row['conditions'][:200]
    return out


def project_key(project):
    """Compare 'owner/repo' and 'repo' spellings of one project as the same project."""
    return project.rsplit('/', 1)[-1]


def bounded(value, name, maximum=16000, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise MemoryError(f'{name} must be a nonempty string of at most {maximum} characters' if required else f'Invalid {name}')
    return value.strip()


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
    def __init__(self, database=None, embedder=embedding.embed):
        self.db = database or Database()
        self.embedder = embedder
        self.lock = threading.RLock()

    def get_record(self, ident):
        ident = record_id(ident)
        rows = self.db.query('SELECT * OMIT search_text FROM type::record($id);', {'id': ident})[0]
        if not rows:
            raise MemoryError('Memory not found')
        return rows[0]

    def search(self, query, project='', topics=None, include_history=False, limit=8, exclude_issue='', principal=None, detail='full'):
        if detail not in {'full', 'brief'}:
            raise MemoryError("detail must be 'full' or 'brief'")
        query = bounded(query, 'query', 1000)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            raise MemoryError('limit must be between 1 and 30')
        project = bounded(project, 'project', 200, False)
        topics = self.topics(topics or [])
        exclude = bounded(exclude_issue, 'exclude_issue', 200, False)
        pool = max(limit * 3, 20)
        # Stemmed full text over text, conditions and topic names, plus a literal substring
        # for exact identifiers. Punctuation is removed from the full-text terms only: a
        # bare '-' or '.' token would otherwise match nearly every record.
        terms = re.sub(r'[^\w]+', ' ', query).strip()
        terms = ' '.join(w for w in terms.split() if w.lower() not in STOPWORDS) or terms
        matched, score = ('search_text @1@ $terms OR ', 'search::score(1)') if terms else ('', '0')
        filters = {'project': project_key(project), 'topics': topics, 'history': include_history, 'exclude': exclude}
        keyword = self.db.query(f'''SELECT *, {score} AS relevance OMIT search_text FROM memory
            WHERE ($history OR status IN ['active', 'disputed'])
            AND ({matched}string::contains(string::lowercase(search_text), string::lowercase($q)))
            AND ($project = '' OR array::last(string::split(project ?? '', '/')) = $project)
            AND (array::len($topics) = 0 OR array::any(topics, |$t| $t IN $topics))
            AND ($exclude = '' OR issue = NONE OR issue != $exclude)
            ORDER BY relevance DESC LIMIT $pool;''',
            dict(filters, q=query, terms=terms, pool=pool))[0]
        semantic = self.semantic_hits(query, filters, pool)
        rows, mode = self.fuse(keyword, semantic, limit)
        self.attach_use_reports(rows)
        self.log_retrieval(principal, project, mode, rows)
        return {'memories': [brief(r) for r in rows] if detail == 'brief' else rows, 'search_mode': mode,
                'hint': 'Disputed records are unresolved evidence. Inspect links before applying. '
                'Project filters are optional. If you actually apply a lesson, record_memory_use with the outcome '
                'helps later readers judge it; reading or finding a lesson alone is not a use. '
                'use_reports counts are agent reports, shown for context and not used to rank.'}

    def semantic_hits(self, query, filters, pool):
        """Nearest memories by meaning as {id: similarity}; empty when no embedder or vectors exist."""
        vector = self.embedder(query, query=True)
        if not vector:
            return {}
        rows = self.db.query('''SELECT memory AS id, vector::similarity::cosine(vector, $v) AS similarity
            FROM memory_embedding
            WHERE model = $model AND ($history OR memory.status IN ['active', 'disputed'])
            AND ($project = '' OR array::last(string::split(memory.project ?? '', '/')) = $project)
            AND (array::len($topics) = 0 OR array::any(memory.topics, |$t| $t IN $topics))
            AND ($exclude = '' OR memory.issue = NONE OR memory.issue != $exclude)
            ORDER BY similarity DESC LIMIT $pool;''',
            dict(filters, v=vector, model=embedding.space(vector), pool=pool))[0]
        floor = max(SEMANTIC_MIN, max((row['similarity'] for row in rows), default=0) - SEMANTIC_BAND)
        return {str(row['id']): row['similarity'] for row in rows if row['similarity'] >= floor}

    def fuse(self, keyword, semantic, limit):
        """Reciprocal-rank fusion of the keyword and semantic rankings."""
        scores, found = {}, {}
        for rank, row in enumerate(keyword):
            ident = str(row['id'])
            found[ident] = dict(row, matched_by='keyword')
            scores[ident] = 1 / (RRF_K + rank)
        missing = [ident for ident in semantic if ident not in found]
        fetched = {}
        if missing:
            params = {f'i{n}': ident for n, ident in enumerate(missing)}
            sql = ' '.join(f'SELECT * OMIT search_text FROM type::record($i{n});' for n in range(len(missing)))
            for result in self.db.query(sql, params):
                for row in result:
                    fetched[str(row['id'])] = row
        for rank, (ident, similarity) in enumerate(sorted(semantic.items(), key=lambda item: -item[1])):
            if ident in found:
                found[ident]['matched_by'] = 'both'
            elif ident in fetched and similarity >= SEMANTIC_ONLY_MIN:
                found[ident] = dict(fetched[ident], matched_by='semantic')
            else:
                continue
            found[ident]['similarity'] = round(similarity, 3)
            scores[ident] = scores.get(ident, 0) + 1 / (RRF_K + rank)
        ordered = sorted(found, key=lambda ident: -scores[ident])[:limit]
        return [found[ident] for ident in ordered], ('hybrid' if semantic else 'keyword')

    def attach_use_reports(self, rows):
        """Show agent use-report counts per memory without letting them change the ranking."""
        if not rows:
            return
        params = {f'i{n}': str(row['id']) for n, row in enumerate(rows)}
        targets = ', '.join(f'type::record($i{n})' for n in range(len(rows)))
        counts = self.db.query(f"""SELECT target, outcome, count() AS n FROM memory_event
            WHERE action = 'use' AND target IN [{targets}] GROUP BY target, outcome;""", params)[0]
        by_target = {}
        for item in counts:
            by_target.setdefault(str(item['target']), {})[item['outcome']] = item['n']
        for row in rows:
            if str(row['id']) in by_target:
                row['use_reports'] = by_target[str(row['id'])]

    def log_retrieval(self, principal, project, mode, rows):
        """Best-effort usage log (no query text); a logging failure never fails a search."""
        if principal is None:
            return
        try:
            params = {f'i{n}': str(row['id']) for n, row in enumerate(rows)}
            returned = ', '.join(f'type::record($i{n})' for n in range(len(rows)))
            params.update(actor=principal.actor, role=principal.role, repository=principal.repository,
                          issue=principal.issue, project=project, mode=mode, count=len(rows))
            self.db.query(f"""CREATE retrieval SET actor=$actor, role=$role, repository=$repository, issue=$issue,
                project=$project, mode=$mode, count=$count, results=[{returned}], created_at=time::now();""", params)
        except MemoryError:
            pass

    def nearest(self, vector, limit=3, exclude=()):
        """Existing active or disputed memories very close in meaning to `vector`."""
        rows = self.db.query('''SELECT memory AS id, memory.title AS title, memory.status AS status,
            vector::similarity::cosine(vector, $v) AS similarity FROM memory_embedding
            WHERE model = $model AND memory.status IN ['active', 'disputed']
            ORDER BY similarity DESC LIMIT $limit;''',
            dict(v=vector, model=embedding.space(vector), limit=limit + len(exclude)))[0]
        return [dict(id=str(r['id']), title=r['title'], status=r['status'], similarity=round(r['similarity'], 3))
                for r in rows if r['similarity'] >= DUPLICATE_MIN and str(r['id']) not in exclude][:limit]

    def topic_suggestions(self, topics):
        """Existing topic names resembling each not-yet-existing topic; suggestions only, never merged."""
        if not topics:
            return {}
        existing = self.db.query('SELECT VALUE name FROM topic;')[0]
        names = {name.lower(): name for name in existing if isinstance(name, str)}
        suggestions = {}
        for topic in topics:
            if topic.lower() in names:
                continue
            words = {w for w in re.split(r'[^a-z0-9]+', topic.lower()) if len(w) >= 3}
            scored = []
            for low, name in names.items():
                other = {w for w in re.split(r'[^a-z0-9]+', low) if len(w) >= 3}
                shared = any(a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b[:4]) and b.startswith(a[:4])))
                             for a in words for b in other)
                ratio = difflib.SequenceMatcher(None, topic.lower(), low).ratio()
                if shared or ratio >= 0.75:
                    scored.append((ratio + shared, name))
            if scored:
                suggestions[topic] = [name for _, name in sorted(scored, key=lambda s: (-s[0], s[1]))[:5]]
        return suggestions

    @staticmethod
    def with_suggestions(result, suggestions):
        if suggestions:
            result['topic_suggestions'] = suggestions
            result['topic_hint'] = ('New topic names were created. Similar existing names are listed; if one also '
                                    'applies, enrich_memory can add it. Similar names need not mean the same thing.')
        return result

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

    def enrich(self, principal, memory_id, topics, explanation, source, request_id):
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
            suggestions = self.topic_suggestions(topics)
            linked = {edge['out'] for edge in self.db.query(
                'SELECT out FROM concerns WHERE in = type::record($id);', {'id': memory_id})[0]}
            event = dict(action='enrich', topics=topics,
                         added_topics=[topic for topic in topics if topic not in record.get('topics', [])],
                         explanation=explanation, source=source, fingerprint=fingerprint,
                         actor=principal.actor, role=principal.role, run_id=principal.run_id,
                         repository=principal.repository, issue=principal.issue)
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
            self.refresh_embedding(memory_id)
            return self.with_suggestions({'memory': self.get_record(memory_id), 'event': stored, 'replayed': False}, suggestions)

    def refresh_embedding(self, memory_id):
        """Re-embed after topics change, since the vector covers topic names. Best effort; never fails a write."""
        try:
            record = self.get_record(memory_id)
            vector = self.embedder(embedding.document_text(record['text'], record.get('conditions') or '', record.get('topics') or []))
            if vector:
                self.db.query('UPSERT type::record($eid) SET memory=type::record($id), model=$model, vector=$vector, created_at=time::now();',
                              {'eid': 'memory_embedding:' + memory_id.split(':', 1)[1], 'id': memory_id,
                               'model': embedding.space(vector), 'vector': vector})
        except MemoryError:
            pass

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
            related.extend(self.db.query('SELECT * OMIT search_text FROM type::record($id);', {'id': ident})[0])
        return {'memory': record, 'relations': edges[0], 'related': related, 'events': edges[1]}

    def write(self, principal, text, source, request_id, topics=None, project='', kind='finding', conditions='', target=None, relation=None, title=''):
        if principal.role not in {'coder','reviewer','frontier'}:
            raise MemoryError('This identity has read-only memory access')
        text = bounded(text, 'text')
        source = bounded(source, 'source', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        project = bounded(project, 'project', 200, False)
        conditions = bounded(conditions, 'conditions', 4000, False)
        title = bounded(title, 'title', 120, False)
        topics = self.topics(topics or [])
        if kind not in {'finding','decision','preference','hypothesis','correction','review'}:
            raise MemoryError('Invalid memory kind')
        payload = dict(text=text, source=source, topics=topics, project=project, kind=kind, conditions=conditions,
                       target=target, relation=relation)
        if title:  # absent unless supplied, so retries of pre-title requests keep their fingerprint
            payload['title'] = title
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([principal.__dict__,request_id],sort_keys=True).encode()).hexdigest()[:32]
        ident = 'memory:' + key
        with self.lock:
            existing = self.db.query('SELECT * OMIT search_text FROM type::record($id);', {'id': ident})[0]
            if existing:
                if existing[0].get('fingerprint') != fingerprint:
                    raise MemoryError('request_id already used for different content; use a new request_id')
                return {'memory': existing[0], 'replayed': True}
            if target:
                self.get_record(target)
            suggestions = self.topic_suggestions(topics)
            vector = self.embedder(embedding.document_text(text, conditions, topics))
            similar = self.nearest(vector, exclude={target} if target else ()) if vector else []
            data = dict(text=text, source=source, topics=topics, kind=kind, status='active', conditions=conditions,
                        title=title or make_title(text),
                        actor=principal.actor, role=principal.role, run_id=principal.run_id,
                        repository=principal.repository, issue=principal.issue, fingerprint=fingerprint)
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
            if vector:
                params.update(embedding_id='memory_embedding:' + key, vector=vector, model=embedding.space(vector))
                statements.append('CREATE type::record($embedding_id) SET memory=type::record($id), model=$model, '
                                  'vector=$vector, created_at=time::now();')
            params['event'] = dict(action=relation or 'record', actor=principal.actor, role=principal.role,
                                   run_id=principal.run_id, issue=principal.issue, source=source)
            statements += ['CREATE memory_event SET action=$event.action, actor=$event.actor, role=$event.role, run_id=$event.run_id, issue=$event.issue, source=$event.source, target=type::record($id), created_at=time::now();', 'COMMIT TRANSACTION;']
            self.db.query('\n'.join(statements), params)
            result = self.with_suggestions({'memory': self.get_record(ident), 'replayed': False}, suggestions)
            if similar:
                result['similar_memories'] = similar
                result['similar_hint'] = ('These existing memories are very close in meaning. If one already says this, '
                                          'prefer record_memory_use to corroborate it or enrich_memory to add context; '
                                          'a new record is still valid when yours adds a different finding or conditions.')
            return result

    def record_use(self, principal, memory_id, outcome, explanation, source, request_id):
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
                         repository=principal.repository, issue=principal.issue)
            self.db.query('''BEGIN TRANSACTION;
                CREATE type::record($event_id) CONTENT $event;
                UPDATE type::record($event_id) SET target=type::record($target), created_at=time::now();
                COMMIT TRANSACTION;''',
                dict(event_id=event_id, event=event, target=memory_id))
            return {'event': self.db.query('SELECT * FROM type::record($id);', {'id': event_id})[0][0],
                    'replayed': False}

    def resolve(self, principal, target_id, replacement_id, rationale, request_id):
        if principal.role != 'frontier':
            raise MemoryError('Only the frontier reconciliation identity may supersede memories')
        record_id(target_id); record_id(replacement_id)
        rationale = bounded(rationale, 'rationale', 4000)
        request_id = bounded(request_id, 'request_id', 200)
        if target_id == replacement_id:
            raise MemoryError('A memory cannot supersede itself')
        key = hashlib.sha256(json.dumps([principal.__dict__,request_id,'resolve'],sort_keys=True).encode()).hexdigest()[:32]
        fingerprint = hashlib.sha256(json.dumps([target_id,replacement_id,rationale]).encode()).hexdigest()
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
                         fingerprint=fingerprint, run_id=principal.run_id, issue=principal.issue)
            self.db.query('''BEGIN TRANSACTION;
                UPDATE type::record($target) SET status='superseded';
                UPDATE type::record($replacement) SET status='active';
                RELATE (type::record($replacement))->supersedes->(type::record($target));
                CREATE type::record($event_id) SET action=$event.action, actor=$event.actor, role=$event.role, rationale=$event.rationale, fingerprint=$event.fingerprint, run_id=$event.run_id, issue=$event.issue, target=type::record($target), replacement=type::record($replacement), created_at=time::now();
                COMMIT TRANSACTION;''', dict(target=target_id,replacement=replacement_id,event_id=event_id,event=event))
            return {'target': self.get_record(target_id), 'replacement': replacement_id, 'replayed': False}


def build_server(store):
    server = MCPServer(name='memory', version='1.0.0', instructions='Shared cross-project lessons. Choose your own searches; inspect evidence and conditions. Record useful findings. No SQL or deletion tool. Memory failures do not block your task.')

    @server.tool()
    def search_memory(query: str, project: str = '', topics: list[str] | None = None, include_history: bool = False, limit: int = 8, exclude_issue: str = '', detail: str = 'full') -> dict:
        """Search across projects by default, by keyword and by meaning. detail='brief' returns id, title, snippet and conditions only, for small context windows; use get_memory for the full lesson. Active and disputed findings are returned; history is opt-in. exclude_issue can preserve current-review independence. A project filter accepts 'repo' or 'owner/repo'."""
        return store.search(query, project, topics, include_history, limit, exclude_issue, CURRENT.get(), detail)

    @server.tool()
    def get_memory(memory_id: str) -> dict:
        """Inspect a memory, provenance, graph neighbors and recent events, including attributed use reports. Reports are not independent verification."""
        return store.inspect(memory_id)

    @server.tool()
    def discover_topics(query: str = '', limit: StrictInt = 30) -> dict:
        """Discover existing topic names with optional case-insensitive substring matching. Reuse names when applicable; this is not a fixed taxonomy or semantic alias resolver."""
        return store.discover_topics(query, limit)

    @server.tool()
    def enrich_memory(memory_id: str, topics: list[str], explanation: str, source: str, request_id: str) -> dict:
        """Add justified topics and graph links to an existing lesson with an audit trail. Preserves original text/source/status; no removal. Reuse request_id only for the identical retry."""
        return store.enrich(CURRENT.get(), memory_id, topics, explanation, source, request_id)

    @server.tool()
    def record_memory(text: str, source: str, request_id: str, topics: list[str] | None = None, project: str = '', kind: str = 'finding', conditions: str = '', title: str = '') -> dict:
        """Record reusable knowledge with evidence and applicability. Caller attribution is authenticated. Optional title is a short readable label (derived from the text if omitted). The result may list similar_memories to corroborate or enrich instead of duplicating. Reuse request_id only to retry the identical write."""
        return store.write(CURRENT.get(), text, source, request_id, topics, project, kind, conditions, title=title)

    @server.tool()
    def correct_memory(target_id: str, text: str, source: str, request_id: str, topics: list[str] | None = None, project: str = '', conditions: str = '') -> dict:
        """Add contrary evidence or a proposed correction. Preserve the original and mark it disputed; no approval is required to contribute."""
        return store.write(CURRENT.get(), text, source, request_id, topics, project, 'correction', conditions, record_id(target_id), 'challenge')

    @server.tool()
    def record_memory_use(memory_id: str, outcome: str, explanation: str, source: str, request_id: str) -> dict:
        """Report actual lesson use as helpful/unhelpful/misleading/uncertain, with evidence and limits. Append-only attributed report, not proof or a vote; does not change the lesson. Retry identical content with the same request_id."""
        return store.record_use(CURRENT.get(), memory_id, outcome, explanation, source, request_id)

    @server.tool()
    def resolve_memory(target_id: str, replacement_id: str, rationale: str, request_id: str) -> dict:
        """Frontier reconciliation only: supersede an outdated claim with a supported active/disputed replacement. Preserves history; never deletes."""
        return store.resolve(CURRENT.get(), target_id, replacement_id, rationale, request_id)

    return server


def valid_auth_config(config):
    return (isinstance(config, dict) and isinstance(config.get('issuer_token'), str) and config['issuer_token']
            and isinstance(config.get('signing_key'), str) and config['signing_key']
            and isinstance(config.get('clients'), dict)
            and all(isinstance(c, dict) and c.get('role') in {'frontier', 'coder', 'reviewer', 'helper'}
                    and isinstance(c.get('token'), str) and len(c['token']) >= 32 for c in config['clients'].values()))


class AuthenticatedApp:
    def __init__(self, app, config, path=None):
        self.app, self.config, self.path = app, config, path
        self.mtime = path.stat().st_mtime_ns if path else None

    def reload(self):
        """Pick up added or rotated clients without a restart; keep the last valid config otherwise."""
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime == self.mtime:
                return
            config = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self.mtime = mtime
        if valid_auth_config(config):
            self.config = config
            print('memory: reloaded client credentials', flush=True)
        else:
            print('memory: ignored invalid client credential file; previous configuration kept', flush=True)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if self.path:
            self.reload()
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
    uvicorn.run(AuthenticatedApp(app,config,ROOT/'.local/service-auth.json'),host='127.0.0.1',port=9602,access_log=False)


if __name__ == '__main__':
    main()
