"""Independent synthetic expectations for historical content comparisons."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from olav_skills.netops.core.diff import state_changes as m


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(tmp_path))
    path = tmp_path / '.olav/databases/main.duckdb'
    path.parent.mkdir(parents=True)
    con = duckdb.connect(str(path))
    con.execute('CREATE SCHEMA netops')
    con.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,parsed_data JSON)')
    for sid in ('before', 'after'):
        con.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?)', [sid,'r1','sentinel','[]'])
    con.execute('CREATE TABLE netops.links(snapshot_id VARCHAR,source_device VARCHAR,source_interface VARCHAR,destination_device VARCHAR,destination_interface VARCHAR,discovery_protocol VARCHAR,link_status VARCHAR)')
    con.execute('CREATE VIEW netops.v_l2_links_auto AS SELECT * FROM netops.links')
    con.close()
    return path


def put(path, sid, data, command='show ip bgp summary', device='r1'):
    with duckdb.connect(str(path)) as con:
        con.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?)', [sid,device,command,json.dumps(data)])


def peer(ip, state, **kwargs):
    return {'bgp_neighbor':ip,'state_or_prefixes_received':state, **kwargs}


def run(path, source='bgp_ios', **kwargs):
    return m.compare_state({'source':source,'snapshot_id_1':'before','snapshot_id_2':'after',**kwargs},db_path=path)


def test_changed_unchanged_add_remove_and_read_only(project):
    put(project,'before',[peer('10.0.0.1','Idle'),peer('10.0.0.2','4'),peer('10.0.0.3','Active')])
    put(project,'after',[peer('10.0.0.1','Established'),peer('10.0.0.2','7'),peer('10.0.0.4','Idle')])
    before = hashlib.sha256(project.read_bytes()).hexdigest()
    out = run(project)
    assert out['status']=='success', out
    assert out['counts']=={'added':1,'removed':1,'changed':1,'unchanged':1,'value_unknown':2}
    changed = next(x for x in out['samples'] if x['change']=='changed')
    assert (changed['bgp_neighbor'],changed['old_state'],changed['new_state'])==('10.0.0.1','Idle','Established')
    assert out['total_changes']==3 and not out['has_more']
    assert hashlib.sha256(project.read_bytes()).hexdigest()==before


def test_directed_parallel_links(project):
    with duckdb.connect(str(project)) as c:
        c.execute("INSERT INTO netops.links VALUES ('before','a','e1','b','e9','LLDP','up'),('before','a','e2','b','e8','LLDP','up'),('after','a','e1','b','e9','LLDP','down'),('after','a','e2','b','e8','LLDP','up'),('after','b','e9','a','e1','LLDP','up')")
    out = run(project,'l2')
    assert out['counts']=={'added':1,'removed':0,'changed':1,'unchanged':1,'value_unknown':1}
    assert next(x for x in out['samples'] if x['change']=='changed')['source_interface']=='e1'


@pytest.mark.parametrize('rows', [[peer('1','Idle'),peer('1','Active')],[peer('1','Idle'),peer('1','Idle')]])
def test_duplicate_identity_refused(project,rows):
    put(project,'before',rows)
    put(project,'after',[peer('1','Active')])
    out=run(project)
    assert out['error_type']=='ambiguous_identity' and out['counts'] is None


def test_vrf_afi_scope_and_null(project):
    put(project,'before',[peer('1','Idle',vrf='blue',afi='ipv4'),peer('1',None,vrf='red',afi='ipv6')])
    put(project,'after',[peer('1','Idle',vrf='blue',afi='ipv4'),peer('1','Active',vrf='red',afi='ipv6')])
    out=run(project)
    assert out['counts']['changed']==1 and out['counts']['value_unknown']==1
    assert out['samples'][0]['vrf']=='red' and out['samples'][0]['old_state'] is None
    assert out['samples'][0]['certainty']=='unknown'


def test_null_equal_unknown(project):
    put(project,'before',[peer('1',None)])
    put(project,'after',[peer('1',None)])
    out=run(project)
    assert out['diff_status']=='unknown' and out['counts']['unchanged']==1


def test_missing_command_not_empty(project):
    put(project,'before',[peer('1','Idle')])
    assert run(project)['error_type']=='source_incomplete'
    put(project,'after',[])
    assert run(project)['counts']['removed']==1


@pytest.mark.parametrize('data', [[{'bgp_neighbor':'1'}],[peer(None,'Idle')],[peer('1',{'bad':1})],{'nested':[]}])
def test_schema_or_identity_drift(project,data):
    put(project,'before',data)
    put(project,'after',[peer('1','Idle')])
    assert run(project)['error_type'] in ('unsupported_schema','unknown_identity')


def test_view_and_column_missing(project):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_l2_links_auto')
    assert run(project,'l2')['error_type']=='source_missing'
    with duckdb.connect(str(project)) as c:
        c.execute('CREATE VIEW netops.v_l2_links_auto AS SELECT snapshot_id FROM netops.links')
    assert run(project,'l2')['error_type']=='unsupported_schema'


def test_missing_or_latest_sid_and_routes(project):
    assert run(project,snapshot_id_1='absent')['error_type']=='snapshot_missing'
    assert run(project,snapshot_id_1='latest')['error_type']=='validation_error'
    assert m.compare_state({'source':'bgp_ios'},db_path=project)['error_type']=='validation_error'
    assert run(project,'routes')['error_type']=='unsupported_source'
    assert run(project,'l2')['counts']['added']==0
    assert run(project,'l2')['scope']['coverage']=='unknown'


def test_interfaces_filters_sample_and_clipping(project):
    cmd='show ip interface brief'
    old=[{'interface':str(i),'status':'up','proto':'up','ip_address':'1.1.1.1'} for i in range(5)]
    new=[{**x,'ip_address':'Z'*1000} for x in old]
    put(project,'before',old,cmd)
    put(project,'after',new,cmd)
    put(project,'before',old,cmd,'r2')
    put(project,'after',old,cmd,'r2')
    out=run(project,'interfaces_ios',device='r1',limit=2)
    assert out['counts']['changed']==5 and out['sample_count']==2 and out['has_more']
    assert out['samples'][0]['cell_truncated'] and len(out['samples'][0]['new_ip_address'])==256
    assert out['scope']['filters']=={'device':'r1'}
    assert run(project,'interfaces_ios',device="r1' OR 1=1--")['error_type']=='source_incomplete'


def test_byte_truncation_and_budgets(project,monkeypatch):
    put(project,'before',[peer(str(i),'Idle') for i in range(6)])
    put(project,'after',[peer(str(i),'Active') for i in range(6)])
    monkeypatch.setattr(m,'SAMPLE_BYTES',500)
    out=run(project)
    assert out['byte_truncated'] and out['has_more'] and out['counts']['changed']==6
    timeout=m.compare_state({'source':'bgp_ios','snapshot_id_1':'before','snapshot_id_2':'after'},db_path=project,timeout_ms=0)
    assert timeout['error_type']=='timeout' and timeout['counts'] is None
    monkeypatch.setattr(m,'MAX_BYTES',100)
    assert run(project)['error_type']=='budget_exceeded'


def test_junos_fields(project):
    cmd='show interfaces terse'
    old={'interface':'ge-0/0/0','link_state':'up','proto':'up','ip_address':None}
    put(project,'before',[old],cmd)
    put(project,'after',[{**old,'link_state':'down'}],cmd)
    out=run(project,'interfaces_junos')
    assert out['samples'][0]['old_status']=='up' and out['samples'][0]['new_status']=='down'
    assert out['samples'][0]['new_ip_address'] is None
    assert out['samples'][0]['old_proto']=='up'
    assert out['samples'][0]['old_admin_state'] is None
    assert out['samples'][0]['certainty']=='unknown'


def test_junos_bgp_profiles_and_unknown_native_state(project):
    command = 'show bgp summary'
    put(project, 'before', [peer('192.0.2.1', 4), peer('192.0.2.2', 'Idle')], command)
    put(project, 'after', [{'peer_ip': '192.0.2.1', 'state': 'establ'},
                           {'peer_ip': '192.0.2.2', 'state': '4'}], command)
    out = run(project, 'bgp_junos')
    assert out['counts'] == {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 1, 'value_unknown': 1}
    changed, = out['samples']
    assert (changed['bgp_neighbor'], changed['new_state'], changed['new_raw_state']) == ('192.0.2.2', None, '4')
    assert changed['vrf'] is None and changed['afi'] is None


def test_junos_bgp_ambiguous_profiles_refused_even_if_one_has_wrong_type(project):
    command = 'show bgp summary'
    put(project, 'before', [peer('192.0.2.1', 'Idle')], command)
    put(project, 'after', [{**peer('192.0.2.1', 'Idle'), 'peer_ip': '192.0.2.1', 'state': {'hidden': True}}], command)
    out = run(project, 'bgp_junos')
    assert out['error_type'] == 'unsupported_schema' and out['counts'] is None


def test_ios_canonical_aliases_and_unknowns_do_not_create_state_changes(project):
    command = 'show ip interface brief'
    old = {'interface': 'GigabitEthernet1', 'status': 'administratively down', 'proto': '?', 'ip_address': 'unassigned'}
    put(project, 'before', [old], command)
    put(project, 'after', [{**old, 'status': 'admin-down', 'proto': 'unsupported'}], command)
    out = run(project, 'interfaces_ios')
    assert out['diff_status'] == 'unknown'
    assert out['counts'] == {'added': 0, 'removed': 0, 'changed': 0, 'unchanged': 1, 'value_unknown': 1}


def test_exact_compare_scope_with_nocase_source_columns(project):
    with duckdb.connect(str(project)) as con:
        con.execute('DROP TABLE netops.parsed_outputs')
        con.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR COLLATE NOCASE,device_name VARCHAR COLLATE NOCASE,command VARCHAR COLLATE NOCASE,parsed_data JSON)')
    put(project, 'before', [peer('192.0.2.1', 'Idle')])
    put(project, 'Before', [peer('192.0.2.2', 'Active')])
    put(project, 'after', [peer('192.0.2.1', 'Established')])
    put(project, 'after', [peer('192.0.2.2', 'Active')], device='R1')
    put(project, 'after', [peer('192.0.2.3', 'Active')], command='SHOW IP BGP SUMMARY')
    out = run(project, device='r1')
    assert out['counts'] == {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 0, 'value_unknown': 0}
    assert run(project, snapshot_id_1='BEFORE', device='r1')['error_type'] == 'snapshot_missing'


def test_real_stdin_dispatcher(project):
    put(project,'before',[peer('1','Idle')])
    put(project,'after',[peer('1','Active')])
    env={**os.environ,'OLAV_HOME':str(project.parents[2])}
    child=subprocess.run([sys.executable,'-m','olav_skills','run','reporter','compare_state'],input=json.dumps({'source':'bgp_ios','snapshot_id_1':'before','snapshot_id_2':'after'}),text=True,capture_output=True,env=env)
    assert child.returncode==0, (child.stdout,child.stderr)
    out=json.loads(child.stdout)
    assert out['counts']['changed']==1
    child=subprocess.run([sys.executable,'-m','olav_skills','run','reporter','compare_state'],input='x'*17000,text=True,capture_output=True,env=env)
    assert child.returncode!=0 and json.loads(child.stdout)['error_type']=='validation_error'


def test_l2_observed_set_can_be_empty_without_claiming_coverage(project):
    with duckdb.connect(str(project)) as c:
        c.execute("INSERT INTO netops.links VALUES ('before','a','e1','b','e2','LLDP','up')")
    out=run(project,'l2')
    assert out['status']=='success' and out['counts']['removed']==1
    assert out['scope']['coverage']=='unknown'
    assert out['samples'][0]['change']=='removed'


def test_malformed_json_is_error_not_empty(project):
    with duckdb.connect(str(project)) as c:
        c.execute('ALTER TABLE netops.parsed_outputs ALTER COLUMN parsed_data TYPE VARCHAR')
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('before','r1','show ip bgp summary','{bad')")
    put(project,'after',[peer('1','Idle')])
    assert run(project)['error_type']=='unsupported_schema'


def test_large_exact_counts_small_sample(project):
    put(project,'before',[peer(str(i),'Idle') for i in range(2000)])
    put(project,'after',[peer(str(i),'Active') for i in range(2000)])
    out=run(project,limit=1)
    assert out['counts']['changed']==2000 and len(out['samples'])==1
    assert out['has_more']


def test_memory_budget_and_invalid_unicode(project,monkeypatch):
    put(project,'before',[peer('1','Idle')])
    put(project,'after',[peer('1','Active')])
    monkeypatch.setenv('OLAV_SQL_MEMORY_LIMIT','1MB')
    assert run(project)['error_type']=='budget_exceeded'
    assert run(project,device='\ud800')['error_type']=='validation_error'
