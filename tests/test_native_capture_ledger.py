"""Official native CLI transactional history; no old-data backfill."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile
import duckdb
import pytest
import yaml
from olav_skills.netops.core.ingest import landing, native_ledger as history
from olav_skills.core.ingest.validators import _hash_bundle_contents
from olav_skills.core.ingest.schema import CommandFile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/importer/scripts/ingest_snapshot.py'
SOURCE=ROOT/'tests/fixtures/synthetic_2host_bundle'
DB='.olav/databases/main.duckdb'

def fixture_bundle(tmp_path):
    bundle=tmp_path/'bundle';shutil.copytree(SOURCE,bundle)
    return bundle

def rehash(bundle):
    file=bundle/'manifest.yaml';manifest=yaml.safe_load(file.read_text());manifest['content_sha256']=_hash_bundle_contents(bundle)[0];file.write_text(yaml.safe_dump(manifest))

def cli(home,bundle,*,cwd=None):
    env={**os.environ,'OLAV_HOME':str(home)}
    # Preserve the caller's import selection across a changed child CWD. Do not
    # add a source path when a fresh installed-package test has no PYTHONPATH.
    if 'PYTHONPATH' in env:
        env['PYTHONPATH']=os.pathsep.join(str(Path(part or '.').resolve()) for part in env['PYTHONPATH'].split(os.pathsep))
    proc=subprocess.run([sys.executable,'-B',str(SCRIPT)],input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,env=env,timeout=90,cwd=cwd)
    assert proc.returncode==0,(proc.stdout,proc.stderr)
    return json.loads(proc.stdout)

def imported(home,bundle,sid='explicit',**kw):
    db=home/DB;db.parent.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(db)) as conn:
        conn.execute('BEGIN')
        try:
            result=landing.ingest_snapshot(bundle,db_path=db,staging_dir=home/'staging',collection_source='bundle:synthetic',snapshot_id=sid,conn=conn,native_ledger=True,native_workspace_root=home/'.olav/workspace',**kw)
            conn.execute('COMMIT');return result
        except BaseException:
            conn.execute('ROLLBACK');raise

def evidence(home,sid):
    script=ROOT/'skills/analyzer/scripts/check_evidence.py'
    args={'snapshot_id':sid,'requirements':[{'device':'R2','command':'show ip interface brief'}],'as_of':'2026-05-15T10:24:00Z','max_age_seconds':60}
    proc=subprocess.run([sys.executable,'-B',str(script)],input=json.dumps(args),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(home)},timeout=30)
    assert proc.returncode==0,(proc.stdout,proc.stderr)
    return json.loads(proc.stdout)

def test_actual_cli_two_sids_raw_history_ready_and_hash(tmp_path):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project'
    first=cli(home,bundle);second=cli(home,bundle)
    assert first['snapshot_id']!=second['snapshot_id']
    with duckdb.connect(str(home/DB),read_only=True) as c:
        rows=c.execute("SELECT snapshot_id,raw_output,source_json,parser_json FROM netops.ingest_sources WHERE device_name='R2' AND command='show ip interface brief'").fetchall()
        assert {r[0] for r in rows}=={first['snapshot_id'],second['snapshot_id']}
        assert c.execute("SELECT DISTINCT snapshot_id FROM netops.raw_output_store").fetchall()==[(second['snapshot_id'],)]
    for sid,raw,source,parser in rows:
        meta=json.loads(source)
        artifact=bundle/meta['input_artifact'];cf=CommandFile.from_text(artifact.read_text(),filename_hint=artifact.name)
        assert meta['input_sha256']==hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert meta['body_sha256']==hashlib.sha256(cf.body.encode()).hexdigest()
        assert meta['actual_raw_sha256']==hashlib.sha256(raw.encode()).hexdigest()
        assert meta['device_name']=='R2' and meta['command']=='show ip interface brief'
        assert meta['raw_artifact']==dict(table='netops.ingest_sources',snapshot_id=sid,device_name='R2',command='show ip interface brief')
        assert meta['collected_at']['source']=='command_header' and meta['collected_at']['precision']=='timestamp'
        assert json.loads(parser)['tier'] and 'raw_output' not in meta and 'body' not in meta
        assert evidence(home,sid)['readiness']=='ready'

@pytest.mark.parametrize(('header', 'device_clock', 'status'), [
    (None, None, 'unknown'),
    ('2026-05-15T20:23:30+10:00', None, 'known'),
    ('2026-05-15T10:23:31Z', None, 'conflict'),
    ('2026-05-15T10:23:30Z', 'malformed', 'invalid'),
], ids=['metadata-only', 'aware-header', 'conflicting-header', 'invalid-device-clock'])
def test_actual_cli_requires_command_clock_for_capture(tmp_path, header, device_clock, status):
    bundle = fixture_bundle(tmp_path)
    home = tmp_path / 'project'
    command = bundle / 'devices/R2/show_ip_interface_brief.txt'
    clock_line = '' if header is None else f'# collected_at: {header}\n'
    command.write_text(command.read_text().replace('# collected_at: 2026-05-15T10:23:30Z\n', clock_line))
    if device_clock:
        meta_path = bundle / 'devices/R2/_meta.yaml'
        device = yaml.safe_load(meta_path.read_text())
        device['collected_at'] = device_clock
        meta_path.write_text(yaml.safe_dump(device))
    rehash(bundle)

    sid = cli(home, bundle, cwd=tmp_path)['snapshot_id']
    with duckdb.connect(str(home / DB), read_only=True) as conn:
        row, = conn.execute(
            "SELECT source_json FROM netops.ingest_sources WHERE snapshot_id = ? "
            "AND device_name = 'R2' AND command = 'show ip interface brief'", [sid]
        ).fetchone()
    provenance = json.loads(row)
    captured = provenance['collected_at']
    inputs = {clock['source']: clock for clock in provenance['capture_evidence']}
    instant = '2026-05-15T10:23:30+00:00'
    assert inputs['manifest']['value'] == instant
    assert inputs['device_meta']['value'] == (None if device_clock else instant)
    if header is None:
        assert inputs['command_header']['status'] == 'unknown'
    if status == 'known':
        assert captured == inputs['command_header']
        assert captured['value'] == instant and captured['precision'] == 'timestamp'
    else:
        assert captured == {'source': 'capture_evidence', 'status': status,
                            'value': None, 'precision': 'unknown'}
    readiness = evidence(home, sid)
    item, = readiness['requirements']
    assert item['raw_status'] == 'verified_nonempty'
    assert item['collected_at'] == captured
    assert readiness['readiness'] == ('ready' if status == 'known' else 'unknown')

def test_existing_zip_boundary_preserved(tmp_path):
    bundle=fixture_bundle(tmp_path);archive=tmp_path/'bundle.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for file in bundle.rglob('*'):
            if file.is_file(): z.write(file,'wrapper/'+file.relative_to(bundle).as_posix())
    # Existing validator/landing only accept directories, even though the
    # lower-level BundleReader can open ZIP. Do not expand that boundary here.
    from olav_skills.core.ingest.validators import validate_bundle
    assert not validate_bundle(archive).ok
    with pytest.raises(ValueError,match='not a directory'):
        landing.ingest_snapshot(archive,db_path=tmp_path/'legacy.duckdb',staging_dir=tmp_path/'legacy-stage',collection_source='synthetic')
    proc=subprocess.run([sys.executable,'-B',str(SCRIPT)],input=json.dumps({'path':str(archive)}),text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(tmp_path/'project')},timeout=30)
    assert proc.returncode and json.loads(proc.stdout)['status']=='rejected'

def test_optin_requires_active_native_transaction_before_ddl(tmp_path):
    bundle=fixture_bundle(tmp_path);db=tmp_path/'empty.duckdb'
    with duckdb.connect(str(db)) as c:
        with pytest.raises(history.NativeLedgerError,match='REQUIRES_NATIVE_TRANSACTION'):
            landing.ingest_snapshot(bundle,db_path=db,staging_dir=tmp_path/'stage',collection_source='synthetic',conn=c,native_ledger=True)
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='netops'").fetchone()==(0,)
    with pytest.raises(history.NativeLedgerError,match='REQUIRES_NATIVE_TRANSACTION'):
        landing.ingest_snapshot(bundle,db_path=db,staging_dir=tmp_path/'stage',collection_source='synthetic',native_ledger=True)

def test_same_sid_refused_without_overwrite(tmp_path):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';imported(home,bundle)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        before=c.execute('SELECT * FROM netops.ingest_sources ORDER BY device_name,command').fetchall()
    file=bundle/'devices/R2/show_ip_interface_brief.txt';file.write_text(file.read_text().replace('192.168.100.101','192.0.2.101'));rehash(bundle)
    with pytest.raises(history.NativeLedgerError,match='SNAPSHOT_EXISTS'): imported(home,bundle)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        assert c.execute('SELECT * FROM netops.ingest_sources ORDER BY device_name,command').fetchall()==before

def test_ledger_failure_rolls_all_database_tables_back(tmp_path,monkeypatch):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';imported(home,bundle,'before')
    with duckdb.connect(str(home/DB),read_only=True) as c:
        tables=('raw_output_store','parsed_outputs','devices','bundle_ingests','ingest_sources')
        before={t:c.execute('SELECT * FROM netops.'+t+' ORDER BY ALL').fetchall() for t in tables}
    original=history.insert_records
    def fail(*args):
        original(*args)
        raise RuntimeError('injected after ledger insert')
    monkeypatch.setattr(history,'insert_records',fail)
    with pytest.raises(RuntimeError,match='injected'): imported(home,bundle,'after')
    with duckdb.connect(str(home/DB),read_only=True) as c:
        assert {t:c.execute('SELECT * FROM netops.'+t+' ORDER BY ALL').fetchall() for t in tables}==before

def test_first_import_failure_rolls_back_ledger_ddl(tmp_path,monkeypatch):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project'
    monkeypatch.setattr(history,'insert_records',lambda *a:(_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError,match='injected'): imported(home,bundle)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='netops'").fetchone()==(0,)

def test_input_and_scrubbed_hashes_are_separate(tmp_path,monkeypatch):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';marker='SYNTHETIC_PRIVATE_MARKER'
    file=bundle/'devices/R2/show_running_config.txt'
    file.write_text('# command: show running-config\n# collected_at: 2026-05-15T10:23:30Z\n\n'+marker+'\n')
    path=bundle/'manifest.yaml';manifest=yaml.safe_load(path.read_text());manifest['redaction']['pre_scrubbed']=False;path.write_text(yaml.safe_dump(manifest));rehash(bundle)
    original=landing._safe_text_for_db
    monkeypatch.setattr(landing,'_safe_text_for_db',lambda text,**kw:text.replace(marker,'[REDACTED]'))
    imported(home,bundle)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        raw,source=c.execute("SELECT raw_output,source_json FROM netops.ingest_sources WHERE device_name='R2' AND command='show running-config'").fetchone()
    meta=json.loads(source)
    assert marker not in raw+source and '[REDACTED]' in raw
    assert meta['body_sha256']!=meta['actual_raw_sha256']==hashlib.sha256(raw.encode()).hexdigest()
    assert meta['redaction_assurance']=='not_certified'

@pytest.mark.parametrize('fault',['duplicate','schema','clock_conflict','date','naive','missing'])
def test_input_shape_and_clock_boundaries(tmp_path,fault):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project'
    if fault=='duplicate': shutil.copy2(bundle/'devices/R2/show_ip_interface_brief.txt',bundle/'devices/R2/duplicate.txt');rehash(bundle)
    if fault=='schema':
        db=home/DB;db.parent.mkdir(parents=True)
        with duckdb.connect(str(db)) as c:c.execute('CREATE SCHEMA netops');c.execute('CREATE TABLE netops.ingest_sources(snapshot_id INTEGER)')
    if fault in ('clock_conflict','date','naive','missing'):
        file=bundle/'devices/R2/show_ip_interface_brief.txt';text=file.read_text()
        replacement={'clock_conflict':'2026-05-15T10:23:31Z','date':'2026-05-15','naive':'2026-05-15T10:23:30','missing':''}[fault]
        text='\n'.join(line for line in text.splitlines() if not line.startswith('# collected_at:')) if fault=='missing' else text.replace('2026-05-15T10:23:30Z',replacement)
        file.write_text(text);rehash(bundle)
    if fault in ('duplicate','schema'):
        with pytest.raises(history.NativeLedgerError): imported(home,bundle)
    else:
        imported(home,bundle)
        result=evidence(home,'explicit')
        assert result['readiness']=='unknown',result

def test_legacy_library_no_ledger_no_backfill(tmp_path):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';db=home/DB;db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('BEGIN')
        landing.ingest_snapshot(bundle,db_path=db,staging_dir=home/'stage',collection_source='synthetic',snapshot_id='legacy',conn=c)
        c.execute('COMMIT')
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='ingest_sources'").fetchone()==(0,)
    assert evidence(home,'legacy')['readiness']=='unknown'
    imported(home,bundle,'new')
    with duckdb.connect(str(db),read_only=True) as c:
        assert c.execute("SELECT count(*) FROM netops.ingest_sources WHERE snapshot_id='legacy'").fetchone()==(0,)
    assert evidence(home,'legacy')['readiness']=='unknown'


def test_actual_script_wrapper_failure_rollback(tmp_path):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';first=cli(home,bundle)
    program='''import runpy,sys
from olav_skills.netops.core.ingest import native_ledger as h
original=h.insert_records
def fail(*args):
 original(*args)
 raise RuntimeError('injected ledger failure')
h.insert_records=fail
entry=runpy.run_path(sys.argv[1])
try: entry['ingest_snapshot'](path=sys.argv[2])
except RuntimeError: pass
else: raise AssertionError('failure did not propagate')
'''
    child=subprocess.run([sys.executable,'-B','-c',program,str(SCRIPT),str(bundle)],text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(home)},timeout=90)
    assert child.returncode==0,(child.stdout,child.stderr)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        for table in ('raw_output_store','parsed_outputs','ingest_sources','bundle_ingests'):
            assert c.execute('SELECT DISTINCT snapshot_id FROM netops.'+table).fetchall()==[(first['snapshot_id'],)]


def test_real_redaction_precedes_ledger_raw_binding(tmp_path):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';secret='SyntheticOnlyCommunity123'
    file=bundle/'devices/R2/show_running_config.txt'
    file.write_text('# command: show running-config\n# collected_at: 2026-05-15T10:23:30Z\n\n'+'snmp-server community '+secret+' RO\n')
    path=bundle/'manifest.yaml';manifest=yaml.safe_load(path.read_text());manifest['redaction']['pre_scrubbed']=False;path.write_text(yaml.safe_dump(manifest));rehash(bundle)
    foreign=tmp_path/'foreign-cwd';foreign.mkdir()
    result=cli(home,bundle,cwd=foreign)
    assert not (foreign/'.olav').exists() and not (foreign/'state').exists()
    assert list(foreign.iterdir())==[]
    assert (home/'.olav/workspace/.redaction_salt').is_file()
    with duckdb.connect(str(home/DB),read_only=True) as c:
        raw,source=c.execute("SELECT raw_output,source_json FROM netops.ingest_sources WHERE device_name='R2' AND command='show running-config'").fetchone()
    meta=json.loads(source)
    assert secret not in raw+source and 'snmp-server community' in raw
    assert meta['actual_raw_sha256']==hashlib.sha256(raw.encode()).hexdigest()!=meta['body_sha256']


def test_capture_file_changed_during_parse_refused(tmp_path,monkeypatch):
    bundle=fixture_bundle(tmp_path);home=tmp_path/'project';original=landing._parse_one
    file=bundle/'devices/R2/show_ip_interface_brief.txt'
    changed=[False]
    def parse(*args,**kw):
        if not changed[0]:
            file.write_text(file.read_text()+'\nCHANGED\n');changed[0]=True
        return original(*args,**kw)
    monkeypatch.setattr(landing,'_parse_one',parse)
    with pytest.raises(history.NativeLedgerError,match='INPUT_CHANGED'): imported(home,bundle)
    with duckdb.connect(str(home/DB),read_only=True) as c:
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='netops'").fetchone()==(0,)


def test_script_rejects_changed_home_after_cached_project_context(tmp_path):
    bundle=fixture_bundle(tmp_path);initial=tmp_path/'initial';selected=tmp_path/'selected'
    program='''import os,runpy,sys
from olav_skills.core.config import get_paths_config
get_paths_config()
os.environ['OLAV_HOME']=sys.argv[3]
entry=runpy.run_path(sys.argv[1])
try: entry['ingest_snapshot'](path=sys.argv[2])
except ValueError as exc:
 assert 'project context changed' in str(exc)
else: raise AssertionError('cached project mismatch accepted')
'''
    child=subprocess.run([sys.executable,'-B','-c',program,str(SCRIPT),str(bundle),str(selected)],text=True,capture_output=True,env={**os.environ,'OLAV_HOME':str(initial)},timeout=30)
    assert child.returncode==0,(child.stdout,child.stderr)
    assert not (initial/DB).exists() and not (selected/DB).exists()
