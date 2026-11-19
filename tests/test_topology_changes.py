"""Synthetic exact-label topology comparisons and native-source limitations."""
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

DB = '.olav/databases/main.duckdb'
BASE = {'snapshot_id_1': 'Before', 'snapshot_id_2': 'After', 'source': 'topology_links'}
SCRIPT = Path(__file__).resolve().parents[1] / 'skills/reporter/scripts/compare_topology.py'


def call(project, args):
    child = subprocess.run([sys.executable, '-B', str(SCRIPT)], input=json.dumps(args), text=True,
        capture_output=True, env={**os.environ, 'OLAV_HOME': str(project)}, timeout=30)
    assert child.stdout, (child.returncode, child.stderr)
    return child.returncode, json.loads(child.stdout)


def put(conn, sid, *, a='A', ai='Gi0/1', b='B', bi='Gi0/2', proto='LLDP', status='up', kind='L2', speed='1G'):
    conn.execute('INSERT INTO netops.topology_links VALUES (?,?,?,?,?,?,?,?,?)', [sid,a,ai,b,bi,proto,status,kind,speed])


@pytest.fixture
def project(tmp_path):
    path = tmp_path / DB
    path.parent.mkdir(parents=True)
    with duckdb.connect(str(path)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.topology_links(snapshot_id VARCHAR,source_device VARCHAR,source_interface VARCHAR,destination_device VARCHAR,destination_interface VARCHAR,discovery_protocol VARCHAR,link_status VARCHAR,link_type VARCHAR,link_speed VARCHAR)')
        put(c, 'Before')
        put(c, 'After', a='B',ai='Gi0/2',b='A',bi='Gi0/1',status='down')
        put(c, 'Before', ai='Gi0/3',bi='Gi0/4')
        put(c, 'After', ai='Gi0/3',bi='Gi0/4',proto='CDP')
        put(c, 'Before', a='C')
        put(c, 'After', a='D')
        put(c, 'before', a='excluded')
    return tmp_path


def test_interfaces_direction_parallel_and_contents(project):
    path = project / DB
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    code, out = call(project, BASE)
    assert code == 0, out
    assert out['counts'] == dict(observed_added=1, observed_removed=1, content_changed=2, unchanged=0, content_unknown=0)
    common = [r for r in out['changes'] if r['membership_status']=='common']
    assert len(common)==2
    assert {r['endpoints'][0]['interface'] for r in common}=={'Gi0/1','Gi0/3'}
    assert {r['field_status']['link_status'] for r in common}=={'changed','unchanged'}
    assert out['source_version']['sha256']==digest==hashlib.sha256(path.read_bytes()).hexdigest()
    assert out['scope']['collection_time']=='unknown'
    limited = call(project, {**BASE, 'limit':1})[1]
    assert limited['counts']==out['counts'] and limited['changes_more'] and len(limited['changes'])==1


@pytest.mark.parametrize('args', [dict(BASE,snapshot_id_1='latest'),dict(BASE,snapshot_id_2='Before'), dict(BASE,limit=True),dict(BASE,timeout_ms=15001),dict(BASE,source='x'),dict(BASE,source=[]),dict(BASE,source={}),dict(BASE,source=None),dict(BASE,source=True),dict(BASE,snapshot_id_1=[]),dict(BASE,snapshot_id_2={}),dict(BASE,limit=[]),dict(BASE,timeout_ms={})])
def test_validation(project,args):
    code,out=call(project,args)
    assert code==1 and out['error_type']=='validation_error' and out['counts'] is None


def test_empty_missing_and_collation_exact(project):
    for missing in ['missing','BEFORE']:
        code,out=call(project,dict(BASE,snapshot_id_1=missing))
        assert code==0 and out['diff_status']=='unknown' and out['counts'] is None, (code,out)
        assert out['sources'][0]['row_count']==0
    with duckdb.connect(str(project/DB)) as c:
        c.execute('DROP TABLE netops.topology_links')
    code,out=call(project,BASE)
    assert code==1 and out['error_type']=='source_table_absent' and out['counts'] is None


@pytest.mark.parametrize('changes', [dict(a=None),dict(ai='unknown'),dict(a='B',ai='Gi0/2',b='A',bi='Gi0/1'),{}])
def test_unknown_identity_and_conservative_duplicate(project,changes):
    with duckdb.connect(str(project/DB)) as c:
        put(c,'Before',**changes)
    _,out=call(project,BASE)
    assert out['diff_status']=='unknown' and out['counts'] is None and out['changes']==[]


def test_unknown_content_is_not_change_or_zero(project):
    with duckdb.connect(str(project/DB)) as c:
        c.execute('DELETE FROM netops.topology_links')
        put(c,'Before',proto=None,status=None,speed=None)
        put(c,'After')
    _,out=call(project,BASE)
    assert out['counts']['content_unknown']==1 and out['counts']['content_changed']==0
    assert out['diff_status']=='unknown'
    assert out['changes'][0]['field_status']['discovery_protocol']=='unknown'
    with duckdb.connect(str(project/DB)) as c:
        c.execute('ALTER TABLE netops.topology_links DROP COLUMN link_type')
    _,out=call(project,BASE)
    assert out['changes'][0]['field_status']['link_type']=='unknown'


def test_input_budget_is_not_partial_diff(project):
    with duckdb.connect(str(project/DB)) as c:
        put(c,'Before',ai='x'*257)
    code,out=call(project,BASE)
    assert code==1 and out['error_type']=='input_budget_exceeded' and out['counts'] is None
    code,out=call(project,dict(BASE,timeout_ms=1))
    assert code==1 and out['error_type']=='deadline_exceeded'


def test_same_device_pair_parallel_not_combined(project):
    with duckdb.connect(str(project/DB)) as c:
        c.execute('DELETE FROM netops.topology_links')
        for sid in ('Before','After'):
            put(c,sid)
            put(c,sid,ai='Gi1/1',bi='Gi1/2')
    _,out=call(project,BASE)
    assert out['counts']['unchanged']==2 and out['diff_status']=='no_observed_changes'


def test_native_import_current_labels_are_not_history(tmp_path):
    """Actual ingest retains parsed history but overwrites a shared link label."""
    from olav_skills.core.ingest.validators import _hash_bundle_contents
    repo=Path(__file__).resolve().parents[1]
    home=tmp_path/'project'
    home.mkdir()
    sids=[]
    for n in (1,2):
        bundle=tmp_path/f'bundle{n}'
        shutil.copytree(repo/'tests/fixtures/synthetic_2host_bundle',bundle)
        # Two valid detailed CDP captures, same endpoints, changed hold timer.
        capture=(
            '# command: show cdp neighbors detail\n# collected_at: 2026-05-15T10:23:30Z\n# pre_scrubbed: true\n\n'
            '-------------------------\nDevice ID: R1\nEntry address(es):\n  IP address: 10.1.12.1\n'
            'Platform: cisco WS-C3650-24PS,  Capabilities: Switch IGMP\n'
            'Interface: GigabitEthernet1,  Port ID (outgoing port): GigabitEthernet2\n'
            f'Holdtime : {140+n} sec\n\nVersion :\nCisco IOS Software [Everest], Catalyst L3 Switch Software, Version 16.6.8\n\n'
            'advertisement version: 2\nDuplex: full\n\nTotal cdp entries displayed : 1\n')
        (bundle/'devices/R2/show_cdp_neighbors_detail.txt').write_text(capture)
        manifest_path=bundle/'manifest.yaml'
        manifest=yaml.safe_load(manifest_path.read_text())
        manifest['content_sha256']=_hash_bundle_contents(bundle)[0]
        manifest_path.write_text(yaml.safe_dump(manifest))
        run=subprocess.run([sys.executable,'-B',str(repo/'skills/importer/scripts/ingest_snapshot.py')],
            input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,
            env={**os.environ,'OLAV_HOME':str(home)},timeout=90)
        assert run.returncode==0,(run.stdout,run.stderr)
        sids.append(json.loads(run.stdout)['snapshot_id'])
    assert sids[0]!=sids[1]
    with duckdb.connect(str(home/DB),read_only=True) as c:
        rows=c.execute('SELECT snapshot_id,source_device,source_interface,destination_device,destination_interface FROM netops.topology_links').fetchall()
        assert len(rows)==1 and rows[0][0]==sids[1],rows
        assert len(c.execute("SELECT DISTINCT snapshot_id FROM netops.parsed_outputs WHERE command='show cdp neighbors detail'").fetchall())==2
    code,out=call(home,dict(snapshot_id_1=sids[0],snapshot_id_2=sids[1],source='topology_links'))
    assert code==0 and out['counts'] is None and out['diff_status']=='unknown'
    assert out['sources'][0]['row_count']==0 and out['sources'][1]['row_count']==1

    code,history=call(home,dict(snapshot_id_1=sids[0],snapshot_id_2=sids[1]))
    assert code==0,history
    assert history['source']=='netops.parsed_outputs'
    assert history['counts']['observed_added']==history['counts']['observed_removed']==0
    assert history['counts']['content_changed']==0 and history['counts']['content_unknown']==1
    assert history['changes'][0]['old']['discovery_protocol']==['CDP']
    assert history['changes'][0]['new']['discovery_protocol']==['CDP']
    assert history['changes'][0]['field_status']['link_status']=='unknown'


def history(project):
    with duckdb.connect(str(project/DB)) as c:
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,platform VARCHAR,parsed_data JSON)')


