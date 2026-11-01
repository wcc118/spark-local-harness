"""Local text embeddings for semantic memory search.

Every failure returns None so callers fall back to keyword search; memory never blocks work.
Vectors from different backends are not comparable (nomic v1.5 via Ollama and via LM Studio
differ by cosine ~0.94), so each stored vector records TAG and only matching vectors are compared.
"""
import json
import os
import urllib.request

URL = os.environ.get('MEMORY_EMBED_URL', 'http://127.0.0.1:11434/api/embed')
MODEL = os.environ.get('MEMORY_EMBED_MODEL', 'nomic-embed-text')
TAG = os.environ.get('MEMORY_EMBED_TAG', 'ollama-nomic-embed-text-v1.5')
MAX_CHARS = 6000


def space(vector):
    """Name of the embedding space a vector lives in; the dimension guards against mixed-size comparisons."""
    return f'{TAG}-{len(vector)}'


def embed(text, query=False):
    """Return a unit-length vector, or None when the embedder is unavailable."""
    prefix = 'search_query: ' if query else 'search_document: '
    body = json.dumps({'model': MODEL, 'input': [prefix + text[:MAX_CHARS]], 'keep_alive': '10m'}).encode()
    try:
        request = urllib.request.Request(URL, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            vector = json.load(response)['embeddings'][0]
        norm = sum(x * x for x in vector) ** 0.5
        return [x / norm for x in vector] if norm else None
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def document_text(text, conditions='', topics=()):
    """The text embedded for a memory: claim, applicability and topic names."""
    return '\n'.join(part for part in [text, conditions, ' '.join(topics)] if part)
