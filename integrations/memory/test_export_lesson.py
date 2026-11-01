import unittest
from unittest.mock import Mock
from export_lesson import digest, publish


class ExportTests(unittest.TestCase):
    def proposal(self):
        value = dict(text='Test behavior independently.', source='public issue', request_id='export-test',
                     reviewed_by='codex', review_note='No expected values or private grading details.')
        value['approved_digest'] = digest(value)
        return value

    def test_only_reviewed_public_text_is_written_as_hypothesis(self):
        store = Mock()
        publish(self.proposal(), store)
        self.assertEqual(store.write.call_args.kwargs['kind'], 'hypothesis')
        self.assertEqual(store.write.call_args.args[0].actor, 'codex')

    def test_changed_or_unreviewed_content_cannot_publish(self):
        for change in ({'text':'changed'}, {'reviewed_by':'coder'}, {'review_note':''},
                       {'approved_digest':''}, {'oracle_record':{'expected':42}}):
            value = self.proposal() | change
            store = Mock()
            with self.assertRaises(ValueError): publish(value, store)
            store.write.assert_not_called()
