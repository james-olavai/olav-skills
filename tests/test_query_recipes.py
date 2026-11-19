"""Native recorded recipe truth sets, with explicit unknown/missing sources."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import duckdb
import pytest
from olav_skills import query_recipes as m

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv('OLAV_HOME',str(tmp_path))
    p=tmp_path/'.olav/databases/main.duckdb';p.parent.mkdir(parents=True)
    with duckdb.connect(str(p)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.devices(hostname VARCHAR,ip_address VARCHAR,platform VARCHAR,vendor VARCHAR,model VARCHAR,role VARCHAR)')
        c.execute("INSERT INTO netops.devices VALUES ('r1','1','ios','Cisco',NULL,NULL),('r2','2','junos','Juniper',NULL,NULL),('uncaptured','3','ios','Cisco',NULL,NULL)")
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR)')
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('s1','r1','show version'),('s1','r2','show version'),('s2','uncaptured','show version')")
        c.execute('CREATE TABLE netops.if_records(device_name VARCHAR,snapshot_id VARCHAR,interface VARCHAR,ip_address VARCHAR,status VARCHAR,proto VARCHAR)')
        c.execute("INSERT INTO netops.if_records VALUES ('r1','s1','e1','1','up','up'),('r1','s1','e2',NULL,'down','down'),('r2','s1','e3',NULL,NULL,'up'),('r2','s2','e4',NULL,'down','down')")
        c.execute('CREATE VIEW netops.v_show_ip_interface_brief_auto AS SELECT * FROM netops.if_records')
        c.execute('CREATE TABLE netops.bgp_records(device_name VARCHAR,snapshot_id VARCHAR,bgp_neighbor VARCHAR,state_or_prefixes_received VARCHAR)')
        c.execute("INSERT INTO netops.bgp_records VALUES ('r1','s1','1','0'),('r1','s1','2','Idle'),('r2','s1','3',NULL),('r2','s1','4','mystery')")
        c.execute('CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT * FROM netops.bgp_records')
    return p

def run(recipe='inventory',**kwargs):
    return m.run_investigation({'recipe':recipe,'snapshot_id':'s1',**kwargs})


def run_inside_worker(recipe='inventory', **kwargs):
    """Use the private read function for fault injection in this test process."""
    with TemporaryDirectory(prefix='olav-investigation-test-') as scratch:
        return m._run_investigation({'recipe': recipe, 'snapshot_id': 's1', **kwargs},
                                    Path(scratch), time.monotonic() + m.TIMEOUT_SECONDS)

def test_inventory_truth_membership_time_and_hash(project):
    before=hashlib.sha256(project.read_bytes()).hexdigest();out=run()
    assert out['status']=='success',out
    assert out['steps'][0]['counts']=={'inventory_device_count':3,'observed_device_count':2,'command_count':1,'record_count':2}
    assert {r['hostname'] for r in out['steps'][1]['rows']}=={'r1','r2'}
    assert out['temporal']['collected_at']['status']=='unknown'
    assert out['collection_coverage']=='unknown' and out['live_state']=='unknown'
    assert out['source_version']['database_sha256']==before==hashlib.sha256(project.read_bytes()).hexdigest()

def test_interface_unknown_is_not_nonhealthy(project):
    out=run('interfaces');assert out['status']=='success',out
    step=out['steps'][0]
    assert step['counts']=={'record_count':3,'healthy_record_count':1,'nonhealthy_record_count':1,'unknown_record_count':1}
    assert {r['interface']:r['classification'] for r in step['rows']}=={'e2':'nonhealthy','e3':'unknown'}
    assert step['sources'][1]['status']=='absent'

def test_bgp_numeric_zero_and_unknown(project):
    step=run('bgp')['steps'][0]
    assert step['counts']=={'record_count':4,'healthy_record_count':1,'nonhealthy_record_count':1,'unknown_record_count':2}
    assert {r['bgp_neighbor'] for r in step['rows']}=={'2','3','4'}

@pytest.mark.parametrize('recipe',['inventory','interfaces','bgp'])
def test_filters_are_bound_and_vendor_scoped(project,recipe):
    result=run(recipe,devices=["r1' OR 1=1--"])
    assert result['status']=='success',result
    step=result['steps'][-1]
    assert step.get('total_rows',step.get('counts',{}).get('record_count'))==0
    result=run(recipe,vendors=['Cisco']);assert result['status']=='success',result
    if recipe!='inventory':assert result['steps'][0]['counts']['record_count']==2
    else:assert {r['hostname'] for r in result['steps'][1]['rows']}=={'r1'}

def test_partial_samples_keep_counts(project):
    step=run('bgp',limit=1)['steps'][0]
    assert step['has_more'] and len(step['rows'])==1 and step['counts']['record_count']==4

@pytest.mark.parametrize('change',[{'snapshot_id':'latest'},{'sql':'select 1'},{'recipe':'unregistered'},{'limit':True},{'devices':'r1'},{'snapshot_id':'missing'}])
def test_reject_invalid_and_missing_scope(project,change):
    assert run(**change)['status']=='error'

def test_missing_protocol_no_zero_health(project):
    with duckdb.connect(str(project)) as c:c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
    result=run('bgp');assert result['status']=='success',result
    assert result['steps'][0]['status']=='unknown' and result['steps'][0]['counts'] is None

def test_bad_schema_and_external_view_refused(project,tmp_path):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        c.execute('CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT snapshot_id FROM netops.bgp_records')
    assert run('bgp')['status']=='error'
    f=tmp_path/'ext.csv';f.write_text('device_name,snapshot_id,bgp_neighbor,state_or_prefixes_received\nr1,s1,1,Idle\n')
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        c.execute("CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT * FROM read_csv_auto('" + str(f).replace("'", "''") + "')")
    assert run('bgp')['status']=='error'

def test_path_and_budgets_fail_closed(project,monkeypatch,tmp_path):
    monkeypatch.setattr(m,'MAX_DATABASE_BYTES',1);assert run_inside_worker()['status']=='error'
    monkeypatch.setattr(m,'MAX_DATABASE_BYTES',256*1024*1024)
    monkeypatch.setattr(m,'MAX_OUTPUT_BYTES',20);assert run_inside_worker()['error_type']=='budget_exceeded'
    monkeypatch.setattr(m,'MAX_OUTPUT_BYTES',32768)
    monkeypatch.setattr(m,'TIMEOUT_SECONDS',0);assert run()['status']=='error'
    monkeypatch.setattr(m,'TIMEOUT_SECONDS',5)
    link=tmp_path/'alias';link.symlink_to(project.parents[2],target_is_directory=True)
    monkeypatch.setenv('OLAV_HOME',str(link));assert run()['status']=='error'

def test_stdin_dispatcher(project):
    child=subprocess.run([sys.executable,'-m','olav_skills','run','analyzer','run_investigation'],input=json.dumps({'recipe':'bgp','snapshot_id':'s1'}),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(project.parents[2])})
    assert child.returncode==0,(child.stdout,child.stderr)
    assert json.loads(child.stdout)['steps'][0]['counts']['unknown_record_count']==2


def test_source_version_change_withholds_results(project,monkeypatch):
    original=m._inventory
    def changed(q,args):
        result=original(q,args)
        stat=project.stat()
        os.utime(project,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
        return result
    monkeypatch.setattr(m,'_inventory',changed)
    result=run_inside_worker()
    assert result['status']=='error' and result['findings'] is None
    assert result['error_type']=='source_changed'


def test_wal_and_missing_binding(project,monkeypatch):
    wal=Path(str(project)+'.wal');wal.write_bytes(b'uncheckpointed')
    assert run()['status']=='error'
    wal.unlink();monkeypatch.delenv('OLAV_HOME')
    assert run()['status']=='error'


def test_stdin_duplicate_scope_is_rejected(project):
    child=subprocess.run([sys.executable,'-m','olav_skills','run','analyzer','run_investigation'],input='{"recipe":"bgp","snapshot_id":"s1","snapshot_id":"s2"}',text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(project.parents[2])})
    assert child.returncode==1
    assert json.loads(child.stdout)['error_type']=='validation_error'


def test_uncatalogued_capture_not_silently_excluded(project):
    with duckdb.connect(str(project)) as c:
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('s1','retired-r3','show version')")
        c.execute("INSERT INTO netops.bgp_records VALUES ('retired-r3','s1','9','Idle')")
        c.execute("INSERT INTO netops.if_records VALUES ('retired-r3','s1','e9',NULL,'down','down')")
    summary=run()['steps'][0]
    assert summary['counts']['observed_device_count']==3 and summary['counts']['record_count']==3
    assert summary['observed_without_catalog_count']==1 and summary['observed_without_catalog_sample']==['retired-r3']
    assert {r['hostname'] for r in run()['steps'][1]['rows']}=={'r1','r2'}
    assert run('bgp')['steps'][0]['counts']['nonhealthy_record_count']==2
    assert run('interfaces')['steps'][0]['counts']['record_count']==4
    assert run('bgp',vendors=['Cisco'])['steps'][0]['counts']['record_count']==2
    assert 'uncatalogued vendor unknown/excluded' in run('bgp',vendors=['Cisco'])['vendor_scope']


def test_protocol_without_current_inventory_still_reads_records(project):
    with duckdb.connect(str(project)) as c:c.execute('DROP TABLE netops.devices')
    assert run('bgp')['steps'][0]['counts']['record_count']==4
    assert run('interfaces')['steps'][0]['counts']['record_count']==3
    assert run('bgp',vendors=['Cisco'])['status']=='error'


def test_view_wholly_unrecognized_columns_is_not_absence(project):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        c.execute('CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT 1 AS alien')
    out=run('bgp')
    assert out['status']=='error' and 'unsupported schema' in out['error']


def test_database_atomic_swap_and_restore_uses_bound_inode(project,monkeypatch):
    import shutil
    alternate=project.parent/'alternate.duckdb';held=project.parent/'held.duckdb'
    shutil.copy2(project,alternate)
    with duckdb.connect(str(alternate)) as c:
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('s1','r1','extra command')")
    original=m._inventory
    def swap(q,args):
        os.replace(project,held);os.replace(alternate,project)
        try:
            on_disk_file, = q.run("SELECT path FROM duckdb_databases() WHERE NOT internal")[0]
            assert Path(on_disk_file).parent != project.parent
            result = original(q,args)
            assert result[0]['counts']['record_count'] == 2
            return result
        finally:
            os.replace(held,project)
    monkeypatch.setattr(m,'_inventory',swap)
    out=run_inside_worker()
    assert out['status']=='error',out
    assert out['error_type']=='source_changed'


def test_script_worker_hard_timeout_removes_private_workspace(project, monkeypatch):
    calls = []
    original = subprocess.run
    def sleeping_worker(argv, **kwargs):
        scratch = Path(argv[-2])
        assert kwargs['env']['TMPDIR'] == str(scratch)
        assert kwargs['cwd'] == str(scratch)
        calls.append(scratch)
        source = "from pathlib import Path; import sys,time; Path(sys.argv[1],'copy').touch(); time.sleep(30)"
        return original([sys.executable, '-B', '-c', source, str(scratch)], **kwargs)
    monkeypatch.setattr(m, 'TIMEOUT_SECONDS', 0.3)
    monkeypatch.setattr(m.subprocess, 'run', sleeping_worker)
    began = time.monotonic()
    out = m.run_investigation({'recipe': 'inventory', 'snapshot_id': 's1'})
    assert out['error_type'] == 'timeout'
    assert 0.2 <= time.monotonic() - began < 3
    assert calls and not calls[0].exists()


def test_worker_uses_bound_package_and_drops_caller_environment(project, tmp_path, monkeypatch):
    shadow = tmp_path / 'shadow'
    fake = shadow / 'olav_skills'
    fake.mkdir(parents=True)
    (fake / '__init__.py').write_text('raise RuntimeError("wrong package loaded")\n', encoding='utf-8')
    monkeypatch.chdir(shadow)
    monkeypatch.setenv('PYTHONPATH', str(shadow))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-for-worker')
    monkeypatch.setenv('OLAV_DB_PATH', 'synthetic-not-for-worker')
    original = subprocess.run

    def inspect(argv, **kwargs):
        scratch = kwargs['cwd']
        assert str(shadow) != scratch and kwargs['env']['TMPDIR'] == scratch
        assert kwargs['env'] == {
            'OLAV_HOME': str(project.parents[2]), 'TMPDIR': scratch,
            'PYTHONPATH': str(Path(m.__file__).resolve().parent.parent),
            'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1',
        }
        assert not kwargs.get('start_new_session')
        return original(argv, **kwargs)

    monkeypatch.setattr(m.subprocess, 'run', inspect)
    out = run()
    assert out['status'] == 'success' and out['steps'][0]['counts']['record_count'] == 2


@pytest.mark.parametrize('config_from_environment', [True, False])
def test_warehouse_settings_refuse_native_before_worker(project, monkeypatch, config_from_environment):
    if config_from_environment:
        monkeypatch.setenv('OLAV_STORAGE_MODE', 'v3')
    else:
        config = project.parents[1] / 'config/ingest-storage.json'
        config.parent.mkdir()
        config.write_text('{}', encoding='utf-8')

    def unexpected(*args, **kwargs):
        pytest.fail('worker must not start for configured storage')

    monkeypatch.setattr(m.subprocess, 'run', unexpected)
    out = run()
    assert out['status'] == 'error' and out['findings'] is None
    assert 'supports native projects only' in out['error']


def test_python_investigation_does_not_release_callers_duckdb_writer_lock(project):
    code = """import sys,duckdb
