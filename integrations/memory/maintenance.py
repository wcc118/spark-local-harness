"""Maintenance for the memory store: backfill, usage statistics, review reports, backup retention.

Everything here is additive or read-only except `backfill` (adds titles and vectors) and
`prune --apply` (removes only old local backup files). Nothing deletes or rewrites memories.
"""
from __future__ import annotations

import collections
import datetime
import difflib
from pathlib import Path
import re
import subprocess

import embedding
from memory_service import Database, clean_label, make_title, project_key
from memoryctl import ROOT, private_write

STALE_DAYS, UNRETRIEVED_DAYS, DISPUTED_DAYS = 90, 60, 14
VERSIONED = re.compile(r'\b\d+\.\d+(\.\d+)?\b')


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None


def backfill(db=None, refresh=False):
    """Give every memory a title and an embedding in the current space. Never touches claim text."""
    db = db or Database()
    rows = db.query('SELECT id, text, conditions, topics, title FROM memory;')[0]
    have = {str(r['memory']) for r in db.query('SELECT memory, model FROM memory_embedding;')[0]
            if r['model'].startswith(embedding.TAG + '-')}
    titled = embedded = failed = 0
    for row in rows:
        ident = str(row['id'])
        if not row.get('title'):
            db.query('UPDATE type::record($id) SET title=$title;', {'id': ident, 'title': make_title(row['text'])})
            titled += 1
        if ident in have and not refresh:
            continue
        vector = embedding.embed(embedding.document_text(row['text'], row.get('conditions') or '', row.get('topics') or []))
        if not vector:
            failed += 1
            continue
        db.query('UPSERT type::record($eid) SET memory=type::record($id), model=$model, vector=$vector, created_at=time::now();',
                 {'eid': 'memory_embedding:' + ident.split(':', 1)[1], 'id': ident, 'model': embedding.space(vector), 'vector': vector})
        embedded += 1
    return {'memories': len(rows), 'titled': titled, 'embedded': embedded, 'embedding_failed': failed}


def load(db):
    memories = db.query('SELECT id, title, text, conditions, project, status, kind, topics, created_at FROM memory;')[0]
    events = db.query("SELECT target, outcome, created_at FROM memory_event WHERE action = 'use';")[0]
    retrievals = db.query('SELECT created_at, mode, count, results FROM retrieval;')[0]
    return memories, events, retrievals


def stats(db=None):
    db = db or Database()
    memories, events, retrievals = load(db)
    vectors = db.query('SELECT model, count() AS n FROM memory_embedding GROUP BY model;')[0]
    live = [m for m in memories if m['status'] in {'active', 'disputed'}]
    retrieved = {str(i) for r in retrievals for i in r.get('results', [])}
    week = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    recent = [r for r in retrievals if (parse_time(r['created_at']) or week) >= week]
    lines = [f"memories: {len(memories)} ({len(live)} active/disputed)",
             'by status: ' + ', '.join(f'{k}={v}' for k, v in sorted(collections.Counter(m['status'] for m in memories).items())),
             'by kind: ' + ', '.join(f'{k}={v}' for k, v in sorted(collections.Counter(m['kind'] for m in memories).items())),
             'by project: ' + ', '.join(f'{k or "(none)"}={v}' for k, v in
                                        collections.Counter(project_key(m['project']) if m.get('project') else None
                                                            for m in memories).most_common()),
             f"without topics: {sum(1 for m in memories if not m.get('topics'))}",
             'embedded: ' + (', '.join(f"{v['model']}={v['n']}" for v in vectors) or 'none') + f' of {len(memories)} memories',
             'use reports: ' + (', '.join(f'{k}={v}' for k, v in
                                          sorted(collections.Counter(e['outcome'] for e in events).items())) or 'none'),
             f"searches logged: {len(retrievals)} total, {len(recent)} in the last 7 days",
             f"searches with no result: {sum(1 for r in retrievals if not r.get('count'))}",
             'search modes: ' + (', '.join(f'{k}={v}' for k, v in
                                           collections.Counter(r.get('mode') for r in retrievals).items()) or 'none'),
             f"live memories ever returned by a search: {len(retrieved & {str(m['id']) for m in live})}/{len(live)}"]
    titles = {str(m['id']): m.get('title') or make_title(m['text']) for m in memories}
    returned = collections.Counter(str(i) for r in retrievals for i in r.get('results', []))
    if returned:
        lines.append('most returned: ' + '; '.join(f'{titles.get(i, i)[:50]} x{n}' for i, n in returned.most_common(3)))
    return '\n'.join(lines)


