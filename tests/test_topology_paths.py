"""Historical multigraph candidates; no forwarding or absence claims."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import duckdb
import pytest
import yaml
from olav_skills.topology_paths import _paths, _request

REPO=Path(__file__).resolve().parents[1]
SCRIPT=REPO/'skills/topology/scripts/inspect_path.py'
DB='.olav/databases/main.duckdb'
BASE=dict(snapshot_id='old',source_device='A',destination_device='C')

def call(home,args):
    run=subprocess.run([sys.executable,'-B',str(SCRIPT)],input=json.dumps(args),text=True,capture_output=True,
        env={**os.environ,'OLAV_HOME':str(home)},timeout=35)
    assert run.stdout,(run.returncode,run.stderr)
    return run.returncode,json.loads(run.stdout)

def parent(c,sid,device,rows):
    c.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',[sid,device,'show cdp neighbors detail','cisco_ios',json.dumps(rows)])

def row(name,local,remote):
    return dict(neighbor_name=name,local_interface=local,neighbor_interface=remote)

@pytest.fixture
def project(tmp_path):
    db=tmp_path/DB;db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,platform VARCHAR,parsed_data JSON)')
        parent(c,'old','A',[row('B','Gi0/1','Gi0/2'),row('B','Gi1/1','Gi1/2'),row('C','Gi2/1','Gi2/2')])
        parent(c,'old','B',[row('C','Gi3/1','Gi3/2')])
        parent(c,'new','A',[row('D','Gi0/1','Gi0/2')])
    return tmp_path

def identities(out):
    return {tuple((h['from']['device'],h['from']['interface'],h['to']['device'],h['to']['interface']) for h in p['hops']) for p in out['paths']}

def test_paths_parallel_reverse_filters_and_exact_scope(project):
    digest=hashlib.sha256((project/DB).read_bytes()).hexdigest()
    code,out=call(project,BASE)
    assert code==0 and out['path_count']==3 and out['search_complete'],out
    expected={
        (('A','GigabitEthernet0/1','B','GigabitEthernet0/2'),('B','GigabitEthernet3/1','C','GigabitEthernet3/2')),
        (('A','GigabitEthernet1/1','B','GigabitEthernet1/2'),('B','GigabitEthernet3/1','C','GigabitEthernet3/2')),
        (('A','GigabitEthernet2/1','C','GigabitEthernet2/2'),)}
    assert identities(out)==expected
    _,reverse=call(project,dict(BASE,source_device='C',destination_device='A'))
    assert identities(reverse)=={tuple((d,di,s,si) for s,si,d,di in reversed(path)) for path in expected}
    _,filtered=call(project,dict(BASE,source_interface='Gi1/1',destination_interface='Gi3/2'))
    assert identities(filtered)=={p for p in expected if p[0][1]=='GigabitEthernet1/1'}
    assert out['source_version']['sha256']==digest==hashlib.sha256((project/DB).read_bytes()).hexdigest()
    for sid in ['OLD','missing','new']:
        _,missing=call(project,dict(BASE,snapshot_id=sid))
        assert missing['path_status']=='unknown' and missing['path_count'] is None

def test_bounds_never_prove_absence(project):
    _,limited=call(project,dict(BASE,limit=1))
    assert len(limited['paths'])==1 and limited['paths_more'] and not limited['search_complete']
    _,hops=call(project,dict(BASE,max_hops=1))
    assert hops['path_count']==1 and hops['search']['hop_boundary_reached']
    _,none=call(project,dict(BASE,max_hops=1,source_interface='Gi0/1'))
    assert none['path_count'] is None and none['path_status']=='unknown'
    code,out=call(project,dict(BASE,timeout_ms=1))
    assert code==1 and out['error_type']=='deadline_exceeded'

@pytest.mark.parametrize('extra',[dict(snapshot_id='latest'),dict(source_device='C'),dict(source_device=[]),dict(destination_device='unknown'),dict(source_interface='512'),dict(limit=True),dict(max_hops=17),dict(timeout_ms=15001),dict(source='topology_links')])
def test_validation(project,extra):
    code,out=call(project,dict(BASE,**extra))
    assert code==1 and out['error_type']=='validation_error'

def test_python_writer_lock_preserved(project,monkeypatch):
    from olav_skills.topology_paths import inspect_path
    monkeypatch.setenv('OLAV_HOME',str(project))
    with duckdb.connect(str(project/DB)) as writer:
        out=inspect_path(json.dumps(BASE).encode())
        assert out['error_type']=='source_locked'
        child=subprocess.run([sys.executable,'-c','import duckdb,sys;duckdb.connect(sys.argv[1])',str(project/DB)],capture_output=True,text=True)
        assert child.returncode and 'lock' in child.stderr.lower()
        assert writer.execute('SELECT count(*) FROM netops.parsed_outputs').fetchone()[0]==3

def test_native_two_imports_old_sid_remains_queryable(tmp_path):
    from olav_skills.core.ingest.validators import _hash_bundle_contents
    home=tmp_path/'project';home.mkdir();sids=[]
    for n in (1,2):
        bundle=tmp_path/f'bundle{n}'
        shutil.copytree(REPO/'tests/fixtures/synthetic_2host_bundle',bundle)
        capture=('# command: show cdp neighbors detail\n# collected_at: 2026-05-15T10:23:30Z\n# pre_scrubbed: true\n\n'
            '-------------------------\nDevice ID: R1\nEntry address(es):\n  IP address: 10.1.12.1\n'
            'Platform: cisco WS-C3650-24PS,  Capabilities: Switch IGMP\n'
            'Interface: GigabitEthernet1,  Port ID (outgoing port): GigabitEthernet2\n'
            f'Holdtime : {140+n} sec\n\nVersion :\nCisco IOS Software [Everest], Catalyst L3 Switch Software, Version 16.6.8\n\n'
            'advertisement version: 2\nDuplex: full\n\nTotal cdp entries displayed : 1\n')
        (bundle/'devices/R2/show_cdp_neighbors_detail.txt').write_text(capture)
        path=bundle/'manifest.yaml';manifest=yaml.safe_load(path.read_text())
        manifest['content_sha256']=_hash_bundle_contents(bundle)[0];path.write_text(yaml.safe_dump(manifest))
        run=subprocess.run([sys.executable,'-B',str(REPO/'skills/importer/scripts/ingest_snapshot.py')],input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(home)},timeout=90)
        assert run.returncode==0,(run.stdout,run.stderr)
        sids.append(json.loads(run.stdout)['snapshot_id'])
    assert sids[0]!=sids[1]
    with duckdb.connect(str(home/DB),read_only=True) as c:
        assert c.execute('SELECT DISTINCT snapshot_id FROM netops.topology_links').fetchall()==[(sids[1],)]
    code,out=call(home,dict(snapshot_id=sids[0],source_device='R2',destination_device='R1'))
    assert code==0 and out['path_count']==1,out
    assert out['paths'][0]['hops']==[dict(**{'from':dict(device='R2',interface='GigabitEthernet1'),'to':dict(device='R1',interface='GigabitEthernet2')},discovery_protocols=['CDP'])]

def test_enumeration_limits(monkeypatch):
    import olav_skills.topology_paths as p
    links={(('A','Gi1'),('B','Gi2')):(('CDP',),None,None,None),
           (('B','Gi3'),('C','Gi4')):(('CDP',),None,None,None)}
    side=dict(links=links,reasons=set(),parents={('A','cmd')},row_count=2)
    args=_request(json.dumps(BASE).encode())
    monkeypatch.setattr(p,'MAX_STEPS',1)
    out=_paths(side,args,time.monotonic()+5)
    assert not out['search_complete'] and out['path_count'] is None
    assert 'enumeration_step_limit' in out['reasons']
    out=_paths(side,args,time.monotonic()-1)
    assert 'enumeration_deadline' in out['reasons']
    monkeypatch.setattr(p,'MAX_NODES',2)
    with pytest.raises(p.PathError,match='graph_budget_exceeded'):_paths(side,args,time.monotonic()+5)

def test_unknown_sources_keep_candidates_qualified(project):
    with duckdb.connect(str(project/DB)) as c:
        parent(c,'old','Z',[])
        parent(c,'old','unknown',[row('C','Gi4/1','Gi4/2')])
    _,out=call(project,BASE)
    assert out['path_count'] is None and not out['source_complete'] and out['paths']==[]
    assert 'empty_or_invalid_parsed_source' in out['reasons']
    assert 'unknown_device_or_unsupported_platform' in out['reasons']
    assert out['search']['steps']==0

def test_actual_graph_node_and_edge_bounds():
    from olav_skills.topology_paths import PathError
    args=_request(json.dumps(BASE).encode())
    def side(links):return dict(links=links,reasons=set(),parents=set(),row_count=len(links))
    links={(('N'+str(n),'Gi1'),('N'+str(n+1),'Gi2')):(('CDP',),None,None,None) for n in range(512)}
    with pytest.raises(PathError,match='graph_budget_exceeded'):_paths(side(links),args,time.monotonic()+5)
    links={(('A','Gi'+str(n)),('C','Gi'+str(n))):(('CDP',),None,None,None) for n in range(2049)}
    with pytest.raises(PathError,match='graph_budget_exceeded'):_paths(side(links),args,time.monotonic()+5)

def test_output_bytes_stop_is_explicit(monkeypatch):
    import olav_skills.topology_paths as p
    args=_request(json.dumps(BASE).encode())
    side=dict(links={(('A','Gi1'),('C','Gi2')):(('CDP',),None,None,None)},reasons=set(),parents=set(),row_count=1)
    monkeypatch.setattr(p,'MAX_SAMPLE_BYTES',4)
    out=_paths(side,args,time.monotonic()+5)
    assert not out['search_complete'] and out['paths_more'] and out['path_count'] is None
    assert 'path_output_limit' in out['reasons']

@pytest.mark.parametrize('fault',['duplicate_parent','duplicate_endpoint','missing_endpoint','unsupported'])
def test_ambiguous_parent_union_never_builds_candidates(project,fault):
    with duckdb.connect(str(project/DB)) as c:
        if fault=='duplicate_parent':parent(c,'old','A',[row('C','Gi5/1','Gi5/2')])
        elif fault=='duplicate_endpoint':parent(c,'old','Z',[row('C','Gi5/1','Gi5/2')]*2)
        elif fault=='missing_endpoint':parent(c,'old','Z',[row('unknown','Gi5/1','Gi5/2')])
        else:c.execute("UPDATE netops.parsed_outputs SET platform='juniper_junos' WHERE snapshot_id='old' AND device_name='B'")
    _,out=call(project,BASE)
    assert out['path_status']=='unknown' and out['paths']==[] and out['path_count'] is None
    assert not out['search_complete'] and out['search']['steps']==0

def test_self_device_observation_is_unknown(project):
    with duckdb.connect(str(project/DB)) as c:
        parent(c,'old','Q',[row('Q','Gi9/1','Gi9/2')])
    _,out=call(project,BASE)
    assert out['path_status']=='unknown' and out['path_count'] is None
    assert out['paths']==[] and 'self_device_observation' in out['reasons']
