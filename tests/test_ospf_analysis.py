"""Recorded OSPF contracts and two real stock-parser import histories."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import duckdb
import pytest
import yaml
from olav_skills.ospf_analysis import _classification, _request, analyze_ospf, OSPFError

REPO=Path(__file__).resolve().parents[1]
SCRIPT=REPO/'skills/analyzer/scripts/analyze_ospf.py'
DB='.olav/databases/main.duckdb'
BASE={'snapshot_id':'old'}

def call(home,args):
    proc=subprocess.run([sys.executable,'-B',str(SCRIPT)],input=json.dumps(args),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(home)},timeout=35)
    assert proc.stdout,(proc.returncode,proc.stderr)
    return proc.returncode,json.loads(proc.stdout)

def row(state='FULL/DR',interface='Gi0/1',address='192.0.2.2',neighbor='2.2.2.2'):
    return dict(state=state,interface=interface,ip_address=address,neighbor_id=neighbor,priority='1',dead_time='00:00:33')

def parent(c,sid,device,rows,platform='cisco_ios',command=None):
    c.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',[sid,device,command or ('show ip ospf neighbor' if platform=='cisco_ios' else 'show ospf neighbor'),platform,json.dumps(rows)])

@pytest.fixture
def project(tmp_path):
    db=tmp_path/DB;db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,platform VARCHAR,parsed_data JSON)')
        parent(c,'old','I',[row(),row('2WAY/DROTHER','Gi0/2','192.0.2.3','3.3.3.3'),row('EXSTART/-','Gi0/3','192.0.2.4','4.4.4.4')])
        parent(c,'new','I',[row('LOADING/DR'),row('FULL/BDR','Gi0/4','192.0.2.5','5.5.5.5')])
        parent(c,'old','J',[row('Full','ge-0/0/1.0')],'juniper_junos')
        parent(c,'new','J',[row('Full','ge-0/0/1.0')],'juniper_junos')
    return tmp_path

def test_classification_and_unknowns(project):
    digest=hashlib.sha256((project/DB).read_bytes()).hexdigest()
    code,out=call(project,BASE)
    assert code==0 and out['summary']==dict(record_count=4,full_observed=2,two_way_unknown=1,transitional_observed=1,unknown=0),json.dumps(out,ensure_ascii=False)
    assert out['health']=='unknown' and out['source_version']['sha256']==digest==hashlib.sha256((project/DB).read_bytes()).hexdigest()
    assert out['effective_request']==dict(BASE,mode='inspect',devices=[],vendors=[],limit=20,timeout_ms=15000)
    assert all(r['health']=='unknown' and r['routing_context']['vrf'] is None for r in out['observations'])
    assert {r['identity']['platform'] for r in out['observations']}=={'cisco_ios','juniper_junos'}
    _,out=call(project,dict(BASE,devices=['J'],vendors=['juniper_junos'],limit=1))
    assert out.get('status')=='success' and out['summary']['record_count']==1 and out['summary']['full_observed']==1,json.dumps(out,ensure_ascii=False)
    _,out=call(project,dict(BASE,limit=1))
    assert out.get('status')=='success' and len(out['observations'])==1 and out['observations_more'] and out['summary']['record_count']==4,json.dumps(out,ensure_ascii=False)


def test_record_compare_not_adjacency_continuity(project):
    code,out=call(project,dict(mode='compare',snapshot_id_1='old',snapshot_id_2='new'))
    assert code==0 and out['comparison_status']=='partial' and out['comparison_scope']=='observed_records_only',json.dumps(out,ensure_ascii=False)
    assert out['counts']==dict(observed_record_added=1,observed_record_removed=2,observed_record_state_changed=1,observed_record_unchanged=1)
    assert out['routing_identity']==out['adjacency_continuity']=='unknown'
    assert {c['kind'] for c in out['changes']}=={'observed_record_added','observed_record_removed','observed_record_state_changed'}
    _,limited=call(project,dict(mode='compare',snapshot_id_1='old',snapshot_id_2='new',limit=1))
    assert limited['counts']==out['counts'] and len(limited['changes'])==1 and limited['changes_more']

@pytest.mark.parametrize('mutation',['empty','duplicate_parent','duplicate_row','bad_ip','bad_state','unsupported','missing','context'])
def test_ambiguity_cannot_compare(project,mutation):
    with duckdb.connect(str(project/DB)) as c:
        if mutation=='empty': parent(c,'old','Z',[])
        elif mutation=='duplicate_parent': parent(c,'old','I',[row()])
        elif mutation=='duplicate_row': parent(c,'old','Z',[row(),row()])
        elif mutation=='bad_ip': parent(c,'old','Z',[row(address='not-ip')])
        elif mutation=='bad_state': parent(c,'old','Z',[row(state='Established')])
        elif mutation=='unsupported': parent(c,'old','Z',[row()],platform='arista_eos')
        elif mutation=='context': parent(c,'old','Z',[dict(row(),vrf='blue')])
    req=dict(mode='compare',snapshot_id_1='missing' if mutation=='missing' else 'old',snapshot_id_2='new')
    _,out=call(project,req)
    assert out['comparison_status']=='unknown' and out['counts'] is None and not out['changes'],json.dumps(out,ensure_ascii=False)

@pytest.mark.parametrize('args',[{'snapshot_id':'latest'},{'snapshot_id':True},{'snapshot_id':'old','limit':True},{'snapshot_id':'old','devices':['I','I']},{'snapshot_id':'old','vendors':['Cisco']},{'snapshot_id':'old','vrf':'blue'},{'snapshot_id':'old','timeout_ms':15001},{'mode':'compare','snapshot_id_1':'old','snapshot_id_2':'old'}])
def test_validation(project,args):
    code,out=call(project,args)
    assert code==1 and out['error_type']=='validation_error'

def test_finite_states():
    assert _classification('FULL/ -')==('full_observed','Full','-')
    assert _classification('2-Way')[0]=='two_way_unknown'
    assert _classification('Init')[0]=='transitional_observed'
    assert _classification('Full/Unknown')[0]=='unknown'

def test_empty_missing_not_zero(project):
    _,out=call(project,{'snapshot_id':'absent'})
    assert out['summary'] is None and out['analysis_status']=='unknown' and out['observed_record_count'] is None
    _,out=call(project,dict(BASE,devices=['absent']))
    assert out['summary'] is None and 'requested_device_source_missing' in out['reasons']

def test_writer_and_caller_connection_preserved(project,monkeypatch):
    monkeypatch.setenv('OLAV_HOME',str(project))
    with duckdb.connect(str(project/DB)) as writer:
        assert analyze_ospf(json.dumps(BASE).encode())['error_type']=='source_locked'
        child=subprocess.run([sys.executable,'-c','import duckdb,sys;duckdb.connect(sys.argv[1])',str(project/DB)],capture_output=True,text=True)
        assert child.returncode and 'lock' in child.stderr.lower()
        assert writer.execute('SELECT count(*) FROM netops.parsed_outputs').fetchone()[0]==4
    conn=duckdb.connect()
    duckdb.sql('CREATE OR REPLACE TEMP TABLE ospf_caller_probe AS SELECT 7 AS n')
    assert analyze_ospf(json.dumps(BASE).encode())['status']=='success'
    assert duckdb.sql('SELECT n FROM ospf_caller_probe').fetchone()==(7,)
    assert conn.execute('SELECT 9').fetchone()==(9,)
    conn.close()

def test_deadline(project):
    code,out=call(project,dict(BASE,timeout_ms=1))
    assert code==1 and out['error_type']=='deadline_exceeded'

@pytest.mark.parametrize('vendor',['cisco_ios','juniper_junos'])
def test_native_import_two_exact_histories(tmp_path,vendor):
    from olav_skills.core.ingest.validators import _hash_bundle_contents
    home=tmp_path/'project';home.mkdir();sids=[]
    for n,state in enumerate(('FULL/DR','EXSTART/DR') if vendor=='cisco_ios' else ('Full','Init')):
        bundle=tmp_path/f'bundle{n}';shutil.copytree(REPO/'tests/fixtures/synthetic_2host_bundle',bundle)
        meta=bundle/'devices/R2/_meta.yaml';data=yaml.safe_load(meta.read_text());data['platform']=vendor;data['vendor']='Cisco' if vendor=='cisco_ios' else 'Juniper';meta.write_text(yaml.safe_dump(data))
        if vendor=='juniper_junos':
            (bundle/'devices/R2/show_version.txt').write_text('# command: show version\n# pre_scrubbed: true\n\nHostname: R2\nModel: mx204\nJunos: 23.4R1.9\n')
        command='show ip ospf neighbor' if vendor=='cisco_ios' else 'show ospf neighbor'
        body=(f'Neighbor ID     Pri   State           Dead Time   Address         Interface\n2.2.2.2           1   {state:<15} 00:00:33    192.0.2.2       GigabitEthernet0/1\n' if vendor=='cisco_ios' else
              f'Address          Interface              State     ID               Pri  Dead\n192.0.2.2        ge-0/0/1.0             {state:<9} 2.2.2.2            1    33\n')
        (bundle/'devices/R2'/f"{command.replace(' ','_')}.txt").write_text(f'# command: {command}\n# collected_at: 2026-05-15T10:23:3{n}Z\n# pre_scrubbed: true\n\n'+body)
        path=bundle/'manifest.yaml';manifest=yaml.safe_load(path.read_text());manifest['content_sha256']=_hash_bundle_contents(bundle)[0];path.write_text(yaml.safe_dump(manifest))
        run=subprocess.run([sys.executable,'-B',str(REPO/'skills/importer/scripts/ingest_snapshot.py')],input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(home)},timeout=90)
        assert run.returncode==0,(run.stdout,run.stderr)
        sids.append(json.loads(run.stdout)['snapshot_id'])
    assert sids[0]!=sids[1]
    for sid,category in zip(sids,('full_observed','transitional_observed')):
        code,out=call(home,dict(snapshot_id=sid,devices=['R2']))
        assert code==0 and out['summary'][category]==1,json.dumps(out,ensure_ascii=False)
        assert out['observations'][0]['identity']['neighbor_ip']=='192.0.2.2'
    _,out=call(home,dict(mode='compare',snapshot_id_1=sids[0],snapshot_id_2=sids[1],devices=['R2']))
    assert out['comparison_status']=='partial' and out['counts']['observed_record_state_changed']==1,json.dumps(out,ensure_ascii=False)

@pytest.mark.parametrize('payload,exitcode',[(b'[]',0),(b'{}',0),(b'{"schema":"olav.ospf-analysis/v1","status":"success"}',0),(b'{"schema":"olav.ospf-analysis/v1","status":"error","error_type":[]}',0),(b'{}',7),(b'{',0)])
def test_supervisor_rejects_invalid_child(project,monkeypatch,payload,exitcode):
    import olav_skills.ospf_analysis as module
    monkeypatch.setenv('OLAV_HOME',str(project))
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,exitcode,payload,b''))
    out=analyze_ospf(json.dumps(BASE).encode())
    assert out['error_type']=='worker_failed',json.dumps(out,ensure_ascii=False)
    expected='child_nonzero' if exitcode else 'response_json' if payload==b'{' else 'response_scope' if b'success' in payload else 'response_schema'
    assert out['diagnostic_stage']==expected,json.dumps(out,ensure_ascii=False)
    if exitcode: assert out['worker_exit_code']==exitcode


def test_payload_budget(project):
    with duckdb.connect(str(project/DB)) as c:
        parent(c,'old','Z',[dict(row(),padding='X'*262144)])
    code,out=call(project,BASE)
    assert code==1 and out['error_type']=='input_budget_exceeded'

@pytest.mark.parametrize('gate',['final_source_check','close_error'])
def test_arbitrary_worker_call_rejects_before_source_or_caller_close(project,tmp_path,gate):
    # Actual source/close failures now live in test_native_reader_bootstrap,
    # using qualified -m exec. Arbitrary -c calls must preserve caller state.
    program='''import sys,time,json,resource,duckdb
from olav_skills import ospf_analysis as o, native_snapshot as n
duckdb.sql('CREATE TEMP TABLE ospf_unqualified_caller AS SELECT 71 AS n')
before=resource.getrlimit(resource.RLIMIT_AS)
def fail(*args): raise AssertionError('unqualified call reached source/close')
n._verify_source=fail
n.close_native_reader=fail
o.main(scratch=sys.argv[1],deadline=time.monotonic()+10)
assert resource.getrlimit(resource.RLIMIT_AS)==before
assert duckdb.sql('SELECT n FROM ospf_unqualified_caller').fetchone()==(71,)
duckdb.close()
'''
    scratch=tmp_path/'reader';scratch.mkdir(mode=0o700)
    proc=subprocess.run([sys.executable,'-B','-c',program,str(scratch),gate],input=json.dumps(BASE),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(project)},timeout=20)
    assert proc.returncode==0,(proc.returncode,proc.stdout,proc.stderr)
    assert json.loads(proc.stdout)['error_type']=='native_reader_bootstrap_rejected'
    assert not list(scratch.iterdir())


@pytest.mark.parametrize('kind',['empty','full','repeated_transition'])
def test_persistence_and_cause_are_not_inferred_from_state_records(project,kind):
    values=[] if kind=='empty' else [row('FULL/DR' if kind=='full' else 'EXSTART/DR')]
    with duckdb.connect(str(project/DB)) as con:
        con.execute('DELETE FROM netops.parsed_outputs')
        parent(con,'old','I',values)
        parent(con,'new','I',values)
    for request in [dict(snapshot_id='old'),dict(mode='compare',snapshot_id_1='old',snapshot_id_2='new')]:
        code,result=call(project,request)
        assert code==0,(code,result)
        assessment=result['assessment']
        assert assessment['persistence_status']==assessment['root_cause_status']=='unknown'
        assert assessment['confirmed_stuck_count'] is None
        assert assessment['basis']=='profile_has_no_qualified_adjacency_duration'