def usage(db=None, days=7, recent=12, as_dict=False):
    """How agents actually use memory: the funnel (searched, returned, opened, applied), who, and which lessons.

    Opens and use reports are agent behaviour; a returned lesson that was never opened or reported is
    not evidence it was ignored or ignorable, only that nothing was recorded. Use reports are the
    agents' own account, not verified measurements.
    """
    db = db or Database()
    window = {'d': f'{days}d'}
    logs = db.query('SELECT kind, created_at, actor, role, repository, issue, project, count, results FROM retrieval '
                    'WHERE created_at > time::now() - <duration> $d ORDER BY created_at DESC;', window)[0]
    uses = db.query("SELECT target, outcome, explanation, actor, role, repository, issue, created_at FROM memory_event "
                    "WHERE action = 'use' AND created_at > time::now() - <duration> $d ORDER BY created_at DESC;", window)[0]
    memories = {str(m['id']): m for m in db.query('SELECT id, title, text, project, status FROM memory;')[0]}
    title = lambda i: (memories.get(str(i), {}).get('title') or make_title(memories.get(str(i), {}).get('text', '')) or str(i))
    # Worker credentials already carry the issue as OWNER/REPO#N; other identities carry none.
    where = lambda r: (str(r['issue']) if '#' in str(r.get('issue') or '') else (r.get('project') or '-'))

    searches = [r for r in logs if (r.get('kind') or 'search') == 'search']  # rows before `kind` existed are searches
    opens = [r for r in logs if r.get('kind') == 'open']
    returned = collections.Counter(str(i) for r in searches for i in r.get('results', []))
    opened = collections.Counter(str(i) for r in opens for i in r.get('results', []))
    applied = collections.Counter(str(u['target']) for u in uses)
    live = [i for i, m in memories.items() if m['status'] in {'active', 'disputed'}]

    who = collections.defaultdict(lambda: collections.Counter())
    for r in searches: who[r['actor']]['searches'] += 1
    for r in opens: who[r['actor']]['opens'] += 1
    for u in uses: who[u['actor']]['use reports'] += 1

    funnel = {'days': days, 'searches': len(searches), 'zero_result_searches': sum(1 for r in searches if not r.get('count')),
              'lessons_returned': len(returned), 'opens': len(opens), 'lessons_opened': len(opened),
              'use_reports': len(uses), 'lessons_applied': len(applied),
              'outcomes': dict(collections.Counter(u['outcome'] for u in uses)),
              'returned_not_opened': len(set(returned) - set(opened) - set(applied)),
              'opened_not_reported': len(set(opened) - set(applied)), 'live_lessons': len(live),
              'live_never_returned': len(set(live) - set(returned))}

    events = []
    for r in searches:
        shown = [title(i) for i in r.get('results', [])[:2]]
        events.append({'at': r['created_at'], 'type': 'search', 'who': r['actor'], 'where': where(r), 'detail': f"{r.get('count', 0)} returned" + (': ' + '; '.join(shown) if shown else '')})
    for r in opens:
        events.append({'at': r['created_at'], 'type': 'open', 'who': r['actor'], 'where': where(r), 'detail': title(r['results'][0]) if r.get('results') else ''})
    for u in uses:
        events.append({'at': u['created_at'], 'type': 'use', 'who': u['actor'], 'where': where(u), 'detail': f"{u['outcome']}: {title(u['target'])} | {(u.get('explanation') or '')[:160]}"})
    events.sort(key=lambda e: e['at'], reverse=True)

    result = {'funnel': funnel, 'who': {k: dict(v) for k, v in who.items()}, 'events': events[:recent],
              'applied': [{'at': u['created_at'], 'who': u['actor'], 'where': where(u), 'outcome': u['outcome'], 'lesson': title(u['target']), 'why': (u.get('explanation') or '')[:200]} for u in uses[:10]],
              'returned_unused': [{'lesson': title(i), 'returned': n} for i, n in returned.most_common() if i not in opened and i not in applied][:5]}
    if as_dict:
        return result
    f = funnel
    lines = [f"MEMORY USE, last {days} days",
             f"  searched {f['searches']}x ({f['zero_result_searches']} with no result) -> {f['lessons_returned']} lessons returned -> "
             f"{f['lessons_opened']} opened in full -> {f['lessons_applied']} applied (use reports: {f['use_reports']}, outcomes {f['outcomes'] or 'none'})",
             f"  returned but not opened or reported: {f['returned_not_opened']}; opened but not reported: {f['opened_not_reported']}; "
             f"live lessons never returned: {f['live_never_returned']} of {f['live_lessons']}",
             'WHO', *[f"  {a}: " + ', '.join(f'{k} {v}' for k, v in c.items()) for a, c in sorted(who.items())],
             'APPLIED BY AGENTS (their own reports, not verified)']
    lines += [f"  {x['at'][:16]} {x['who']} {x['where']} [{x['outcome']}] {x['lesson'][:70]}\n      {x['why'][:150]}" for x in result['applied']] or ['  none yet']
    lines += ['RETURNED A LOT, NEVER OPENED OR REPORTED (maybe weak titles, maybe not relevant)']
    lines += [f"  x{x['returned']} {x['lesson'][:80]}" for x in result['returned_unused']] or ['  none']
    lines += ['RECENT'] + [f"  {e['at'][5:16]} {e['type']:6} {e['who']:11} {e['where'][:28]:28} {e['detail'][:90]}" for e in result['events']]
    return '\n'.join(lines)


