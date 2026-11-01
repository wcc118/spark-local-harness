"""Draft candidate lessons from a finished PR with a local model. Dry run unless --record.

    extract_candidates.py OWNER/REPO#N [--record] [--model ID]

The model proposes reusable, evidence-backed lessons from the PR description, reviews and comments.
Recording writes them as kind=hypothesis with a source naming the PR and the drafting model, tagged
`auto-extracted`, so they read as unreviewed. An orchestrator confirms or corrects them; nothing here
promotes a claim. Needs `gh` and an OpenAI-compatible endpoint: set MEMORY_LLM_URL (default: LM Studio on localhost:1234).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
LLM = os.environ.get('MEMORY_LLM_URL', 'http://127.0.0.1:1234/v1/chat/completions')
MCP = 'http://127.0.0.1:9602/mcp'
PROMPT = """You extract reusable engineering lessons from a finished pull request for a shared knowledge base.
Return a JSON array of 0 to 4 objects with keys: "title" (<=90 chars), "text" (the lesson as a standalone claim, 1-3 sentences),
"conditions" (versions, environment or scope under which it holds), "topics" (2-4 short kebab-case names).
Only include lessons that would help someone on a DIFFERENT task: root causes, surprising behaviour, review findings that
exposed a failure mode, decisions with their reason. Skip routine changes, restatements of the diff and anything the text
does not support. Return [] if nothing qualifies. Output only JSON.

PULL REQUEST DISCUSSION:
"""


def fetch(ref):
    match = re.fullmatch(r'([\w.-]+/[\w.-]+)#(\d+)', ref)
    if not match:
        sys.exit('Expected OWNER/REPO#N')
    repo, number = match.groups()
    out = subprocess.run(['gh', 'pr', 'view', number, '-R', repo, '--json', 'title,body,comments,reviews,mergedAt,url'],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    parts = [f"TITLE: {data['title']}", f"BODY:\n{data['body']}"]
    parts += [f"REVIEW ({r['state']}): {r['body']}" for r in data['reviews'] if r.get('body')]
    parts += [f"COMMENT: {c['body']}" for c in data['comments'] if c.get('body')]
    return repo, data, '\n\n'.join(parts)[:24000]


def draft(discussion, model):
    body = {'model': model, 'temperature': 0.2, 'max_tokens': 1800,
            'messages': [{'role': 'user', 'content': PROMPT + discussion}]}
    request = urllib.request.Request(LLM, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=600) as response:
        content = json.load(response)['choices'][0]['message']['content']
    found = re.search(r'\[.*\]', content, re.S)
    items = json.loads(found.group(0)) if found else []
    return [i for i in items if isinstance(i, dict) and i.get('text') and i.get('title')][:4]


def record(item, ref, repo, url, model):
    token = json.loads((ROOT / '.local/service-auth.json').read_text())['clients']['claude']['token']
    topics = list(dict.fromkeys([*(item.get('topics') or [])[:4], 'auto-extracted']))
    arguments = dict(
        text=item['text'], title=item['title'][:120], kind='hypothesis', topics=topics, project=repo,
        conditions=item.get('conditions') or '',
        source=f'{url} (PR {ref}); drafted by local model {model}, not yet reviewed by a person or orchestrator',
        request_id='extract-' + hashlib.sha256(f"{ref}|{item['text']}".encode()).hexdigest()[:24])
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'record_memory', 'arguments': arguments}}
    request = urllib.request.Request(MCP, data=json.dumps(body).encode(), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)['result']
    payload = result.get('structuredContent') or json.loads(result['content'][0]['text'])
    return payload['memory']['id'] if 'memory' in payload else payload


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ref')
    parser.add_argument('--record', action='store_true', help='write the candidates (default: print only)')
    parser.add_argument('--model', default='qwen/qwen3.8-27b')
    args = parser.parse_args()
    repo, data, discussion = fetch(args.ref)
    items = draft(discussion, args.model)
    print(f"{args.ref}: {data['title']}  ({len(items)} candidate{'s' * (len(items) != 1)})")
    for item in items:
        print(f"\n- {item['title']}\n  {item['text']}\n  conditions: {item.get('conditions') or '-'}\n  topics: {item.get('topics')}")
        if args.record:
            print('  recorded as', record(item, args.ref, repo, data['url'], args.model))
    if items and not args.record:
        print('\n(dry run; add --record to store these as unreviewed hypotheses)')


if __name__ == '__main__':
    main()