try:
    duckdb.connect(sys.argv[1]).close()
except duckdb.IOException as exc:
    print('writer_locked' if 'lock' in str(exc).lower() else 'unexpected_error')
else:
    print('writer_opened')
"""
    with duckdb.connect(str(project)) as caller_writer:
        caller_writer.execute('BEGIN TRANSACTION')
        result = run()
        assert result['status'] == 'error' and result['error_type'] == 'source_locked', result
        child = subprocess.run([sys.executable, '-B', '-c', code, str(project)], capture_output=True,
                               text=True, timeout=10, check=True)
        assert child.stdout.strip() == 'writer_locked'
        caller_writer.execute('ROLLBACK')
    assert run()['status'] == 'success'


def test_python_request_keeps_full_filter_bounds_beyond_stdin_cap(project):
    devices = ['a' * 248 + f'{i:08d}' for i in range(50)]
    vendors = ['b' * 248 + f'{i:08d}' for i in range(50)]
    out = run('bgp', devices=devices, vendors=vendors)
    assert out['status'] == 'success', out.get('error')
    assert out['steps'][0]['counts']['record_count'] == 0
    assert out['effective_request']['devices'] == devices
    assert out['effective_request']['vendors'] == vendors
    args = {'recipe': 'bgp', 'snapshot_id': 's1', 'devices': devices, 'vendors': vendors}
    child = subprocess.run([sys.executable, '-B', '-m', 'olav_skills', 'run', 'analyzer', 'run_investigation'],
                           input=json.dumps(args), capture_output=True, text=True, timeout=10)
    assert child.returncode == 1
    assert json.loads(child.stdout)['error_type'] == 'validation_error'



def test_explicit_schema_collation_fails_closed(project):
    with duckdb.connect(str(project)) as c:
        c.execute('CREATE TABLE netops.collated(snapshot_id VARCHAR COLLATE NOCASE)')
        c.execute("INSERT INTO netops.collated VALUES ('s1'),('S1')")
    out=run()
    assert out['status']=='error' and 'COLLATE' in out['error']



def test_date_label_is_not_verified_collection_time(project):
    with duckdb.connect(str(project)) as c:
        c.execute("UPDATE netops.parsed_outputs SET snapshot_id='2026-11-01' WHERE snapshot_id='s1'")
    out=run(snapshot_id='2026-11-01')
    assert out['status']=='success',out
    assert out['temporal']['collected_at']['status']=='unknown'
    assert out['temporal']['snapshot_label_inference']['status']=='derived'
    assert out['steps'][1]['temporal']['collected_at']['value'] is None



def test_identifier_and_protocol_types_fail_closed(project):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        c.execute('CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT device_name,7 AS snapshot_id,bgp_neighbor,state_or_prefixes_received FROM netops.bgp_records')
    assert 'column type' in run('bgp')['error']
    with duckdb.connect(str(project)) as c:
        c.execute('CREATE TABLE netops.raw_output_store(snapshot_id INTEGER)')
    assert 'identity column type' in run()['error']


def test_extended_colliding_columns_do_not_change_membership(project):
    with duckdb.connect(str(project)) as c:
        c.execute("ALTER TABLE netops.devices ADD COLUMN device_name VARCHAR DEFAULT 'r2'")
        c.execute("ALTER TABLE netops.parsed_outputs ADD COLUMN hostname VARCHAR DEFAULT 'uncaptured'")
    out=run(vendors=['Cisco'])
    assert out['status']=='success',out
    assert out['steps'][0]['counts']['observed_device_count']==1
    assert {r['hostname'] for r in out['steps'][1]['rows']}=={'r1'}


def test_custom_macro_db_unsupported(project):
    with duckdb.connect(str(project)) as c:c.execute('CREATE MACRO custom_scope(x) AS lower(x)')
    out=run()
    assert out['status']=='error' and 'custom macros' in out['error']


def test_canonical_interface_nonup_states_are_observed_concerns(project):
    with duckdb.connect(str(project)) as c:
        c.execute("DELETE FROM netops.if_records")
        c.executemany("INSERT INTO netops.if_records VALUES ('r1','s1',?,NULL,?,'down')",[(str(i),s) for i,s in enumerate(['admin-down','err-disabled','testing','dormant','down'])])
    counts=run('interfaces')['steps'][0]['counts']
    assert counts['nonhealthy_record_count']==5 and counts['unknown_record_count']==0


def test_junos_interface_family_is_not_a_line_state(project):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_interface_brief_auto')
        c.execute('CREATE TABLE netops.junos_if(device_name VARCHAR,snapshot_id VARCHAR,interface VARCHAR,ip_address VARCHAR,admin_state VARCHAR,link_state VARCHAR,proto VARCHAR)')
        c.execute("INSERT INTO netops.junos_if VALUES ('r2','s1','ge-0/0/0',NULL,'up','up','inet'),('r2','s1','ge-0/0/1',NULL,'up','down','inet6'),('r2','s1','ge-0/0/2',NULL,'up',NULL,'inet')")
        c.execute('CREATE VIEW netops.v_show_interfaces_terse_auto AS SELECT * FROM netops.junos_if')
    out=run('interfaces');assert out['status']=='success',out
    step=out['steps'][0]
    assert step['counts']=={'record_count':3,'healthy_record_count':1,'nonhealthy_record_count':1,'unknown_record_count':1}
    assert all(row['protocol_semantics']=='family' and row['status'] is None for row in step['rows'])


def test_junos_bgp_native_fields_and_ambiguous_profiles(project):
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        c.execute('CREATE TABLE netops.junos_bgp(device_name VARCHAR,snapshot_id VARCHAR,peer_ip VARCHAR,state VARCHAR)')
        c.execute("INSERT INTO netops.junos_bgp VALUES ('r2','s1','1','Established'),('r2','s1','2','Idle'),('r2','s1','3','7'),('r2','s1','4',NULL)")
        c.execute('CREATE VIEW netops.v_show_bgp_summary_auto AS SELECT * FROM netops.junos_bgp')
    out=run('bgp');assert out['status']=='success',out
    assert out['steps'][0]['counts']=={'record_count':4,'healthy_record_count':1,'nonhealthy_record_count':1,'unknown_record_count':2}
    assert all(r['source_state_field']=='state' and r['state_semantics']=='junos_state' for r in out['steps'][0]['rows'])
    with duckdb.connect(str(project)) as c:
        c.execute('DROP VIEW netops.v_show_bgp_summary_auto')
        c.execute('CREATE VIEW netops.v_show_bgp_summary_auto AS SELECT *,peer_ip AS bgp_neighbor,state AS state_or_prefixes_received FROM netops.junos_bgp')
    assert run('bgp')['status']=='error'


def test_real_native_import_canonical_admin_down(tmp_path):
    root=tmp_path/'real-import';root.mkdir()
    env={**os.environ,'OLAV_HOME':str(root),'PYTHONDONTWRITEBYTECODE':'1'}
    fixture=Path(__file__).parent/'fixtures/synthetic_2host_bundle'
    child=subprocess.run([sys.executable,'-m','olav_skills','run','importer','ingest_snapshot'],input=json.dumps({'path':str(fixture)}),text=True,capture_output=True,env=env)
    assert child.returncode==0,(child.stdout,child.stderr)
    sid=json.loads(child.stdout)['snapshot_id']
    child=subprocess.run([sys.executable,'-m','olav_skills','run','analyzer','run_investigation'],input=json.dumps({'recipe':'interfaces','snapshot_id':sid,'devices':['R2']}),text=True,capture_output=True,env=env)
    assert child.returncode==0,(child.stdout,child.stderr)
    step=json.loads(child.stdout)['steps'][0]
    assert step['counts']=={'record_count':5,'healthy_record_count':4,'nonhealthy_record_count':1,'unknown_record_count':0}
    assert step['rows'][0]['interface']=='GigabitEthernet3' and step['rows'][0]['status']=='admin-down'