def retitle(changes, source, actor='claude', db=None, revert=False):
    """Change display titles with an audit event each. `changes` maps memory id to {'old': ..., 'new': ...}.

    Titles are display labels; claim text is never touched. A change applies only if the stored title still
    equals `old` (or, when reverting, `new`), so a title someone else changed meanwhile is left alone.
    Each applied change writes a memory_event (action 'retitle') recording both titles and the source.
    """
    db = db or Database()
    applied, skipped = 0, []
    for ident, change in changes.items():
        before, after = (change['new'], change['old']) if revert else (change['old'], change['new'])
        after = clean_label(after) if not revert else after  # same one-line, colon-safe form every other path stores
        current = db.query('SELECT VALUE title FROM type::record($id);', {'id': ident})[0]
        if not current or current[0] != before:
            skipped.append(ident)
            continue
        db.query("""BEGIN TRANSACTION;
            UPDATE type::record($id) SET title=$after;
            CREATE memory_event SET action='retitle', target=type::record($id), old_title=$before, new_title=$after,
                source=$source, actor=$actor, role='frontier', reverted=$revert, created_at=time::now();
            COMMIT TRANSACTION;""", {'id': ident, 'before': before, 'after': after, 'source': source, 'actor': actor, 'revert': revert})
        applied += 1
    return {'applied': applied, 'skipped_title_changed': skipped}


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # vectors are stored unit length


def similar_names(a, b):
    squash = lambda name: re.sub(r'[^a-z0-9]', '', name.lower()).rstrip('s')
    return squash(a) == squash(b) or difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85


