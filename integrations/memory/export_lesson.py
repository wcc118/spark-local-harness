#!/usr/bin/env python3
"""Publish explicitly reviewed generalizations, never copy private oracle records.

Input is newly authored public text. This command cannot determine whether prose
contains an answer key: the frontier/owner review is the trust boundary.
"""
import argparse
import hashlib
import json
from pathlib import Path

from memory_service import Database, MemoryStore, Principal

FIELDS = {'text', 'source', 'request_id', 'conditions', 'topics', 'project',
          'reviewed_by', 'review_note', 'approved_digest'}


def digest(proposal):
    content = {k: v for k, v in proposal.items() if k != 'approved_digest'}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def publish(proposal, store=None):
    if not isinstance(proposal, dict) or set(proposal) - FIELDS:
        raise ValueError('Only explicit public proposal fields are accepted')
    if proposal.get('reviewed_by') not in {'codex', 'claude', 'owner'}:
        raise ValueError('An owner or frontier review is required')
    if not isinstance(proposal.get('review_note'), str) or not proposal['review_note'].strip():
        raise ValueError('Record the review of expected values and check-specific details')
    if proposal.get('approved_digest') != digest(proposal):
        raise ValueError('Approval digest does not match the reviewed proposal')
    store = store or MemoryStore(Database('knowledge'))
    return store.write(Principal(role='frontier', actor=proposal['reviewed_by']),
                       proposal.get('text'), proposal.get('source'),
                       proposal.get('request_id'), topics=proposal.get('topics', []),
                       project=proposal.get('project', ''), kind='hypothesis',
                       conditions=proposal.get('conditions', '') + '\nExport review: ' + proposal['review_note'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proposal', type=Path)
    parser.add_argument('--digest', action='store_true', help='Print content digest for a completed review; no publication')
    args = parser.parse_args()
    proposal = json.loads(args.proposal.read_text())
    if args.digest:
        print(digest(proposal))
    else:
        result = publish(proposal)
        print(json.dumps({'id': result['memory']['id'], 'kind': 'hypothesis',
                          'replayed': result.get('replayed', False)}))


if __name__ == '__main__':
    main()
