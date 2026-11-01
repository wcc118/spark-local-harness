from codex_local_harness.managed_lane import admit
from codex_local_harness.lane import LaneStatus
import pytest

CONFIG={'routes': {'coder/managed-3090': {'model':'lmstudio/qwen/qwen3.8-27b','runtime':'qwen-code','mode':'native'}}}

@pytest.mark.parametrize('status', ['ok','unreachable','model-not-loaded','context-too-small'])
def test_admission_uses_loaded_state_and_shared_endpoint(status):
    calls=[]
    def check(*args, **kwargs):
        calls.append((args,kwargs))
        return LaneStatus(status,'qwen/qwen3.8-27b',65536,None,0.1,'now','private detail')
    tier, model, result=admit(CONFIG,{'LMSTUDIO_BASE_URL':'http://host:1234/v1'},check)
    assert tier=='managed-3090' and model.startswith('lmstudio/')
    assert calls==[(('http://host:1234/v1','qwen/qwen3.8-27b'),dict(min_context=65536,timeout=8.0,token=None))]
    assert result['status']==status and 'detail' not in result


def test_override_cannot_bypass_loaded_model_check():
    with pytest.raises(ValueError):
        admit(CONFIG,{'CODEX_LOCAL_MODEL':'vllm/other'})


def test_no_default_route_fallback():
    with pytest.raises(ValueError):
        admit({}, {})


def test_native_worker_uses_exact_endpoint_and_does_not_put_token_in_argv(tmp_path):
    import os, subprocess
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'bin/codex-local-worker-v4').read_text()
    start=source.index('    qwen_model=${worker_model#vllm/}')
    end=source.index('    worker_server_pid=$!',start)
    script='''docker_args=(); docker() { printf '%s\\0' "$@" > "$captured"; }; export -f docker
    timeout() { shift 4; "$@"; }
    '''+source[start:end]+'\nwait\n'
    env=os.environ | dict(worker_model='lmstudio/qwen/qwen3.8-27b',worker_container_image='fixture',
        worker_timeout='5',worker_task='fixture',worker_log=str(tmp_path/'log'),
        LMSTUDIO_BASE_URL='http://pc:1234/v1',LMSTUDIO_API_TOKEN='test-only-token',captured=str(tmp_path/'args'))
    result=subprocess.run(['bash','-eu','-c',script],env=env,capture_output=True)
    assert result.returncode==0,result.stderr
    args=(tmp_path/'args').read_bytes().decode().split('\0')
    assert 'OPENAI_BASE_URL=http://pc:1234/v1' in args
    assert 'qwen/qwen3.8-27b' in args
    assert 'OPENAI_API_KEY' in args
    assert not any('test-only-token' in arg for arg in args)


def test_main_unloaded_and_busy_skip_without_worker(tmp_path, monkeypatch, capsys):
    import json
    from codex_local_harness import managed_lane
    from codex_local_harness.lane import GpuLease
    monkeypatch.setenv('HOME',str(tmp_path))
    config=tmp_path/'config.json';config.write_text(json.dumps(CONFIG))
    monkeypatch.setenv('CODEX_LOCAL_WORKER_CONFIG',str(config))
    calls=[]
    monkeypatch.setattr(managed_lane.os,'execve',lambda *args: calls.append(args))
    monkeypatch.setattr(managed_lane,'admit',lambda *args: ('managed-3090','lmstudio/qwen/qwen3.8-27b',dict(status='model-not-loaded',loaded_context=None)))
    assert managed_lane.main(['repo','task'])==0
    assert json.loads(capsys.readouterr().out)['disposition']=='skipped'
    lease=GpuLease('gpu-advisory','test',tmp_path/'.local/share/codex-local-harness')
    assert lease.acquire(timeout=0)
    try:
        assert managed_lane.main(['repo','task'])==0
        assert json.loads(capsys.readouterr().out)['status']=='lease-busy'
        assert lease.sidecar_file.exists()
        assert lease.holder()['purpose']=='test'
    finally:
        lease.release()
    assert not calls


def test_main_holds_lease_through_exec_and_forces_bounds(tmp_path, monkeypatch):
    import json, os
    from codex_local_harness import managed_lane
    from codex_local_harness.lane import GpuLease
    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.delenv('CODEX_LOCAL_DRY_RUN',raising=False)
    config=tmp_path/'config.json';config.write_text(json.dumps(CONFIG))
    monkeypatch.setenv('CODEX_LOCAL_WORKER_CONFIG',str(config))
    monkeypatch.setattr(managed_lane,'admit',lambda *args: ('managed-3090','lmstudio/qwen/qwen3.8-27b',dict(status='ok',loaded_context=65536)))
    class Executed(Exception): pass
    def execute(path,args,env):
        competitor=GpuLease('gpu-advisory','test',tmp_path/'.local/share/codex-local-harness')
        assert not competitor.acquire(timeout=0)
        assert env['CODEX_LOCAL_MODEL_SWITCH']=='0'
        assert env['CODEX_LOCAL_TOOL_CALL_BUDGET']=='80'
        assert env['CODEX_LOCAL_TIMEOUT_SECONDS']=='900'
        assert env['CODEX_LOCAL_LANE_CONTEXT']=='65536'
        assert args[1:] == ['coder','repo','task']
        raise Executed
    monkeypatch.setattr(managed_lane.os,'execve',execute)
    with pytest.raises(Executed): managed_lane.main(['repo','task'])
    lease=GpuLease('gpu-advisory','after',tmp_path/'.local/share/codex-local-harness')
    assert lease.acquire(timeout=0)
    lease.release()