def review(db=None, duplicate_min=0.9, max_vectors=1500):
    """Read-only maintenance proposals. Act on them with resolve_memory, enrich_memory or correct_memory."""
    db = db or Database()
    memories, events, retrievals = load(db)
    now = datetime.datetime.now(datetime.timezone.utc)
    label = lambda m: f"{m['id']}  {m.get('title') or make_title(m['text'])}"
    live = [m for m in memories if m['status'] in {'active', 'disputed'}]
    retrieved = collections.defaultdict(lambda: None)
    for r in retrievals:
        when = parse_time(r['created_at'])
        for ident in r.get('results', []):
            if when and (retrieved[str(ident)] is None or when > retrieved[str(ident)]):
                retrieved[str(ident)] = when
    out = []

    stale = [m for m in live if (now - (parse_time(m['created_at']) or now)).days >= STALE_DAYS
             and VERSIONED.search((m['text'] or '') + ' ' + (m.get('conditions') or ''))
             and not ((last := retrieved.get(str(m['id']))) and (now - last).days < UNRETRIEVED_DAYS)]
    out.append(f'== Possibly stale ({len(stale)}): older than {STALE_DAYS}d, mention a version, not retrieved in {UNRETRIEVED_DAYS}d')
    out += ['  ' + label(m) for m in stale]

    disputed = [m for m in memories if m['status'] == 'disputed'
                and (now - (parse_time(m['created_at']) or now)).days >= DISPUTED_DAYS]
    out.append(f'== Disputed over {DISPUTED_DAYS}d, awaiting resolve_memory ({len(disputed)})')
    out += ['  ' + label(m) for m in disputed]

    rows = db.query('SELECT memory, model, vector FROM memory_embedding;')[0]
    space = collections.Counter(r['model'] for r in rows).most_common(1)[0][0] if rows else None
    vectors = {str(r['memory']): r['vector'] for r in rows if r['model'] == space}  # one space at a time
    by_id = {str(m['id']): m for m in live if str(m['id']) in vectors}
    pairs = []
    if len(by_id) <= max_vectors:
        ids = sorted(by_id)
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                if (score := cosine(vectors[a], vectors[b])) >= duplicate_min:
                    pairs.append((score, a, b))
    out.append(f'== Near-duplicate pairs, cosine >= {duplicate_min} ({len(pairs)})' +
               ('' if len(by_id) <= max_vectors else f' [skipped: more than {max_vectors} memories]'))
    for score, a, b in sorted(pairs, reverse=True):
        out += [f'  {score:.3f}', '    ' + label(by_id[a]), '    ' + label(by_id[b])]

    names = sorted({t for m in memories for t in m.get('topics') or []})
    merges = [(a, b) for n, a in enumerate(names) for b in names[n + 1:] if similar_names(a, b)]
    out.append(f'== Similar topic names ({len(merges)}); enrich_memory can add the preferred one')
    out += [f'  {a}  ~  {b}' for a, b in merges]

    spellings = collections.defaultdict(set)
    for m in memories:
        if m.get('project'):
            spellings[project_key(m['project'])].add(m['project'])
    aliases = {k: v for k, v in spellings.items() if len(v) > 1}
    out.append(f'== Project names spelled more than one way ({len(aliases)}); search treats them as one')
    out += [f"  {k}: {', '.join(sorted(v))}" for k, v in aliases.items()]

    bare = [m for m in live if not m.get('topics')]
    out.append(f'== Active memories with no topics ({len(bare)}); enrich_memory would make them discoverable')
    out += ['  ' + label(m) for m in bare[:25]] + (['  ...'] if len(bare) > 25 else [])
    return '\n'.join(out)


