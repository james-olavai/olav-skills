"""Synthetic data only; exercise public CLI and bounded read-only library."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from olav_skills.readiness import data_doctor, serialize


def cli(project=None, *extra, env_home=None):
    env = dict(os.environ)
    env.pop('OLAV_HOME', None)
    if env_home is not None:
        env['OLAV_HOME'] = str(env_home)
    cmd = [sys.executable, '-m', 'olav_skills', 'doctor', '--data']
    if project is not None:
        cmd += ['--project', str(project)]
    p = subprocess.run(cmd + list(extra), input='', capture_output=True, text=True, env=env, timeout=10)
    return p, json.loads(p.stdout)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'public-project'
    db = root / '.olav/databases/main.duckdb'
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.devices(hostname VARCHAR)')
        c.execute("INSERT INTO netops.devices VALUES ('edge-a'),('core-b'),('intent-only'),(NULL)")
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR)')
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('before','edge-a','show x'),('before','core-b','show y'),('after','edge-a','show x')")
        c.execute('CREATE TABLE netops.raw_output_store(device_name VARCHAR,command VARCHAR,snapshot_id VARCHAR,raw_output VARCHAR)')
        c.execute("INSERT INTO netops.raw_output_store VALUES ('edge-a','show x','after','BODY_MUST_NEVER_BE_RETURNED')")
        c.execute('CREATE TABLE netops.topology_links(snapshot_id VARCHAR,source_device VARCHAR,destination_device VARCHAR)')
        c.execute("INSERT INTO netops.topology_links VALUES ('after','edge-a','core-b')")
        c.execute('CREATE TABLE netops.bundle_ingests(snapshot_id VARCHAR,collected_at TIMESTAMP,ingested_at TIMESTAMP)')
        c.execute("INSERT INTO netops.bundle_ingests VALUES ('before',NULL,'2026-11-18 01:00:00'),('after','2026-11-17 01:00:00','2026-11-18 01:00:00')")
        c.execute("CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT 'after' AS snapshot_id,'edge-a' AS device_name,'192.0.2.1' AS bgp_neighbor WHERE FALSE")
    return root


def sources(data):
    return {x['source'].split('.', 1)[1]: x for x in data['sources']}


def fingerprint(project):
    return {p.relative_to(project).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in project.rglob('*') if p.is_file()}


def test_real_cli_two_snapshots_readonly(project):
    before = fingerprint(project)
    p, data = cli(project, '--snapshot-id', 'before')
    assert p.returncode == 0 and data['status'] == 'success'
    assert data['schema'] == 'olav.data-readiness/v1'
    assert data['data_readiness'] == 'recorded_queries_available'
    s = sources(data)
    assert s['devices']['record_count'] == 4
    assert s['devices']['distinct_identities'] == 3
    assert s['devices']['null_identities'] == 1
    assert s['parsed_outputs']['record_count'] == 2 and s['parsed_outputs']['observed_devices'] == 2
    assert s['raw_output_store']['record_count'] == 1
    assert s['raw_output_store']['record_scope'] == 'current_last_writer_all_records'
    assert s['topology_links']['status'] == 'empty'
    assert s['topology_links']['record_scope'] == 'current_last_writer_label'
    assert s['v_show_ip_bgp_summary_auto']['record_scope'] == 'current_view_projection_filtered_by_label'
    assert s['v_show_ip_bgp_summary_auto']['status'] == 'empty'
    assert s['bundle_ingests']['collection_time']['status'] == 'unknown'
    assert s['bundle_ingests']['collection_time']['min']['value'] is None
    assert data['coverage']['collection'] == data['coverage']['parsing'] == 'unknown'
    assert 'BODY_MUST_NEVER_BE_RETURNED' not in p.stdout
    assert data['snapshot_labels']['labels'] == ['after', 'before']
    p2, after = cli(project, '--snapshot-id', 'after')
    assert p2.returncode == 0 and sources(after)['parsed_outputs']['record_count'] == 1
    t = sources(after)['bundle_ingests']['collection_time']
    assert t['status'] == 'known' and t['min']['precision'] == 'timestamp_without_timezone'
    assert fingerprint(project) == before


def test_missing_db_no_create(tmp_path):
    before = fingerprint(tmp_path)
    p, data = cli(tmp_path)
    assert p.returncode == 0 and data['database_status'] == 'missing'
    assert data['data_readiness'] == 'unavailable'
    assert fingerprint(tmp_path) == before and not (tmp_path / '.olav').exists()


def test_no_snapshot_and_unknown_sid(project):
    p, data = cli(project)
    assert p.returncode == 0 and data['data_readiness'] == 'unknown'
    assert sources(data)['parsed_outputs']['status'] == 'snapshot_not_selected'
    assert sources(data)['devices']['record_count'] == 4
    _, absent = cli(project, '--snapshot-id', 'not-recorded')
    assert absent['data_readiness'] == 'insufficient_recorded_data'
    assert sources(absent)['parsed_outputs']['status'] == 'empty'
    assert absent['coverage']['collection'] == 'unknown'


def test_explicit_selection_and_invalid_project(project, tmp_path, monkeypatch):
    monkeypatch.delenv('OLAV_HOME', raising=False)
    p, data = cli()
    assert p.returncode == 2 and data['error_type'] == 'validation_error'
    p, data = cli(env_home=project)
    assert p.returncode == 0 and data['scope']['project'] == str(project)
    # An explicit project overrides a conflicting env; there is no dotenv read.
    (project / '.env').write_text('OLAV_HOME=/definitely/not/authorized\n')
    p, data = cli(project, env_home='/missing-wrong-project')
    assert p.returncode == 0 and data['scope']['project'] == str(project)
    for invalid in ('relative', tmp_path / 'missing'):
        p, data = cli(invalid)
        assert p.returncode == 2 and data['error_type'] == 'validation_error'
    for alias in ('latest', 'CURRENT', 'newest', ''):
        p, data = cli(project, '--snapshot-id', alias)
        assert p.returncode == 2 and data['error_type'] == 'validation_error'


@pytest.mark.parametrize('component', ['project', '.olav', 'databases', 'main.duckdb'])
def test_symlinks_refused(project, tmp_path, component):
    if component == 'project':
        link = tmp_path / 'project-link'
        link.symlink_to(project, target_is_directory=True)
        chosen = link
    else:
        original = {'main.duckdb': project / '.olav/databases/main.duckdb', 'databases': project / '.olav/databases', '.olav': project / '.olav'}[component]
        target = tmp_path / ('moved-' + component)
        original.rename(target)
        original.symlink_to(target, target_is_directory=component != 'main.duckdb')
        chosen = project
    p, data = cli(chosen)
    assert p.returncode == 2 and data['error_type'] == 'validation_error'


@pytest.mark.parametrize('schema', ['absent', 'empty', 'missing_columns', 'valid_empty'])
def test_schema_cases(tmp_path, schema):
    path = tmp_path / '.olav/databases/main.duckdb'
    path.parent.mkdir(parents=True)
    with duckdb.connect(str(path)) as c:
        if schema != 'absent':
            c.execute('CREATE SCHEMA netops')
        if schema == 'missing_columns':
            c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR)')
        if schema == 'valid_empty':
            c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR)')
    before = fingerprint(tmp_path)
    p, data = cli(tmp_path, '--snapshot-id', 'label')
    assert p.returncode == 0
    if schema == 'absent':
        assert data['netops_schema'] == 'missing' and data['data_readiness'] == 'unavailable'
    else:
        expected = {'empty': 'missing', 'missing_columns': 'unsupported_schema', 'valid_empty': 'empty'}[schema]
        assert sources(data)['parsed_outputs']['status'] == expected
    assert fingerprint(tmp_path) == before


def test_timeout_and_output_budget(project):
    before = fingerprint(project)
    data = data_doctor(project, 'before', timeout_ms=1)
    assert data['status'] == 'error' and data['error_type'] == 'timeout'
    data = data_doctor(project, 'before', max_bytes=512)
    assert data['status'] == 'error' and data['error_type'] == 'output_budget'
    assert len(serialize(data).encode()) <= 512
    assert fingerprint(project) == before


def test_broken_db_and_external_view_denied(project, tmp_path):
    with duckdb.connect(str(project / '.olav/databases/main.duckdb')) as c:
        secret = tmp_path / 'synthetic-secret.txt'
        secret.write_text('do-not-return-this-synthetic-body')
        c.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        quoted = str(secret).replace("'", "''")
        c.execute("CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT 'before' AS snapshot_id,content AS device_name,'x' AS bgp_neighbor FROM read_text('" + quoted + "')")
    p, data = cli(project, '--snapshot-id', 'before')
    assert p.returncode == 2 and data['error_type'] == 'external_access_denied'
    assert 'do-not-return-this' not in p.stdout
    db = project / '.olav/databases/main.duckdb'
    db.write_bytes(b'not a duckdb database')
    before = fingerprint(project)
    p, data = cli(project)
    assert p.returncode == 2 and data['error_type'] == 'database_error'
    assert fingerprint(project) == before


def test_snapshot_and_column_bounds(project):
    with duckdb.connect(str(project / '.olav/databases/main.duckdb')) as c:
        c.executemany("INSERT INTO netops.parsed_outputs VALUES (?,'edge-a','show')", [(f's{i:02}',) for i in range(25)])
        for i in range(35):
            c.execute(f'ALTER TABLE netops.devices ADD COLUMN extra_{i} VARCHAR')
    p, data = cli(project)
    assert p.returncode == 0 and data['truncated'] is True
    assert len(data['snapshot_labels']['labels']) == 20
    assert data['snapshot_labels']['truncated'] is True
    assert len(sources(data)['devices']['columns']) == 32
    assert sources(data)['devices']['columns_truncated'] is True


def test_default_doctor_stays_lazy():
    code = "from olav_skills.cli import main; import sys; rc=main(['doctor']); assert 'olav_skills.readiness' not in sys.modules; assert 'duckdb' not in sys.modules; raise SystemExit(rc)"
    p = subprocess.run([sys.executable, '-c', code], text=True, capture_output=True, timeout=10)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)['ok'] is True
    p = subprocess.run([sys.executable, '-m', 'olav_skills', 'doctor', '--project', '/tmp'], text=True, capture_output=True, timeout=10)
    assert p.returncode == 2 and 'require doctor --data' in p.stderr


def test_memory_budget(project):
    with duckdb.connect(str(project / '.olav/databases/main.duckdb')) as c:
        c.execute('DROP TABLE netops.devices')
        c.execute('CREATE VIEW netops.devices AS SELECT repeat(md5(CAST(i AS VARCHAR)),32) AS hostname FROM range(1000000) r(i) GROUP BY hostname')
    before = fingerprint(project)
    p, data = cli(project)
    assert p.returncode == 2 and data['error_type'] in ('resource_budget', 'timeout')
    assert data['data_readiness'] == 'unknown'
    assert fingerprint(project) == before


def test_real_cli_deadline(project):
    with duckdb.connect(str(project / '.olav/databases/main.duckdb')) as c:
        c.execute('DROP TABLE netops.devices')
        c.execute('CREATE VIEW netops.devices AS SELECT CAST(sum(sin(i)) AS VARCHAR) AS hostname FROM range(1000000000000) r(i)')
    before = fingerprint(project)
    p, data = cli(project)
    assert p.returncode == 2 and data['error_type'] == 'timeout'
    assert data['data_readiness'] == 'unknown'
    assert fingerprint(project) == before


def test_partial_and_unqualified_bundle_time(project):
    db = project / '.olav/databases/main.duckdb'
    with duckdb.connect(str(db)) as c:
        c.execute("INSERT INTO netops.bundle_ingests VALUES ('after',NULL,NULL)")
    p, data = cli(project, '--snapshot-id', 'after')
    assert p.returncode == 0
    assert sources(data)['bundle_ingests']['collection_time']['status'] == 'partial'
    assert data['coverage']['collection'] == 'unknown'
    with duckdb.connect(str(db)) as c:
        c.execute('DROP TABLE netops.bundle_ingests')
        c.execute('CREATE TABLE netops.bundle_ingests(snapshot_id VARCHAR,collected_at VARCHAR)')
        c.execute("INSERT INTO netops.bundle_ingests VALUES ('after','2026-11-17 01:00:00')")
    p, data = cli(project, '--snapshot-id', 'after')
    assert p.returncode == 0
    time = sources(data)['bundle_ingests']['collection_time']
    assert time['status'] == 'unknown' and time['type_qualified'] is False