def parent(c, sid, rows, device='A', command='show cdp neighbors detail', platform='cisco_ios'):
    c.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',[sid,device,command,platform,json.dumps(rows)])


def neighbor(name='B',local='Gi0/1',remote='Gi0/2'):
    return dict(neighbor_name=name,local_interface=local,neighbor_interface=remote,neighbor_port_id=remote)


def test_history_protocol_sets_parallel_and_swapped_endpoints(project):
    history(project)
    with duckdb.connect(str(project/DB)) as c:
        for sid in ('Before','After'):
            parent(c,sid,[neighbor(),neighbor(local='Gi1/1',remote='Gi1/2')])
            parent(c,sid,[neighbor(name='A',local='GigabitEthernet0/2',remote='GigabitEthernet0/1')],device='B')
            parent(c,sid,[neighbor(local='Gi1/1' if sid=='Before' else 'Gi0/1',remote='Gi1/2' if sid=='Before' else 'Gi0/2')],command='show lldp neighbors detail')
    _,out=call(project,{k:v for k,v in BASE.items() if k!='source'})
    assert out['counts']['observed_added']==out['counts']['observed_removed']==0
    assert out['counts']['content_changed']==2
    assert out['counts']['content_unknown']==2
    assert len(out['changes'])==2
    assert {row['endpoints'][0]['interface'] for row in out['changes']}=={'GigabitEthernet0/1','GigabitEthernet1/1'}
    assert all(row['field_status']['discovery_protocol']=='changed' for row in out['changes'])


