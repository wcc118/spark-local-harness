import json
import os
from pathlib import Path

import pytest

from codex_local_harness.worker_containers import PREFIX, candidate, reap, snapshot


ID='a'*64


def container(**labels):
    values={'managed':'1','uid':str(os.getuid()),'run':'run1','role':'coder','pid':'123',
            'start_ticks':'456','started':'100'}
    values.update(labels)
    return {'Id':ID,'Name':'/codex-worker-run1','Config':{'Labels':{PREFIX+k:v for k,v in values.items()}}}


def gone(pid):
    raise FileNotFoundError


def test_only_positive_stale_ownership_can_be_reaped():
    assert candidate(container(),500,300,os.getuid(),gone)['run_id']=='run1'
    assert candidate(container(),500,300,os.getuid(),lambda pid:'456') is None
    assert candidate(container(),500,300,os.getuid(),lambda pid:'999') is not None  # PID reused
    assert candidate(container(),200,300,os.getuid(),gone) is None
    def unavailable(pid): raise PermissionError
    assert candidate(container(),500,300,os.getuid(),unavailable) is None
    for values in ({'managed':'0'},{'uid':'-1'},{'pid':'0'},{'start_ticks':'?'},
                   {'started':'nan'},{'role':'model'},{'run':'../bad'}):
        assert candidate(container(**values),500,300,os.getuid(),gone) is None
    for key,value in (('Name','/vllm'),('Id','not-an-id'),('Config',None)):
        item=container();item[key]=value
        assert candidate(item,500,300,os.getuid(),gone) is None


def test_reaper_uses_exact_container_identity_records_effect_and_does_not_stop_live(tmp_path):
    calls=[]
    def execute(*args):
        calls.append(args)
        if args[0]=='ps': return ID+'\n'
        if args[0]=='inspect': return json.dumps([container()])
        assert args==('stop','--time','5',ID)
        return ID
    result=reap(tmp_path,300,execute=execute,probe=gone,now=500)
    assert result['stale']==1 and result['stopped']==1 and result['errors']==0
    assert json.loads((tmp_path/'worker-containers.jsonl').read_text())['action']=='stopped'
    assert snapshot(tmp_path)==result
    calls.clear()
    result=reap(tmp_path,300,execute=execute,probe=lambda pid:'456',now=500)
    assert result['stopped']==0 and not any(c[0]=='stop' for c in calls)


def test_docker_failure_and_bad_inspection_do_not_claim_cleanup(tmp_path):
    def execute(*args):
        if args[0]=='ps': return ID
        if args[0]=='inspect': return json.dumps([container()])
        raise RuntimeError('failed')
    result=reap(tmp_path,300,execute=execute,probe=gone,now=500)
    assert result['stopped']==0 and result['errors']==1
    assert json.loads((tmp_path/'worker-containers.jsonl').read_text())['action']=='stop-failed'
    with pytest.raises(ValueError): reap(tmp_path,float('nan'),execute=execute)


@pytest.fixture(scope='module')
def lifetime_image(tmp_path_factory):
    import subprocess
    import uuid
    root=tmp_path_factory.mktemp('container-lifetime-image')
    tag='codex-lifetime-test:'+uuid.uuid4().hex
    if subprocess.run(['docker','image','inspect','codex-local-worker:0.3.0'],capture_output=True).returncode:
        pytest.skip('local worker image unavailable')
    script='''#!/bin/sh
case "$*" in *fixture-budget*) exec python3 /budget.py "$@" ;; *fixture-sleep*) exec sleep 300 ;; esac
sleep 1
printf '%s\\n' '{"type":"text","part":{"text":"fixture completed"}}'
'''
    (root/'budget.py').write_text("""import json, sys, time
args = ' '.join(sys.argv)
count = 2 if 'fixture-budget-2' in args else 3
for i in range(count):
    identity = str(i)
    if any(arg.startswith('qwen') for arg in sys.argv):
        events = [{'type':'assistant','message':{'content':[{'type':'tool_use','id':identity,'name':'read_file','input':{'path':identity}}]}},
                  {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':identity,'content':'fixture'}]}}]
    else:
        events = [{'type':'tool_use','part':{'callID':identity,'tool':'read','state':{'status':'completed','input':{'filePath':identity}}}}]
    for event in events: print(json.dumps(event), flush=True)
if 'slow' in args: time.sleep(30)
""")
    (root/'fixture.sh').write_text(script)
    (root/'Dockerfile').write_text('FROM codex-local-worker:0.3.0\nCOPY fixture.sh /fixture.sh\nCOPY budget.py /budget.py\nENTRYPOINT ["/bin/sh", "/fixture.sh"]\n')
    subprocess.run(['docker','build','-q','-t',tag,str(root)],check=True,capture_output=True,timeout=60)
    try: yield tag
    finally: subprocess.run(['docker','image','rm',tag],capture_output=True,timeout=15)


