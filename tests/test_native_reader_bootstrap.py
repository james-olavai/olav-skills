"""Actual dedicated -m reader initialization, cleanup and caller refusal gates."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import duckdb
import pytest

MODULES=['olav_skills.ospf_analysis','olav_skills.evidence_readiness']
DB='.olav/databases/main.duckdb'

@pytest.fixture
def project(tmp_path):
    home=tmp_path/'project';db=home/DB;db.parent.mkdir(parents=True)
    raw='recorded synthetic command\n';digest=hashlib.sha256(raw.encode()).hexdigest()
    clock={'value':'2026-05-15T10:23:30+00:00','precision':'timestamp','status':'known','source':'command_header'}
    source={'device_name':'R1','command':'show ip ospf neighbor','actual_raw_sha256':digest,
        'raw_artifact':{'table':'netops.ingest_sources','snapshot_id':'capture','device_name':'R1','command':'show ip ospf neighbor'},
        'collected_at':clock,'capture_evidence':[clock,dict(clock,source='device_meta'),dict(clock,source='manifest')]}
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,platform VARCHAR,parsed_data JSON)')
        c.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',['capture','R1','show ip ospf neighbor','cisco_ios',json.dumps([dict(neighbor_id='2.2.2.2',ip_address='192.0.2.2',interface='Gi0/1',state='FULL/DR',priority='1',dead_time='00:00:33')])])
        c.execute('CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,raw_output VARCHAR,source_json VARCHAR)')
        c.execute('INSERT INTO netops.ingest_sources VALUES (?,?,?,?,?)',['capture','R1','show ip ospf neighbor',raw,json.dumps(source)])
    return home

def args(module):
    return {'snapshot_id':'capture'} if module.endswith('ospf_analysis') else {'snapshot_id':'capture','requirements':[{'device':'R1','command':'show ip ospf neighbor'}],'as_of':'2026-05-15T10:24:00Z','max_age_seconds':60}

def environment(home,hook=None):
    env={k:os.environ[k] for k in ('PATH','PYTHONPATH','TMPDIR') if k in os.environ}
    env.update(OLAV_HOME=str(home),OPENBLAS_NUM_THREADS='1',PYTHONIOENCODING='utf-8')
    paths=[str(Path(p or '.').resolve()) for p in env.get('PYTHONPATH','').split(os.pathsep)] if 'PYTHONPATH' in env else []
    if hook is not None: paths.insert(0,str(hook))
    if paths: env['PYTHONPATH']=os.pathsep.join(paths)
    return env

def run_worker(home,tmp_path,module,hook_text=None):
    work=tmp_path/'foreign';work.mkdir()
    scratch=tmp_path/'reader';scratch.mkdir(mode=0o700)
    hook=tmp_path/'hook'
    if hook_text is not None:
        hook.mkdir();(hook/'sitecustomize.py').write_text(hook_text)
    return subprocess.run([sys.executable,'-B','-P','-s','-m',module,'--worker',str(scratch),str(time.monotonic()+10)],input=json.dumps(args(module)),text=True,capture_output=True,cwd=work,env=environment(home,hook if hook_text is not None else None),timeout=15)

INSTRUMENT='''import json,sys,resource
from pathlib import Path
from olav_skills import native_reader_bootstrap as b, native_snapshot as n
original=b.bootstrap_native_reader
checks=[0]
verify=n._verify_source
def source_verify(*args):
 checks[0]+=1
 return verify(*args)
n._verify_source=source_verify
def bootstrap(module):
 public=original(module)
 assert original(module) is public
 conn=public.default_connection()
 print('QA_META '+json.dumps({'stage':'bootstrap','threads':len(list(Path('/proc/self/task').iterdir())),'settings':conn.execute("SELECT current_setting('threads'),current_setting('memory_limit'),current_setting('enable_external_access'),current_setting('lock_configuration')").fetchone(),'limit':resource.getrlimit(resource.RLIMIT_AS)[0]}),file=sys.stderr)
 return public
b.bootstrap_native_reader=bootstrap
close=n.close_native_reader
def closing():
 close()
 print('QA_META '+json.dumps({'stage':'closed','threads':len(list(Path('/proc/self/task').iterdir())),'source_checks':checks[0]}),file=sys.stderr)
n.close_native_reader=closing
'''

@pytest.mark.parametrize('module',MODULES)
def test_actual_exec_bootstrap_identity_settings_and_normal_close(project,tmp_path,module):
    proc=run_worker(project,tmp_path,module,INSTRUMENT)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
    out=json.loads(proc.stdout)
    assert out['status']=='success',json.dumps(out)
    if module.endswith('ospf_analysis'): assert out['summary']['full_observed']==1
    else: assert out['readiness']=='ready'
    meta=[json.loads(line[8:]) for line in proc.stderr.splitlines() if line.startswith('QA_META ')]
    assert meta[0]['stage']=='bootstrap' and meta[0]['threads']==1 and meta[0]['settings']==[1,'128.0 MiB',False,True],meta
    assert 0<meta[0]['limit']<=768*1024*1024
    assert meta[-1]=={'stage':'closed','threads':1,'source_checks':3},meta
    assert not list((tmp_path/'reader').iterdir()) and list((tmp_path/'foreign').iterdir())==[]

@pytest.mark.parametrize('module',MODULES)
@pytest.mark.parametrize('preload',['duckdb','_duckdb'])
def test_actual_exec_rejects_preloaded_modules(project,tmp_path,module,preload):
    hook=f'''import importlib,atexit
m=importlib.import_module({preload!r})
if {preload!r}=='duckdb':
 m.sql('CREATE TEMP TABLE caller_bootstrap_probe AS SELECT 41 AS n')
 def cleanup():
  assert m.sql('SELECT n FROM caller_bootstrap_probe').fetchone()==(41,)
  m.close()
 atexit.register(cleanup)
'''
    proc=run_worker(project,tmp_path,module,hook)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
    assert json.loads(proc.stdout)['error_type']=='native_reader_bootstrap_rejected'
    assert 'Exception ignored' not in proc.stderr

@pytest.mark.parametrize('module',MODULES)
def test_runpy_and_forged_argv_preserve_caller_resource_default_and_writer(project,tmp_path,module):
    program='''import json,os,resource,runpy,sys,duckdb
from pathlib import Path
module=sys.argv[1]
home=Path(os.environ['OLAV_HOME'])
scratch=sys.argv[2]
writer=duckdb.connect(str(home/".olav/databases/main.duckdb"))
duckdb.sql('CREATE TEMP TABLE caller_bootstrap_probe AS SELECT 43 AS n')
before=resource.getrlimit(resource.RLIMIT_AS)
sys.argv=[module,'--worker',scratch,sys.argv[3]]
sys.orig_argv=[sys.executable,'-B','-P','-s','-m',module,*sys.argv[1:]]
try: runpy.run_module(module,run_name='__main__',alter_sys=True)
except SystemExit as exc: assert exc.code==0
assert resource.getrlimit(resource.RLIMIT_AS)==before
assert duckdb.sql('SELECT n FROM caller_bootstrap_probe').fetchone()==(43,)
assert writer.execute('SELECT count(*) FROM netops.parsed_outputs').fetchone()==(1,)
writer.close();duckdb.close()
'''
    scratch=tmp_path/'reader';scratch.mkdir(mode=0o700)
    proc=subprocess.run([sys.executable,'-B','-c',program,module,str(scratch),str(time.monotonic()+10)],input=json.dumps(args(module)),text=True,capture_output=True,cwd=tmp_path,env=environment(project),timeout=15)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
    assert json.loads(proc.stdout)['error_type']=='native_reader_bootstrap_rejected'

@pytest.mark.parametrize('module',MODULES)
@pytest.mark.parametrize('fault',['source_check','close'])
def test_actual_exec_final_source_or_close_failure_never_success(project,tmp_path,module,fault):
    hook='''from olav_skills import native_snapshot as n
'''
    if fault=='close':
        hook+='''def fail(): raise RuntimeError('injected-close-failure')
n.close_native_reader=fail
'''
    else:
        hook+='''original=n._verify_source
count=[0]
def verify(*args):
 count[0]+=1
 if count[0]==3: raise n.NativeSnapshotError('source_changed','injected final source failure')
 return original(*args)
n._verify_source=verify
'''
    proc=run_worker(project,tmp_path,module,hook)
    if fault=='close': assert proc.returncode and not proc.stdout
    else:
        assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
        assert json.loads(proc.stdout)['error_type']=='source_changed'

@pytest.mark.parametrize('module',MODULES)
def test_public_api_does_not_load_duckdb_in_caller(project,tmp_path,module):
    program='''import importlib,json,sys
m=importlib.import_module(sys.argv[1])
assert 'duckdb' not in sys.modules and '_duckdb' not in sys.modules
raw=json.loads(sys.argv[2])
out=m.analyze_ospf(json.dumps(raw).encode()) if sys.argv[1].endswith('ospf_analysis') else m.check_evidence(raw)
assert out['status']=='success',out
assert 'duckdb' not in sys.modules and '_duckdb' not in sys.modules
print(json.dumps({'status':'success'}))
'''
    proc=subprocess.run([sys.executable,'-B','-c',program,module,json.dumps(args(module))],text=True,capture_output=True,cwd=tmp_path,env=environment(project),timeout=20)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)

@pytest.mark.parametrize('fault',['missing_api','wrong_version','effective_settings'])
def test_actual_exec_api_version_setting_failures_are_safe_and_close_owned_connection(project,tmp_path,fault):
    hook='''import atexit,importlib,sys
from types import SimpleNamespace
from olav_skills import native_reader_bootstrap as b
'''
    if fault in ('missing_api','wrong_version'):
        hook+='''load=importlib.import_module
def importing(name,*args,**kwargs):
 low=load(name,*args,**kwargs)
 if name=='_duckdb':
'''
        if fault=='missing_api': hook+="  return SimpleNamespace(connect=low.connect,__version__=low.__version__)\n"
        else: hook+="  return SimpleNamespace(connect=low.connect,set_default_connection=low.set_default_connection,default_connection=low.default_connection,__version__='2.0.0')\n"
        hook+=''' return low
b.importlib.import_module=importing
'''
    else:
        hook+='''load=importlib.import_module
connections=[]
def importing(name,*args,**kwargs):
 low=load(name,*args,**kwargs)
 if name=='_duckdb':
  connect=low.connect
  def connecting(*args,**kwargs):
   conn=connect(*args,**kwargs);connections.append(conn);return conn
  low.connect=connecting
 return low
b.importlib.import_module=importing
settings=b._settings
def mismatch(conn):
 settings(conn)
 b._reject()
b._settings=mismatch
def verify_closed():
 assert len(connections)==1
 try: connections[0].execute('SELECT 1')
 except Exception: print('QA_CLOSED true',file=sys.stderr)
 else: raise AssertionError('owned connection was not closed')
atexit.register(verify_closed)
'''
    proc=run_worker(project,tmp_path,MODULES[0],hook)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
    assert json.loads(proc.stdout)['error_type']=='native_reader_bootstrap_rejected'
    assert 'Exception ignored' not in proc.stderr
    if fault=='effective_settings': assert 'QA_CLOSED true' in proc.stderr
