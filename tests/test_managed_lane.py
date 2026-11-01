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
