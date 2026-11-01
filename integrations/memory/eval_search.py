"""Measure search quality: does a lesson come back when someone describes it in other words?

    eval_search.py generate [--hard] [--n 40] [--seed 7]   # an LLM writes one query per sampled lesson
    eval_search.py run [--hard]                            # recall@5 / top-1 / MRR, keyword vs hybrid

--hard tells the query writer to avoid the lesson's own wording, the case semantic search exists for.
Cases are saved under .local/eval/ (ignored by git). Reads live memory with the service's own search;
it writes nothing to the database. Re-run after changing thresholds, the embedding model or the corpus.
"""
import argparse
import json
import os
from pathlib import Path
import random
import urllib.request

import embedding
import memory_service as service

ROOT = Path(__file__).resolve().parent
LLM = os.environ.get('MEMORY_LLM_URL', 'http://127.0.0.1:1234/v1/chat/completions')  # OpenAI-compatible; use a non-reasoning model
MODEL = 'gemma-3-12b-it'
EASY = 'Describe the situation in your own words; avoid copying rare identifiers, file names or numbers.'
HARD = ('Use synonyms and general terms: do NOT reuse any word of 5 or more letters that appears in the lesson, '
        'and no identifiers or numbers.')


def cases_path(hard):
    return ROOT / '.local/eval' / ('cases_hard.json' if hard else 'cases_easy.json')


def write_query(text, hard):
    prompt = ('Write ONE short search query (5-9 words) that a developer who has this problem might type to find the '
              f'lesson below. {HARD if hard else EASY} Output only the query.\n\nLESSON:\n{text[:900]}')
    body = {'model': MODEL, 'temperature': 0.3, 'max_tokens': 60, 'messages': [{'role': 'user', 'content': prompt}]}
    request = urllib.request.Request(LLM, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)['choices'][0]['message']['content'].strip().strip('"').split('\n')[-1]


def generate(hard, n, seed):
    store = service.MemoryStore()
    rows = store.db.query("SELECT id, text FROM memory WHERE status = 'active' AND string::len(text) > 200;")[0]
    random.seed(seed)
    cases = [(row['id'], write_query(row['text'], hard)) for row in random.sample(rows, min(n, len(rows)))]
    cases = [case for case in cases if case[1]]
    cases_path(hard).parent.mkdir(parents=True, exist_ok=True)
    cases_path(hard).write_text(json.dumps(cases))
    print(f'{len(cases)} cases saved to {cases_path(hard)}; e.g. {cases[:2]}')


def run(hard):
    cases = json.loads(cases_path(hard).read_text())
    store = service.MemoryStore()
    settings = {'floor': service.SEMANTIC_MIN, 'only': service.SEMANTIC_ONLY_MIN}
    for label, embedder in [('keyword only', lambda text, query=False: None), ('hybrid', embedding.embed)]:
        store.embedder = embedder
        hit5 = hit1 = reciprocal = 0
        for ident, query in cases:
            ids = [row['id'] for row in store.search(query, limit=5)['memories']]
            if ident in ids:
                hit5 += 1
                reciprocal += 1 / (ids.index(ident) + 1)
                hit1 += ids[0] == ident
        n = len(cases)
        print(f'{label:13} recall@5={hit5 / n:.2f} top1={hit1 / n:.2f} MRR={reciprocal / n:.2f}  (n={n})')
    print(f'hybrid thresholds: semantic floor {settings["floor"]}, semantic-only {settings["only"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['generate', 'run'])
    parser.add_argument('--hard', action='store_true')
    parser.add_argument('--n', type=int, default=40)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    generate(args.hard, args.n, args.seed) if args.command == 'generate' else run(args.hard)


if __name__ == '__main__':
    main()