def link_similar(db=None, minimum=0.85, space=None):
    """Add or refresh similar_to edges between live memories at or above `minimum` cosine. Additive: never removes edges."""
    import hashlib
    db = db or Database()
    live = {str(m['id']) for m in db.query("SELECT id FROM memory WHERE status IN ['active', 'disputed'];")[0]}
    rows = db.query('SELECT memory, model, vector FROM memory_embedding;')[0]
    space = space or (collections.Counter(r['model'] for r in rows).most_common(1)[0][0] if rows else None)
    vectors = {str(r['memory']): r['vector'] for r in rows if r['model'] == space and str(r['memory']) in live}
    ids, linked = sorted(vectors), 0
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if (score := cosine(vectors[a], vectors[b])) >= minimum:
                edge = 'similar_to:' + hashlib.sha256(f'{a}|{b}'.encode()).hexdigest()[:24]
                db.query('INSERT RELATION INTO similar_to {id: type::record($e), in: type::record($a), out: type::record($b), '
                         'similarity: $s} ON DUPLICATE KEY UPDATE similarity = $s;',
                         {'e': edge, 'a': a, 'b': b, 's': round(score, 4)})
                linked += 1
    return {'memories': len(ids), 'linked': linked, 'minimum': minimum}


def backup_sets(directory=None):
    """Local backups newest first, as (stem, verified)."""
    directory = directory or ROOT / 'backups'
    stems = sorted({p.name.split('.')[0] + '.' + p.name.split('.')[1] for p in directory.glob('*.surql')}, reverse=True)
    return [(stem, (directory / (stem + '.verify.json')).exists()) for stem in stems]


def prune(apply=False, keep_days=14, keep_weeks=12, directory=None):
    """Keep every backup newer than keep_days, then one per ISO week for keep_weeks, always the newest verified one.

    Prints the plan; only deletes with apply=True. With no verified backup at all, nothing is pruned.
    """
    directory = directory or ROOT / 'backups'
    now = datetime.datetime.now(datetime.timezone.utc)
    keep, weeks = set(), set()
    sets = backup_sets(directory)
    newest_verified = next((s for s, ok in sets if ok), None)
    for stem, verified in sets:
        stamp = datetime.datetime.strptime(stem[:15], '%Y%m%dT%H%M%S').replace(tzinfo=datetime.timezone.utc)
        week = stamp.isocalendar()[:2]
        if (now - stamp).days < keep_days or stem == newest_verified:
            keep.add(stem)
        elif verified and week not in weeks and (now - stamp).days < keep_days + 7 * keep_weeks:
            keep.add(stem)
            weeks.add(week)
        elif newest_verified is None:
            keep.add(stem)  # nothing has been proven restorable, so nothing may be pruned
    doomed = [stem for stem, _ in sets if stem not in keep]
    for stem in doomed:
        if apply:
            for path in directory.glob(stem + '.*'):
                path.unlink()
    return {'kept': len(keep), 'removed' if apply else 'would_remove': doomed}


def offsite(config_path=None, directory=None):
    """Encrypt the newest verified backup with gpg and copy it to the configured destination.

    .local/offsite.json: {"destination": "user@host:/path/ or /mount/path/", "passphrase_file": ".local/offsite.pass"}
    The backup holds database user definitions, so it is never copied unencrypted.
    """
    directory = directory or ROOT / 'backups'
    import json
    path = Path(config_path) if config_path else ROOT / '.local/offsite.json'
    if not path.exists():
        raise RuntimeError('Off-machine backup is not configured: create .local/offsite.json (see maintenance.offsite)')
    config = json.loads(path.read_text())
    stem = next((s for s, ok in backup_sets(directory) if ok), None)
    if stem is None:
        raise RuntimeError('No verified backup to send; run backup --verify first')
    source = directory / (stem + '.surql')
    encrypted = source.with_suffix('.surql.gpg')
    passphrase = (ROOT / config['passphrase_file']).resolve()
    subprocess.run(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase-file', str(passphrase),
                    '--symmetric', '--cipher-algo', 'AES256', '-o', str(encrypted), str(source)], check=True)
    encrypted.chmod(0o600)
    try:
        subprocess.run(['rsync', '-t', '--chmod=F600', str(encrypted), config['destination']], check=True)
    finally:
        encrypted.unlink(missing_ok=True)
    return {'sent': stem + '.surql.gpg', 'destination': config['destination']}
