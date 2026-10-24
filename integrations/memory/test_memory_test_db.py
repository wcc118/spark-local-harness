"""Fixture-boundary tests: exercise actual setup/teardown with external I/O mocked."""
import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import test_memory_service as service


class FixtureLifecycleTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            profile = Path(directory)/'profile.json'
            profile.write_text(json.dumps(dict(schema=1,disposable=True,endpoint='http://172.18.0.2:8000',namespace='memory_test',username='memory_test',password='x'*32)))
            stack.enter_context(patch.dict(os.environ, {'MEMORY_TEST_PROFILE':str(profile)}))
            database = stack.enter_context(patch.object(service,'DisposableDatabase')).return_value
            client = stack.enter_context(patch.object(service,'TestClient')).return_value
            stack.enter_context(patch.object(service,'MemoryStore'))
            stack.enter_context(patch.object(service,'build_server'))
            stack.enter_context(patch.object(service,'AuthenticatedApp'))
            admin = stack.enter_context(patch.object(service,'admin_query',side_effect=AssertionError('host access forbidden')))
            creds = stack.enter_context(patch.object(service,'credentials',side_effect=AssertionError('host credential access forbidden')))
            yield profile,database,client,admin,creds

    def test_schema_failure_removes_created_database(self):
        with self.fixture() as (_,database,_,admin,creds):
            database.query.side_effect = RuntimeError('schema failed')
            with self.assertRaisesRegex(RuntimeError,'schema failed'):
                service.MemoryIntegration.setUpClass()
            database.create.assert_called_once()
            database.remove.assert_called_once()
            admin.assert_not_called();creds.assert_not_called()

    def test_client_startup_failure_removes_database(self):
        with self.fixture() as (_,database,client,_,_):
            client.__enter__.side_effect = RuntimeError('client start failed')
            with self.assertRaisesRegex(RuntimeError,'client start failed'):
                service.MemoryIntegration.setUpClass()
            client.__enter__.assert_called_once()
            database.remove.assert_called_once()

    def test_client_exit_failure_does_not_skip_database_cleanup(self):
        with self.fixture() as (_,database,client,_,_):
            service.MemoryIntegration.setUpClass()
            client.__exit__.side_effect = RuntimeError('client exit failed')
            with self.assertRaisesRegex(RuntimeError,'client exit failed'):
                service.MemoryIntegration.tearDownClass()
            client.__exit__.assert_called_once()
            database.remove.assert_called_once()

    def test_setup_and_cleanup_errors_are_both_visible(self):
        with self.fixture() as (_,database,client,_,_):
            client.__enter__.side_effect = RuntimeError('original startup failure')
            database.remove.side_effect = RuntimeError('cleanup failure')
            with self.assertRaises(RuntimeError) as caught:
                service.MemoryIntegration.setUpClass()
            self.assertIn('original startup failure',str(caught.exception))
            self.assertIn('cleanup failure',str(caught.exception))
            self.assertIn('original startup failure',str(caught.exception.__cause__))
            database.remove.assert_called_once()

    def test_explicit_empty_missing_invalid_profiles_never_fall_back(self):
        for mode in ('empty','missing','invalid'):
            with self.subTest(mode=mode), self.fixture() as (profile,_,_,admin,creds):
                if mode=='empty':os.environ['MEMORY_TEST_PROFILE']=''
                elif mode=='missing':profile.unlink()
                else:profile.write_text('{}')
                # Use the real validator for invalid contents, not a mock that simply raises.
                from test_database import DisposableDatabase
                with patch.object(service,'DisposableDatabase',DisposableDatabase):
                    with self.assertRaises(RuntimeError):service.MemoryIntegration.setUpClass()
                admin.assert_not_called();creds.assert_not_called()


if __name__=='__main__':unittest.main()