@pytest.mark.parametrize('fault', ['missing','empty','invalid','duplicate_parent','duplicate_endpoint','unsupported','unknown_interface','lldp_description'])
def test_history_sources_never_become_empty_sets(project,fault):
    history(project)
    with duckdb.connect(str(project/DB)) as c:
        parent(c,'Before',[neighbor()])
        if fault=='missing':
            pass
        elif fault=='empty':
            parent(c,'After',[])
        elif fault=='invalid':
            parent(c,'After',{'not':'a list'})
        elif fault=='duplicate_parent':
            parent(c,'After',[neighbor()]);parent(c,'After',[neighbor()])
        elif fault=='duplicate_endpoint':
            parent(c,'After',[neighbor(),neighbor()])
        elif fault=='unsupported':
            parent(c,'After',[neighbor()],platform='juniper_junos')
        elif fault=='unknown_interface':
            parent(c,'After',[neighbor(remote='512')])
        elif fault=='lldp_description':
            c.execute('DELETE FROM netops.parsed_outputs')
            for sid in ('Before','After'):
                row=neighbor(remote='Ethernet1')
                row['neighbor_port_id']='512'
                parent(c,sid,[row],command='show lldp neighbors detail')
    _,out=call(project,{k:v for k,v in BASE.items() if k!='source'})
    assert out['counts'] is None and out['changes']==[] and out['diff_status']=='unknown',out


def test_python_api_does_not_release_callers_writer_lock(project,monkeypatch):
    """Calling the public supervisor must not open/close the source in this PID."""
    from olav_skills.topology_changes import compare_topology
    monkeypatch.setenv('OLAV_HOME',str(project))
    with duckdb.connect(str(project/DB)) as writer:
        out=compare_topology(json.dumps(BASE).encode())
        assert out['error_type']=='source_locked'
        child=subprocess.run([sys.executable,'-c','import duckdb,sys;duckdb.connect(sys.argv[1])',str(project/DB)],capture_output=True,text=True)
        assert child.returncode!=0 and 'lock' in child.stderr.lower()
        assert writer.execute('SELECT count(*) FROM netops.topology_links').fetchone()[0]>0


def test_configured_backend_is_rejected_before_cleanenv(project,monkeypatch):
    from olav_skills.topology_changes import compare_topology
    monkeypatch.setenv('OLAV_HOME',str(project))
    config=project/'.olav/config/ingest-storage.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"version": 999, "backend": "unavailable"}')
    out=compare_topology(json.dumps(BASE).encode())
    assert out['status']=='error' and out['error_type']=='unsupported_backend'
