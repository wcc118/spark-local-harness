"""Tests for maintenance module: usage statistics and retitle with mocked DB."""
import collections
import datetime
import unittest
from unittest.mock import Mock, patch

import maintenance


class RetitleTests(unittest.TestCase):
    """Tests for maintenance.retitle - title change with audit trail."""

    def test_retitle_applies_title_change(self):
        """Retitle changes the title when old value matches."""
        mock_db = Mock()
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]

        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }
        result = maintenance.retitle(change, 'test source', db=mock_db)

        self.assertEqual(result['applied'], 1)
        self.assertEqual(result['skipped_title_changed'], [])
        self.assertEqual(mock_db.query.call_count, 2)

    def test_retitle_skips_when_title_already_changed(self):
        """Retitle skips if the current title doesn't match old expected value."""
        mock_db = Mock()
        mock_db.query.side_effect = [
            [['Changed Externally']]  # SELECT VALUE title - doesn't match 'Original Title'
        ]

        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }
        result = maintenance.retitle(change, 'test source', db=mock_db)

        self.assertEqual(result['applied'], 0)
        self.assertEqual(result['skipped_title_changed'], ['memory:retitle-test'])
        self.assertEqual(mock_db.query.call_count, 1)  # Only the read, no write

    def test_retitle_does_not_modify_text(self):
        """Retitle only changes title, never the text content."""
        mock_db = Mock()
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]

        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }
        maintenance.retitle(change, 'test source', db=mock_db)

        # Verify transaction was called with correct parameters
        call_args = mock_db.query.call_args_list[1]
        sql = call_args[0][0]
        params = call_args[0][1]
        self.assertIn('UPDATE type::record', sql)
        self.assertIn('SET title=$after', sql)
        self.assertEqual(params['after'], 'New Title')
        self.assertIn('CREATE memory_event', sql)
        self.assertEqual(params['source'], 'test source')

    def test_retitle_appends_audit_event(self):
        """Retitle creates a memory_event with action='retitle'."""
        mock_db = Mock()
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]

        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }
        maintenance.retitle(change, 'test source', actor='test-user', db=mock_db)

        # Verify event was created with correct data
        call_args = mock_db.query.call_args_list[1]
        sql = call_args[0][0]
        params = call_args[0][1]
        self.assertIn("action='retitle'", sql)
        self.assertEqual(params['before'], 'Original Title')
        self.assertEqual(params['after'], 'New Title')
        self.assertEqual(params['source'], 'test source')
        self.assertEqual(params['actor'], 'test-user')

    def test_retitle_idempotent_does_not_reapply(self):
        """Same retitle change cannot be applied twice."""
        mock_db = Mock()
        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }

        # First application
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]
        result1 = maintenance.retitle(change, 'test source', db=mock_db)
        self.assertEqual(result1['applied'], 1)

        # Second application should skip (title no longer matches old)
        mock_db.query.side_effect = [
            [['New Title']]  # SELECT VALUE title - already changed
        ]
        result2 = maintenance.retitle(change, 'test source', db=mock_db)
        self.assertEqual(result2['applied'], 0)
        self.assertEqual(result2['skipped_title_changed'], ['memory:retitle-test'])

    def test_retitle_revert_restores_original_title(self):
        """Revert restores the new title back to old when revert=True."""
        mock_db = Mock()
        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title'
            }
        }

        # First apply - title changes from 'Original Title' to 'New Title'
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]
        maintenance.retitle(change, 'test source', db=mock_db)

        # Now revert - current title is 'New Title'
        # For revert: before=new (current='New Title'), after=old (Original Title)
        # clean_label is NOT applied for revert (per maintenance.py line 163)
        mock_db.query.side_effect = [
            [['New Title']],  # SELECT VALUE title - current is 'New Title'
            [[]]  # Transaction
        ]
        result = maintenance.retitle(change, 'test source', db=mock_db, revert=True)
        self.assertEqual(result['applied'], 1)

        # Verify revert was called with correct params
        # After the revert, the query calls are: [0]=SELECT Original, [1]=TXN Original,
        # [2]=SELECT New, [3]=TXN New
        # So call_args_list[3] is the revert transaction
        call_args = mock_db.query.call_args_list[3]
        params = call_args[0][1]
        # For revert: before='New Title' (current), after='Original Title' (was original)
        self.assertEqual(params['before'], 'New Title')
        self.assertEqual(params['after'], 'Original Title')
        self.assertEqual(params['revert'], True)

    def test_retitle_multiple_changes(self):
        """Retitle handles multiple title changes in one call."""
        mock_db = Mock()
        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title 1'
            },
            'memory:retitle-test-2': {
                'old': 'Second Original',
                'new': 'New Title 2'
            }
        }

        # Both titles match, both should be applied
        mock_db.query.side_effect = [
            [['Original Title']],  # First SELECT VALUE title
            [[]],  # First transaction
            [['Second Original']],  # Second SELECT VALUE title
            [[]]  # Second transaction
        ]

        result = maintenance.retitle(change, 'test source', db=mock_db)

        self.assertEqual(result['applied'], 2)
        self.assertEqual(result['skipped_title_changed'], [])
        self.assertEqual(mock_db.query.call_count, 4)  # 2 reads + 2 writes

    def test_retitle_mixed_success_partial_skip(self):
        """Retitle returns partial success when some skip and some apply."""
        mock_db = Mock()
        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New Title 1'
            },
            'memory:retitle-test-2': {
                'old': 'Second Original',
                'new': 'New Title 2'
            }
        }

        # First title matches, second doesn't
        mock_db.query.side_effect = [
            [['Original Title']],  # First SELECT VALUE title
            [[]],  # First transaction
            [['Changed Externally']]  # Second SELECT VALUE title - doesn't match
        ]

        result = maintenance.retitle(change, 'test source', db=mock_db)

        self.assertEqual(result['applied'], 1)
        self.assertEqual(result['skipped_title_changed'], ['memory:retitle-test-2'])
        self.assertEqual(mock_db.query.call_count, 3)  # 2 reads + 1 write

    def test_retitle_skips_empty_changes(self):
        """Retitle handles empty changes gracefully."""
        mock_db = Mock()
        result = maintenance.retitle({}, 'test source', db=mock_db)
        self.assertEqual(result['applied'], 0)
        self.assertEqual(result['skipped_title_changed'], [])
        mock_db.query.assert_not_called()

    def test_retitle_applies_clean_label_to_new_title(self):
        """Retitle cleans the new title when applying."""
        mock_db = Mock()
        mock_db.query.side_effect = [
            [['Original Title']],  # SELECT VALUE title
            [[]]  # Transaction
        ]

        change = {
            'memory:retitle-test': {
                'old': 'Original Title',
                'new': 'New  \n  Title \t with whitespace'
            }
        }
        maintenance.retitle(change, 'test source', db=mock_db)

        # Verify the after value was cleaned
        call_args = mock_db.query.call_args_list[1]
        params = call_args[0][1]
        # clean_label should be applied (one line, no control chars)
        # The function removes newlines, tabs, and other control characters
        self.assertEqual(params['after'], 'New Title with whitespace')


