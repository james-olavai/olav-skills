"""Independent manifest, temporal and exact-source expectations."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from olav_skills import evidence_readiness as evidence

DB = '.olav/databases/main.duckdb'
STAMP = '2026-10-01T00:00:00+00:00'
REQUEST = {'snapshot_id': 'Exact', 'requirements': [{'device': 'Edge-1', 'command': 'show interfaces'}],
           'as_of': '2026-10-01T00:01:00Z', 'max_age_seconds': 60}


def call(home, request):
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'TMPDIR') if k in os.environ}
    env['OLAV_HOME'] = str(home)
    result = subprocess.run([sys.executable, '-B', '-m', 'olav_skills.evidence_readiness'],
        cwd=home, env=env, input=json.dumps(request), text=True, capture_output=True, timeout=40)
    assert result.stdout, (result.returncode, result.stderr)
    return result.returncode, json.loads(result.stdout)


def mutate(home, sql, params=()):
    with duckdb.connect(str(home / DB)) as conn:
        conn.execute(sql, params)


@pytest.fixture
def project(tmp_path):
    path = tmp_path / DB
    path.parent.mkdir(parents=True)
    clock = dict(value=STAMP, precision='timestamp', source='command_header', status='known')
    meta = {'raw_artifact': {'table': 'netops.ingest_sources', 'snapshot_id': 'Exact',
                            'device_name': 'Edge-1', 'command': 'show interfaces'},
            'actual_raw_sha256': hashlib.sha256(b'raw').hexdigest(), 'collected_at': clock,
            'capture_evidence': [clock]}
    with duckdb.connect(str(path)) as conn:
        conn.execute('CREATE SCHEMA netops')
        conn.execute('CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR, device_name VARCHAR, '
                     'command VARCHAR, raw_output VARCHAR, source_json VARCHAR)')
        conn.execute('INSERT INTO netops.ingest_sources VALUES (?,?,?,?,?)',
                     ['Exact', 'Edge-1', 'show interfaces', 'raw', json.dumps(meta)])
        conn.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR, device_name VARCHAR, '
                     'command VARCHAR, parsed_data JSON)')
        conn.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?)',
                     ['Exact', 'Edge-1', 'show interfaces', '[{"interface":"Gi0/1"}]'])
    return tmp_path


def meta_patch(home, change):
    with duckdb.connect(str(home / DB)) as conn:
        meta = json.loads(conn.execute('SELECT source_json FROM netops.ingest_sources').fetchone()[0])
        change(meta)
        conn.execute('UPDATE netops.ingest_sources SET source_json=?', [json.dumps(meta)])


def test_explicit_manifest_exact_time_boundary(project):
    before = hashlib.sha256((project / DB).read_bytes()).hexdigest()
    code, out = call(project, REQUEST)
    assert code == 0 and out['readiness'] == 'ready', out
    assert out['summary'] == {'requirement_count': 1, 'ready': 1, 'not_ready': 0, 'unknown': 0,
                             'raw_verified': 1, 'parsed_nonempty': 1, 'fresh': 1}
    assert out['source_version']['sha256'] == before == hashlib.sha256((project / DB).read_bytes()).hexdigest()
    assert out['collection_completeness'] == out['parsing_success'] == out['health_assessment'] == 'unknown'
    assert out['requirements'][0]['age_seconds'] == 60


@pytest.mark.parametrize('patch', [{'snapshot_id': 'latest'}, {'max_age_seconds': True},
    {'max_age_seconds': -1}, {'as_of': '2026-10-01'}, {'as_of': '2026-10-01T00:00:00'},
    {'requirements': []}, {'requirements': REQUEST['requirements']*2}, {'sql': 'SELECT 1'}])
def test_bad_request(project, patch):
    code, out = call(project, REQUEST | patch)
    assert code == 1 and out['readiness'] == 'unknown'


@pytest.mark.parametrize('command', ['Show interfaces', 'show  interfaces', 'show interfaces vrf RED'])
def test_command_identity_is_binary_exact(project, command):
    code, out = call(project, REQUEST | {'requirements': [{'device': 'Edge-1', 'command': command}]})
    assert code == 0 and out['readiness'] == 'not_ready'
    assert out['requirements'][0]['raw_status'] == 'missing'


def test_manifest_denominator_includes_missing(project):
    code, out = call(project, REQUEST | {'requirements': REQUEST['requirements'] + [{'device':'absent', 'command':'show interfaces'}]})
    assert code == 0 and out['summary']['requirement_count'] == 2 and out['summary']['not_ready'] == 1


@pytest.mark.parametrize('value,status', [('[]','empty'), ('null','unknown'), ('{}','unknown'), ('[{}]','unknown')])
def test_parsed_presence_not_success(project, value, status):
    mutate(project, 'UPDATE netops.parsed_outputs SET parsed_data=?', [value])
    code, out = call(project, REQUEST)
    assert code == 0 and out['requirements'][0]['parsed_status'] == status
    assert out['readiness'] != 'ready'


@pytest.mark.parametrize('field,value', [('actual_raw_sha256','0'*64), ('raw_artifact',{}),
    ('raw_artifact',{'table':'netops.ingest_sources','snapshot_id':'Other','device_name':'Edge-1','command':'show interfaces'})])
def test_bad_provenance_rejected(project, field, value):
    meta_patch(project, lambda meta: meta.update({field:value}))
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == 'invalid_raw_provenance'


@pytest.mark.parametrize('clock', [dict(value='2026-10-01', precision='date', source='command_header', status='known'),
    dict(value=STAMP, precision='timestamp', source='manifest', status='known'),
    dict(value=None, precision='unknown', source='capture_evidence', status='conflict'),
    dict(value=None, precision='unknown', source=None, status='unknown'),
    dict(value='2026-10-01T00:01:01+00:00', precision='timestamp', source='command_header', status='known')])
def test_unknown_clock_never_fresh(project, clock):
    meta_patch(project, lambda meta: meta.update(collected_at=clock, capture_evidence=[clock]))
    code, out = call(project, REQUEST)
    assert code == 0 and out['readiness'] == 'unknown', out
    assert out['requirements'][0]['freshness'] == 'unknown'


def test_hidden_capture_conflict_unknown(project):
    meta_patch(project, lambda meta: meta['capture_evidence'].append(dict(value='2026-10-02', precision='date', source='manifest', status='known')))
    code, out = call(project, REQUEST)
    assert code == 0 and out['readiness'] == 'unknown'


def test_stale(project):
    code, out = call(project, REQUEST | {'max_age_seconds': 59})
    assert code == 0 and out['readiness'] == 'not_ready' and out['requirements'][0]['freshness'] == 'stale'


@pytest.mark.parametrize('table,error', [('ingest_sources','ambiguous_raw_source'), ('parsed_outputs','ambiguous_parsed_source')])
def test_duplicate_source(project, table, error):
    mutate(project, f'INSERT INTO netops.{table} SELECT * FROM netops.{table}')
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == error


def test_source_budget(project):
    mutate(project, 'UPDATE netops.ingest_sources SET raw_output=?', ['x'*(512*1024+1)])
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == 'source_budget_exceeded'


def test_writer_lock_and_default_connection_preserved(project, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(project))
    duckdb.execute('CREATE TABLE evidence_caller_marker(value INTEGER)')
    try:
        with duckdb.connect(str(project / DB)) as writer:
            assert evidence.check_evidence(REQUEST)['error_type'] == 'source_locked'
            assert writer.execute('SELECT count(*) FROM netops.ingest_sources').fetchone() == (1,)
        duckdb.execute('INSERT INTO evidence_caller_marker VALUES (7)')
        assert duckdb.execute('SELECT * FROM evidence_caller_marker').fetchall() == [(7,)]
    finally:
        duckdb.execute('DROP TABLE evidence_caller_marker')


def test_cleanenv_nonzero_and_timeout_cleanup(project, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(project))
    monkeypatch.setenv('FORBIDDEN_CREDENTIAL','marker')
    scratch=[]
    def fake(cmd, **kw):
        assert 'FORBIDDEN_CREDENTIAL' not in kw['env']
        assert cmd[2:5] == ['-P','-s','-m']
        scratch.append(Path(kw['cwd']))
        raise subprocess.TimeoutExpired(cmd, 0)
    monkeypatch.setattr(subprocess,'run',fake)
    assert evidence.check_evidence(REQUEST)['error_type'] == 'deadline_exceeded'
    assert not scratch[0].exists()
    monkeypatch.setattr(subprocess,'run',lambda cmd, **kw: subprocess.CompletedProcess(cmd,1,b'{"status":"success"}'))
    assert evidence.check_evidence(REQUEST)['error_type'] == 'worker_failed'


def test_real_native_import_header_clock(tmp_path):
    from test_counter_deltas import native_bundle
    home=tmp_path/'native'
    home.mkdir()
    bundle=native_bundle(tmp_path/'bundle','before','2026-10-01T00:00:00Z',[0]*5)
    env={k:os.environ[k] for k in ('PATH','PYTHONPATH','TMPDIR') if k in os.environ}
    env['OLAV_HOME']=str(home)
    result=subprocess.run([sys.executable,'-B','-m','olav_skills','run','importer','ingest_snapshot'],
        input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,cwd=home,env=env,timeout=60)
    assert result.returncode == 0,(result.stdout,result.stderr)
    sid=json.loads(result.stdout)['snapshot_id']
    code,out=call(home,REQUEST|{'snapshot_id':sid})
    assert code == 0 and out['readiness'] == 'ready',out
    assert out['requirements'][0]['age_seconds'] == 60
    assert out['requirements'][0]['parsed_status'] == 'nonempty'
    # Explicit synthetic legacy condition: erase fixture history, never backfill.
    mutate(home, 'DROP TABLE netops.ingest_sources')
    code, legacy = call(home, REQUEST | {'snapshot_id': sid})
    assert code == 0 and legacy['readiness'] == 'unknown'
    assert 'historical_raw_ledger_unavailable' in legacy['requirements'][0]['reasons']
    again = subprocess.run([sys.executable,'-B','-m','olav_skills','run','importer','ingest_snapshot'],
        input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,cwd=home,env=env,timeout=60)
    assert again.returncode == 0, (again.stdout,again.stderr)
    new_sid = json.loads(again.stdout)['snapshot_id']
    assert new_sid != sid
    code, old = call(home, REQUEST | {'snapshot_id': sid})
    assert code == 0 and old['readiness'] == 'unknown', old
    assert old['requirements'][0]['raw_status'] == 'unknown'
    code, new = call(home, REQUEST | {'snapshot_id': new_sid})
    assert code == 0 and new['readiness'] == 'ready', new


@pytest.mark.parametrize('payload', [b'[]', b'{"schema":"wrong","status":"success"}',
    b'{"schema":"olav.evidence-readiness/v1","status":"success","effective_request":{}}'])
def test_worker_scope_gate(project, monkeypatch, payload):
    monkeypatch.setenv('OLAV_HOME', str(project))
    monkeypatch.setattr(subprocess,'run',lambda cmd, **kw: subprocess.CompletedProcess(cmd,0,payload))
    assert evidence.check_evidence(REQUEST)['error_type'] == 'worker_result_invalid'


def test_nonfinite_parsed_unknown(project):
    mutate(project,'ALTER TABLE netops.parsed_outputs ALTER COLUMN parsed_data TYPE VARCHAR')
    mutate(project,'UPDATE netops.parsed_outputs SET parsed_data=?',['[{"x":NaN}]'])
    code,out=call(project,REQUEST)
    assert code == 0 and out['readiness'] == 'unknown'


def test_wal_rejected(project):
    wal=Path(str(project/DB)+'.wal')
    wal.write_bytes(b'synthetic-uncheckpointed-wal')
    code,out=call(project,REQUEST)
    assert code == 1 and out['error_type'] == 'source_has_wal'


def test_no_last_writer_fallback(project):
    mutate(project,'ALTER TABLE netops.ingest_sources RENAME TO raw_output_store')
    code,out=call(project,REQUEST)
    assert code == 0 and out['readiness'] == 'unknown'
    assert out['requirements'][0]['raw_status'] == 'unknown'


def test_case_distinct_requirements_not_deduplicated(project):
    code,out=call(project,REQUEST|{'requirements': REQUEST['requirements'] + [{'device':'Edge-1','command':'Show interfaces'}]})
    assert code == 0 and out['summary']['requirement_count'] == 2
    assert out['summary']['ready'] == 1 and out['summary']['not_ready'] == 1


def test_close_failure_prevents_success_output(monkeypatch):
    import io
    from types import SimpleNamespace
    output=io.BytesIO()
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(REQUEST).encode())))
    monkeypatch.setattr(sys,'stdout',SimpleNamespace(buffer=output))
    monkeypatch.setattr(evidence,'_worker',lambda *args: {'status':'success'})
    # The mock represents an already-owned qualified reader. Real exec and
    # arbitrary-caller ownership are exercised by test_native_reader_bootstrap.
    from olav_skills import native_reader_bootstrap
    monkeypatch.setattr(native_reader_bootstrap,'owns_native_reader',lambda module: True)
    def close_failure():
        raise RuntimeError('synthetic-close-failure')
    monkeypatch.setattr(evidence,'close_native_reader',close_failure)
    with pytest.raises(RuntimeError,match='synthetic-close-failure'):
        evidence.main(scratch='synthetic',deadline='1')
    assert output.getvalue() == b''


def test_oversized_parsed_is_error(project):
    mutate(project,'UPDATE netops.parsed_outputs SET parsed_data=?',[json.dumps([{'padding':'x'*(512*1024)}])])
    code,out=call(project,REQUEST)
    assert code == 1 and out['error_type'] == 'source_budget_exceeded'


def test_real_native_clock_conflict_not_ready(tmp_path):
    import yaml
    from test_counter_deltas import native_bundle
    home=tmp_path/'native-conflict';home.mkdir()
    bundle=native_bundle(tmp_path/'bundle-conflict','before','2026-10-01T00:00:00Z',[0]*5)
    meta_path=bundle/'devices/Edge-1/_meta.yaml'
    meta=yaml.safe_load(meta_path.read_text());meta['collected_at']='2026-10-01T00:00:01Z'
    meta_path.write_text(yaml.safe_dump(meta))
    env={k:os.environ[k] for k in ('PATH','PYTHONPATH','TMPDIR') if k in os.environ}
    env['OLAV_HOME']=str(home)
    result=subprocess.run([sys.executable,'-B','-m','olav_skills','run','importer','ingest_snapshot'],
        input=json.dumps({'path':str(bundle)}),text=True,capture_output=True,cwd=home,env=env,timeout=60)
    assert result.returncode == 0,(result.stdout,result.stderr)
    sid=json.loads(result.stdout)['snapshot_id']
    code,out=call(home,REQUEST|{'snapshot_id':sid})
    assert code == 0 and out['readiness'] == 'unknown',out
    item=out['requirements'][0]
    assert item['raw_status']=='verified_nonempty' and item['parsed_status']=='nonempty'
    assert item['collected_at']['status']=='conflict' and item['freshness']=='unknown'
