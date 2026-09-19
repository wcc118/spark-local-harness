"""Run a no-inference OpenCode/container permissions probe from the repository root."""
import json, os, pathlib, shutil, subprocess, tempfile
from codex_local_harness.review_profile import render
root=pathlib.Path(tempfile.mkdtemp(prefix='issue181-permissions-'))
home=root/'home'; checkout=root/'checkout'; scratch=root/'scratch'
for p in (home/'.config/opencode/agents',checkout,scratch): p.mkdir(parents=True)
(checkout/'untouched').write_text('original')
(home/'.config/opencode/opencode.json').write_text(json.dumps({'permission':{'external_directory':'deny'}}))
(home/'.config/opencode/agents/convergence-reviewer.md').write_text(render(pathlib.Path('agents/convergence-reviewer.md').read_text(),checkout,scratch))
base=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,nosuid,nodev,size=1g']
for p,ro in ((home,False),(scratch,False),(checkout,True)):
 base+=['--mount',f'type=bind,src={p},dst={p}'+(',readonly' if ro else '')]
base+=['--mount',f'type=bind,src={shutil.which("opencode")},dst=/usr/local/bin/opencode,readonly','--workdir',str(checkout),'--env',f'HOME={home}','--env',f'XDG_CONFIG_HOME={home}/.config','codex-local-worker:0.2.0','opencode','debug','agent','convergence-reviewer']
config=subprocess.run(base,check=True,text=True,capture_output=True,timeout=30)
(root/'effective-agent.json').write_text(config.stdout)
permissions=json.loads(config.stdout)['permission']
print('external rules:',[p for p in permissions if p.get('permission')=='external_directory'])
assert next(p for p in reversed(permissions) if p.get('permission') in ('doom_loop','*'))['action']=='allow'
command=f'printf diagnostic > {scratch}/probe.txt; if printf forbidden > {checkout}/untouched 2>/dev/null; then exit 90; fi; cat {checkout}/untouched'
probe=subprocess.run(base+['--tool','bash','--params',json.dumps({'command':command,'description':'Verify scratch write and immutable checkout without inference'})],text=True,capture_output=True,timeout=30)
(root/'tool-result.txt').write_text(probe.stdout+probe.stderr)
print('probe exit:',probe.returncode,'scratch:',(scratch/'probe.txt').read_text() if (scratch/'probe.txt').exists() else 'MISSING','checkout:',(checkout/'untouched').read_text())
print('evidence:',root)
assert probe.returncode==0 and (scratch/'probe.txt').read_text()=='diagnostic'
assert (checkout/'untouched').read_text()=='original'
