"""Explicit offline GitHub API fixture for worker process tests."""
import os
import pytest


@pytest.fixture
def fake_base_api(tmp_path, monkeypatch):
    directory = tmp_path / 'fake-base-api'
    directory.mkdir()
    command = directory / 'gh'
    command.write_text('#!/bin/sh\n'
                       'case "$1:$2" in\n'
                       '  api:repos/*/git/ref/heads/main) exec git rev-parse HEAD ;;\n'
                       '  *) echo "unexpected fixture GitHub command" >&2; exit 2 ;;\n'
                       'esac\n')
    command.chmod(0o755)
    monkeypatch.setenv('PATH', str(directory) + os.pathsep + os.environ['PATH'])
