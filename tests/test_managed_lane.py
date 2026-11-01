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
