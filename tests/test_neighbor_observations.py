"""Pure history extractor, independent canonical endpoint expectations."""
import json
import duckdb
import pytest
from olav_skills.neighbor_observations import read_history, NeighborError

def database():
    c=duckdb.connect(':memory:')
    c.execute('CREATE SCHEMA netops')
    c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,platform VARCHAR,parsed_data JSON)')
    return c

def parent(c,sid,rows,device='A',command='show cdp neighbors detail',platform='cisco_ios'):
    c.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',[sid,device,command,platform,json.dumps(rows)])

def row(name='B',local='Gi0/1',remote='Gi0/2'):
    return dict(neighbor_name=name,local_interface=local,neighbor_interface=remote,neighbor_port_id=remote)

def test_single_and_dual_preserve_protocol_and_parallel():
    with database() as c:
        parent(c,'old',[row(),row(local='Gi1/1',remote='Gi1/2')])
        parent(c,'old',[row(name='A',local='Gi0/2',remote='Gi0/1')],device='B',command='show lldp neighbors detail')
        parent(c,'new',[row(name='C')])
        sides=read_history(c,['old','new'])
        assert sides['old']['links']=={
            (('A','GigabitEthernet0/1'),('B','GigabitEthernet0/2')):(('CDP','LLDP'),None,None,None),
            (('A','GigabitEthernet1/1'),('B','GigabitEthernet1/2')):(('CDP',),None,None,None)}
        assert sides['old']['reasons']==set()
        assert read_history(c,['old'])['old']==sides['old']
        assert read_history(c,['OLD'])['OLD']['reasons']=={'snapshot_source_missing','empty_or_uncollected_snapshot_source'}
        assert 'counterpart_parent_scope_missing' not in sides['new']['reasons']

@pytest.mark.parametrize('fault',['empty','duplicate','duplicate_parent','bad_port','unsupported','invalid'])
def test_unknown_source_reasons(fault):
    with database() as c:
        rows=[row()]
        if fault=='empty':rows=[]
        if fault=='duplicate':rows*=2
        if fault=='bad_port':rows=[row(remote='512')]
        if fault=='invalid':rows={'not':'list'}
        parent(c,'old',rows,platform='junos' if fault=='unsupported' else 'cisco_ios')
        if fault=='duplicate_parent':parent(c,'old',rows)
        assert read_history(c,['old'])['old']['reasons']

def test_combined_budget_and_schema():
    with database() as c:
        parent(c,'old',[row()]*4097)
        with pytest.raises(NeighborError,match='input_budget_exceeded'):read_history(c,['old'])
    with database() as c:
        c.execute('ALTER TABLE netops.parsed_outputs ALTER COLUMN parsed_data TYPE VARCHAR')
        with pytest.raises(NeighborError,match='unsupported_schema'):read_history(c,['old'])