@pytest.mark.parametrize('runtime',['opencode','qwen-code'])
@pytest.mark.parametrize('ending',['normal','timeout','cancel','interrupt'])
@pytest.mark.parametrize('convergence',['0','1'])
def test_real_worker_container_lifetime(tmp_path,fake_base_api,lifetime_image,runtime,ending,convergence):
    """Execute the real wrapper and Docker paths, with inference replaced by a shell fixture."""
    import signal
    import subprocess
    import time
    root=Path(__file__).resolve().parents[1]
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-b','main',str(repo)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'config','user.email','fixture@example.invalid'],check=True)
    subprocess.run(['git','-C',str(repo),'config','user.name','Fixture'],check=True)
    (repo/'data').write_text('fixture')
    subprocess.run(['git','-C',str(repo),'add','data'],check=True)
    subprocess.run(['git','-C',str(repo),'commit','-m','fixture'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'remote','add','origin','https://github.com/wcc118/example.git'],check=True)
    role='coder' if runtime=='qwen-code' else 'helper'
    routes=tmp_path/'routes.json';routes.write_text(json.dumps({'defaults':{role:'standard'},'routes':{
        role+'/standard':{'agent':role,'model':'vllm/test','runtime':runtime,'mode':'native'}}}))
    env=os.environ | {'CODEX_LOCAL_WORKER_CONFIG':str(routes),'CODEX_LOCAL_SOURCE':str(root/'src'),
        'CODEX_LOCAL_CONTAINER_IMAGE':lifetime_image,'CODEX_LOCAL_MODEL_SWITCH':'0','CODEX_LOCAL_SESSION_BRIDGE':'0',
        'CODEX_LOCAL_RECORD_RUN':convergence,'CODEX_LOCAL_REPOSITORY':'wcc118/example','CODEX_LOCAL_ISSUE':'193',
        'CODEX_LOCAL_SESSION_ID':'fixture-container-lifetime','CODEX_LOCAL_CODER_POSTFLIGHT':'0','CODEX_LOCAL_CONVERGENCE':convergence,
        'CODEX_LOCAL_TIMEOUT_SECONDS':'3' if ending=='timeout' else '20',
        'CODEX_LOCAL_MEMORY_CONFIG':str(tmp_path/'no-memory'), 'CODEX_LOCAL_HEALTH_DIR':str(tmp_path/'health'),
        'CODEX_LOCAL_ISOLATION_PARENT':str(tmp_path/'isolated'),'CODEX_LOCAL_SANDBOX_MOUNTS':str(tmp_path/'no-mounts')}
    output=tmp_path/'output'
    with output.open('w') as handle:
        proc=subprocess.Popen([str(root/'bin/codex-local-worker-v4'),role,str(repo),
            'fixture-normal' if ending=='normal' else 'fixture-sleep'],env=env,stdout=handle,stderr=handle,start_new_session=True)
        identifier=None
        try:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and proc.poll() is None:
                ids=subprocess.check_output(['docker','ps','-q','--no-trunc','--filter',
                    'label='+PREFIX+'pid='+str(proc.pid)],text=True).split()
                if ids:
                    identifier=ids[0];break
                time.sleep(.1)
            assert identifier, output.read_text()
            item=json.loads(subprocess.check_output(['docker','inspect',identifier],text=True))[0]
            labels=item['Config']['Labels']
            assert item['Name']=='/codex-worker-'+labels[PREFIX+'run']
            assert labels[PREFIX+'role']==role and labels[PREFIX+'start_ticks'].isdigit()
            if ending in {'cancel', 'interrupt'}:
                os.killpg(proc.pid, signal.SIGTERM if ending == 'cancel' else signal.SIGINT)
            status=proc.wait(timeout=25)
            assert status in ({'normal':{0},'timeout':{124,137},'cancel':{143},'interrupt':{130}}[ending]),output.read_text()
            remaining=subprocess.check_output(['docker','ps','-q','--no-trunc','--filter','id='+identifier],text=True)
            assert not remaining.strip()
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            if identifier:
                subprocess.run(['docker','stop','--time','1',identifier],capture_output=True,timeout=5)


