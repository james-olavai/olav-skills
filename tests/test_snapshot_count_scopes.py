"""Real batch counts distinguish current catalog from selected parsed records."""
import hashlib
import json
import os
import subprocess
import sys

import duckdb
import pytest


@pytest.fixture
def project(tmp_path):
    db=tmp_path/'.olav/databases/main.duckdb';db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.devices(hostname VARCHAR, vendor VARCHAR)')
        c.executemany('INSERT INTO netops.devices VALUES (?,?)',
                      [('late-a','Cisco'),('late-b','Juniper'),('retired','Cisco')])
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR, device_name VARCHAR, command VARCHAR)')
        c.executemany('INSERT INTO netops.parsed_outputs VALUES (?,?,?)',[
            ('Before','retired','show version'),('After','late-a','show version'),
            ('After','late-a','show interfaces'),('After','late-b','show version'),
            ('After','uncatalogued','show version')])
    return tmp_path


@pytest.mark.parametrize('filters,expected',[
    ({},(3,2,2,3)),
    ({'commands':['show interfaces']},(3,1,1,1)),
    ({'devices':['late-a']},(1,1,2,2)),
    ({'vendors':['Juniper']},(1,1,1,1)),
])
def test_installed_summary_has_distinct_scopes_without_changing_counts(project,filters,expected):
    env={k:os.environ[k] for k in ('PATH','PYTHONPATH','TMPDIR') if k in os.environ}
    env['OLAV_HOME']=str(project)
    db=project/'.olav/databases/main.duckdb';before=hashlib.sha256(db.read_bytes()).hexdigest()
    request={'operation':'snapshot_summary','snapshot_id':'After',**filters}
    p=subprocess.run([sys.executable,'-B','-m','olav_skills','run','analyzer','query_batch'],
                     input=json.dumps(request),text=True,capture_output=True,cwd=project,env=env,timeout=30)
    assert p.returncode==0,(p.stdout,p.stderr)
    out=json.loads(p.stdout)
    assert tuple(out['counts'][k] for k in ('inventory_device_count','observed_device_count','command_count','record_count'))==expected
    inventory=out['result_contract']['counts']['inventory_device_count']
    observed=out['result_contract']['counts']['observed_device_count']
    assert inventory['scope']=='filtered_current_project_inventory'
    assert inventory['snapshot_id'] is None and inventory['snapshot_applies'] is False
    assert inventory['physical_sources']==['netops.devices']
    assert inventory['applicable_filters']==['devices','vendors']
    assert observed['scope']=='selected_snapshot_parsed_devices_in_filtered_inventory'
    assert observed['snapshot_id']=='After' and observed['snapshot_applies'] is True
    assert observed['physical_sources']==['netops.devices','netops.parsed_outputs']
    assert observed['applicable_filters']==['devices','vendors','commands']
    # The uncatalogued parsed device is deliberately outside this query's join.
    # Metadata must describe that real limit rather than claim all raw members.
    assert observed['count']==expected[1] and out['coverage']['inventory_is_snapshot_membership'] is False
    assert 'mixed_current_inventory_and_selected_snapshot_parsed_counts' in out['result_contract']['semantics']
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
