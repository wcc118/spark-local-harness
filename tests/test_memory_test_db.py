"""Host capability lifecycle behavior with the Docker process boundary mocked."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from codex_local_harness import memory_test_db as capability


def result(code=0,value=None):
    return subprocess.CompletedProcess([],code,json.dumps(value) if value is not None else '', '')


def test_owned_cleanup_refuses_mismatched_label(monkeypatch):
    docker=Mock(return_value=result(value=[{'Config':{'Labels':{capability.LABEL:'someone-else'}}}]))
    monkeypatch.setattr(capability,'docker',docker)
    with pytest.raises(capability.CapabilityError,match='ownership label'):
        capability.cleanup('test-owner')
    assert [call.args[:2] for call in docker.call_args_list]==[('container','inspect')]


def test_missing_resource_is_not_success_when_daemon_unavailable(monkeypatch):
    docker=Mock(side_effect=[result(1),capability.CapabilityError('daemon unavailable')])
    monkeypatch.setattr(capability,'docker',docker)
    with pytest.raises(capability.CapabilityError,match='daemon unavailable'):
        capability.cleanup('test-daemon')
    assert docker.call_args_list[-1].args[0]=='info'


def test_start_collision_does_not_remove_existing_resources(monkeypatch,tmp_path):
    docker=Mock(return_value=result(value=[{}]));cleanup=Mock()
    monkeypatch.setattr(capability,'docker',docker);monkeypatch.setattr(capability,'cleanup',cleanup)
    with pytest.raises(capability.CapabilityError,match='already exist'):
        capability.start('test-collision',tmp_path/'profile.json')
    cleanup.assert_not_called()
    assert len(docker.call_args_list)==1


def test_partial_start_failure_cleans_only_owned_created_resources(monkeypatch,tmp_path):
    key=capability.identity('test-partial');calls=[]
    def docker(*args,**kwargs):
        calls.append(args)
        if args[:2]==('container','inspect'):
            return result(1)
        if args[:2]==('network','inspect'):
            return result(value=[{'Labels':{capability.LABEL:key}}]) if ('network','create') in [c[:2] for c in calls] else result(1)
        if args[0]=='run':raise capability.CapabilityError('container start failed')
        return result()
    monkeypatch.setattr(capability,'docker',docker)
    with pytest.raises(capability.CapabilityError,match='container start failed'):
        capability.start('test-partial',tmp_path/'profile.json')
    assert ('network','rm','codex-memory-test-'+key) in calls
    assert not any(c[:2]==('container','rm') for c in calls)
    assert not (tmp_path/'profile.json').exists()


def test_cleanup_removes_container_before_network_and_includes_volumes(monkeypatch):
    key=capability.identity('test-cleanup');calls=[]
    def docker(*args,**kwargs):
        calls.append(args)
        if args[:2]==('container','inspect'):return result(value=[{'Config':{'Labels':{capability.LABEL:key}}}])
        if args[:2]==('network','inspect'):return result(value=[{'Labels':{capability.LABEL:key}}])
        return result()
    monkeypatch.setattr(capability,'docker',docker);capability.cleanup('test-cleanup')
    removals=[c for c in calls if c[1]=='rm']
    assert removals==[('container','rm','--force','--volumes','codex-memory-test-'+key),('network','rm','codex-memory-test-'+key)]


def test_docker_failure_does_not_echo_process_output(monkeypatch):
    monkeypatch.setattr(subprocess,'run',Mock(return_value=subprocess.CompletedProcess([],1,'private payload','private stderr')))
    with pytest.raises(capability.CapabilityError) as caught:capability.docker('run','image')
    assert 'private' not in str(caught.value)