def test_real_reaper_stops_only_its_owned_orphan(tmp_path,lifetime_image):
    import subprocess
    import time
    import uuid
    from codex_local_harness.worker_containers import docker
    run='fixture-'+uuid.uuid4().hex
    arguments=['run','-d','--rm','--name','codex-worker-'+run]
    labels={'managed':'1','uid':str(os.getuid()),'run':run,'role':'coder','pid':'99999999',
            'start_ticks':'1','started':str(time.time()-3600)}
    for key,value in labels.items(): arguments+=['--label',PREFIX+key+'='+value]
    identifier=docker(*arguments,lifetime_image,'fixture-sleep').strip()
    def restricted(*args):
        if args[0]=='ps':
            return docker(*args,'--filter','label='+PREFIX+'run='+run)
        return docker(*args)
    try:
        result=reap(tmp_path,1800,execute=restricted)
        assert result['stale']==1 and result['stopped']==1 and result['errors']==0
        assert not docker('ps','-q','--filter','id='+identifier).strip()
    finally:
        subprocess.run(['docker','stop','--time','1',identifier],capture_output=True,timeout=5)


@pytest.mark.parametrize('runtime', ['opencode', 'qwen-code'])
@pytest.mark.parametrize('calls,slow', [(2, False), (3, False), (3, True)])
def test_real_supervised_tool_budget(tmp_path, fake_base_api, lifetime_image, runtime, calls, slow):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
    for key, value in [('user.email', 'fixture@example.invalid'), ('user.name', 'Fixture')]:
        subprocess.run(['git', '-C', str(repo), 'config', key, value], check=True)
    (repo / 'data').write_text('original')
    subprocess.run(['git', '-C', str(repo), 'add', 'data'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-m', 'fixture'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', 'https://github.com/wcc118/example.git'], check=True)
    routes = tmp_path / 'routes.json'
    routes.write_text(json.dumps({'defaults': {'coder': 'standard'}, 'routes': {'coder/standard': {
        'agent': 'coder', 'model': 'vllm/test', 'runtime': runtime, 'mode': 'native'}}}))
    health = tmp_path / 'health'
    env = os.environ | {'CODEX_LOCAL_WORKER_CONFIG': str(routes), 'CODEX_LOCAL_SOURCE': str(root / 'src'),
        'CODEX_LOCAL_CONTAINER_IMAGE': lifetime_image, 'CODEX_LOCAL_MODEL_SWITCH': '0',
        'CODEX_LOCAL_SESSION_BRIDGE': '0', 'CODEX_LOCAL_RECORD_RUN': '1',
        'CODEX_LOCAL_REPOSITORY': 'wcc118/example', 'CODEX_LOCAL_ISSUE': '265',
        'CODEX_LOCAL_SESSION_ID': 'fixture-budget', 'CODEX_LOCAL_CODER_POSTFLIGHT': '0',
        'CODEX_LOCAL_TIMEOUT_SECONDS': '40', 'CODEX_LOCAL_TOOL_CALL_BUDGET': '2',
        'CODEX_LOCAL_MEMORY_CONFIG': str(tmp_path / 'no-memory'), 'CODEX_LOCAL_HEALTH_DIR': str(health),
        'CODEX_LOCAL_LOG_DIR': str(tmp_path / 'logs'), 'CODEX_LOCAL_ISOLATION_PARENT': str(tmp_path / 'isolated'),
        'CODEX_LOCAL_SANDBOX_MOUNTS': str(tmp_path / 'no-mounts')}
    task = f'fixture-budget-{calls}-' + ('slow' if slow else 'fast')
    result = subprocess.run([str(root / 'bin/codex-local-worker-candidate'), 'coder', str(repo), task],
                            env=env, capture_output=True, text=True, timeout=55)
    assert result.returncode == (75 if calls > 2 else 0), result.stderr
    terminal = json.loads((health / 'execution.jsonl').read_text())
    assert terminal['stop_reason'] == ('step-budget' if calls > 2 else None)
    assert terminal['publication'] is None
    assert Path(terminal['checkout']).exists()
    assert (repo / 'data').read_text() == 'original'
    if calls > 2:
        marker = json.loads((health / (terminal['run_id'] + '.tool-budget.json')).read_text())
        assert marker['count'] == 3 and marker['limit'] == 2
    remaining = subprocess.check_output(['docker', 'ps', '-q', '--filter',
        'label=' + PREFIX + 'run=' + terminal['run_id']], text=True)
    assert not remaining.strip()