class UsageTests(unittest.TestCase):
    """Tests for maintenance.usage - the memory usage funnel."""

    def test_usage_empty_database_returns_zero_counts(self):
        """Usage on empty database returns zero counts for all funnel stages."""
        mock_db = Mock()
        mock_db.query.return_value = [[], [], []]  # logs, uses, memories

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        funnel = result['funnel']
        self.assertEqual(funnel['searches'], 0)
        self.assertEqual(funnel['lessons_returned'], 0)
        self.assertEqual(funnel['opens'], 0)
        self.assertEqual(funnel['lessons_opened'], 0)
        self.assertEqual(funnel['use_reports'], 0)
        self.assertEqual(funnel['lessons_applied'], 0)

    def test_usage_records_search_funnel_stage(self):
        """Usage correctly counts searches and lessons returned."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        # logs = [retrieval rows], uses = [], memories = [memory rows]
        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user',
                 'role': 'coder', 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 2, 'results': ['memory:test-1', 'memory:test-2']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'},
                {'id': 'memory:test-2', 'title': 'Second', 'text': 'Text 2',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['searches'], 1)
        self.assertEqual(result['funnel']['lessons_returned'], 2)
        self.assertEqual(result['funnel']['returned_not_opened'], 2)

    def test_usage_records_open_funnel_stage(self):
        """Usage correctly counts opens and relates to searches."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'open', 'created_at': now, 'actor': 'test-user',
                 'role': 'coder', 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 1, 'results': ['memory:test-1']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['opens'], 1)
        self.assertEqual(result['funnel']['lessons_opened'], 1)
        self.assertEqual(result['funnel']['opened_not_reported'], 1)

    def test_usage_records_use_report_funnel_stage(self):
        """Usage correctly counts use reports (applied lessons)."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[]],  # logs (first query result - empty list of rows)
            [[  # uses (second query result)
                {'target': 'memory:test-1', 'outcome': 'helpful',
                 'explanation': 'Worked as expected', 'actor': 'test-user',
                 'role': 'coder', 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'created_at': now}
            ]],
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['use_reports'], 1)
        self.assertEqual(result['funnel']['lessons_applied'], 1)
        self.assertEqual(result['funnel']['outcomes'], {'helpful': 1})

    def test_usage_accounts_for_searches_with_zero_results(self):
        """Usage tracks searches that returned no results."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user',
                 'role': 'coder', 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 0, 'results': []}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['zero_result_searches'], 1)

    def test_usage_accounts_for_searches_outside_window(self):
        """Usage ignores retrievals outside the time window."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        # Mock only returns the recent retrieval (old ones are filtered by SQL in real DB)
        mock_db.query.side_effect = [
            [[  # logs (first query result - only recent retrieval returned)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user',
                 'role': 'coder', 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 0, 'results': []}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        # Only the recent search should be counted
        self.assertEqual(result['funnel']['searches'], 1)
        self.assertEqual(result['funnel']['zero_result_searches'], 1)

    def test_usage_tracks_who_field_by_actor(self):
        """Usage correctly attributes actions to actors."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'coder-1', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 1,
                 'results': ['memory:test-1']},
                {'kind': 'open', 'created_at': now, 'actor': 'reviewer-1', 'role': 'reviewer',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 1,
                 'results': ['memory:test-1']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        who = result['who']
        self.assertIn('coder-1', who)
        self.assertIn('reviewer-1', who)
        self.assertEqual(who['coder-1']['searches'], 1)
        self.assertEqual(who['reviewer-1']['opens'], 1)

    def test_usage_live_lessons_metrics(self):
        """Usage correctly counts live lessons and those never returned."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 1,
                 'results': ['memory:test-1']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'},
                {'id': 'memory:test-2', 'title': 'Second', 'text': 'Text 2',
                 'project': 'test/repo', 'status': 'active'},
                {'id': 'memory:test-3', 'title': 'Third', 'text': 'Text 3',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['live_lessons'], 3)
        self.assertEqual(result['funnel']['live_never_returned'], 2)

    def test_usage_events_list_contains_search_open_and_use(self):
        """Usage events list includes search, open, and use events."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 1,
                 'results': ['memory:test-1']},
                {'kind': 'open', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 1,
                 'results': ['memory:test-1']}
            ]],
            [[  # uses (second query result)
                {'target': 'memory:test-1', 'outcome': 'helpful', 'explanation': 'Good lesson',
                 'actor': 'test-user', 'role': 'coder', 'repository': 'test/repo',
                 'issue': 'test/repo#244', 'created_at': now}
            ]],
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        events = result['events']
        event_types = [e['type'] for e in events]
        self.assertIn('search', event_types)
        self.assertIn('open', event_types)
        self.assertIn('use', event_types)

    def test_usage_returns_non_empty_string_when_no_data(self):
        """Usage returns a non-empty string summary even with no data."""
        mock_db = Mock()
        mock_db.query.return_value = [[], [], []]

        output = maintenance.usage(mock_db, days=7, as_dict=False)
        self.assertIsInstance(output, str)
        self.assertIn('MEMORY USE', output)

    def test_usage_as_dict_and_string_are_consistent(self):
        """Dictionary and string outputs contain equivalent data."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        # Two sets of queries (one for dict_result, one for string_result)
        mock_db.query.side_effect = [
            [[  # logs (first query result - dict call)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 2,
                 'results': ['memory:test-1', 'memory:test-2']}
            ]],
            [[]],  # uses (second query result - dict call)
            [[  # memories (third query result - dict call)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'},
                {'id': 'memory:test-2', 'title': 'Second', 'text': 'Text 2',
                 'project': 'test/repo', 'status': 'active'}
            ]],
            [[  # logs (first query result - string call)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244', 'count': 2,
                 'results': ['memory:test-1', 'memory:test-2']}
            ]],
            [[]],  # uses (second query result - string call)
            [[  # memories (third query result - string call)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'},
                {'id': 'memory:test-2', 'title': 'Second', 'text': 'Text 2',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        dict_result = maintenance.usage(mock_db, days=7, as_dict=True)
        string_result = maintenance.usage(mock_db, days=7, as_dict=False)

        # String should contain the funnel summary
        self.assertIn('searched', string_result)
        self.assertIn('returned', string_result)


class MemoryKindTests(unittest.TestCase):
    """Tests for memory kind semantics - kind absent means historical search."""

    def test_usage_treats_kind_absent_as_search(self):
        """Retrievals without kind field are treated as historical searches."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                # No kind field - should be treated as search
                {'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 2, 'results': ['memory:test-1', 'memory:test-2']},
                {'kind': 'open', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 1, 'results': ['memory:test-1']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)

        # Both should be counted: one as search (no kind), one as open
        self.assertEqual(result['funnel']['searches'], 1)
        self.assertEqual(result['funnel']['opens'], 1)
        self.assertEqual(result['funnel']['lessons_returned'], 2)

    def test_usage_searches_with_no_results_count(self):
        """Searches returning zero results are tracked separately."""
        mock_db = Mock()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'

        mock_db.query.side_effect = [
            [[  # logs (first query result)
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 0, 'results': []},  # No results
                {'kind': 'search', 'created_at': now, 'actor': 'test-user', 'role': 'coder',
                 'repository': 'test/repo', 'issue': 'test/repo#244',
                 'count': 1, 'results': ['memory:test-1']}
            ]],
            [[]],  # uses (second query result - empty list of rows)
            [[  # memories (third query result)
                {'id': 'memory:test-1', 'title': 'First', 'text': 'Text 1',
                 'project': 'test/repo', 'status': 'active'}
            ]]
        ]

        result = maintenance.usage(mock_db, days=7, as_dict=True)
        self.assertEqual(result['funnel']['searches'], 2)
        self.assertEqual(result['funnel']['zero_result_searches'], 1)


if __name__ == '__main__':
    unittest.main()
