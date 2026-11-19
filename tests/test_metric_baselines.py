"""Exact recorded-net-counter baselines, with independent arithmetic expectations."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from olav_skills import metric_baselines as baseline

DB = '.olav/databases/main.duckdb'
DEVICE = 'Edge-1'
PORT = 'GigabitEthernet0/1'
HISTORY = ['H0', 'H1', 'H2', 'H3', 'H4', 'H5']
TARGET = ['T0', 'T1']
VALUES = [0, 10, 30, 60, 100, 150, 200, 270]
REQUEST = {'baseline_snapshot_ids': HISTORY, 'target_snapshot_ids': TARGET,
           'device': DEVICE, 'interface': PORT, 'profile': 'input_errors'}


def call(home, args):
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'TMPDIR') if k in os.environ}
    env['OLAV_HOME'] = str(home)
    result = subprocess.run([sys.executable, '-B', '-m', 'olav_skills', 'run', 'analyzer', 'analyze_baseline'],
                            input=json.dumps(args), text=True, capture_output=True, cwd=home,
                            env=env, timeout=40)
    assert result.stdout, (result.returncode, result.stderr)
    return result.returncode, json.loads(result.stdout)


def row(value):
    return {'interface': PORT, **{key: str(value) for key in
        ('input_errors', 'crc', 'output_errors', 'queue_drops', 'queue_output_drops')}}


@pytest.fixture
def project(tmp_path):
    path = tmp_path / DB
    path.parent.mkdir(parents=True)
    with duckdb.connect(str(path)) as conn:
        conn.execute('CREATE SCHEMA netops')
        conn.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR, device_name VARCHAR, '
                     'command VARCHAR, platform VARCHAR, parsed_data JSON, collected_at VARCHAR, captured_at VARCHAR)')
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for i, (sid, value) in enumerate(zip(HISTORY + TARGET, VALUES)):
            conn.execute('INSERT INTO netops.parsed_outputs VALUES (?, ?, ?, ?, ?, ?, NULL)',
                [sid, DEVICE, 'show interfaces', 'cisco_ios', json.dumps([row(value)]),
                 (start + timedelta(seconds=i*10)).isoformat()])
    return tmp_path


def mutate(home, sql, params=()):
    with duckdb.connect(str(home / DB)) as conn:
        conn.execute(sql, params)


@pytest.mark.parametrize('profile,unit', [('input_errors', 'errors/second'), ('crc', 'errors/second'),
    ('output_errors', 'errors/second'), ('queue_drops', 'packets/second'), ('queue_output_drops', 'packets/second')])
def test_complete_exact_rates(project, profile, unit):
    before = hashlib.sha256((project / DB).read_bytes()).hexdigest()
    code, out = call(project, REQUEST | {'profile': profile})
    assert code == 0 and out['analysis_status'] == 'observed', out
    assert out['source_version']['sha256'] == before == hashlib.sha256((project / DB).read_bytes()).hexdigest()
    assert [item['rate'] for item in out['baseline_intervals']] == [1, 2, 3, 4, 5]
    assert out['statistics'] == {'sample_count': 5, 'minimum': 1, 'maximum': 5, 'mean': 3,
        'median': 3, 'p90': 5, 'p95': 5, 'percentile_method': 'nearest_rank', 'elapsed_seconds': 10}
    assert out['target_interval']['rate'] == 7 and out['target_comparison'] == 'above_reference'
    assert out['unit'] == unit
    assert out['health_assessment'] == 'unknown' and out['counter_continuity'] == 'not_certified'
    assert out['unobserved_reset_possible'] is True
    assert out['threshold_suggestion']['approval'] == 'not_approved'


@pytest.mark.parametrize('sql,params', [
    ('UPDATE netops.parsed_outputs SET collected_at=NULL WHERE snapshot_id=?', ['H2']),
    ('UPDATE netops.parsed_outputs SET captured_at=? WHERE snapshot_id=?', ['2026-10-01T00:00:21Z', 'H2']),
    ('UPDATE netops.parsed_outputs SET collected_at=? WHERE snapshot_id=?', ['2026-10-01', 'H2']),
    ('UPDATE netops.parsed_outputs SET collected_at=? WHERE snapshot_id=?', ['2026-10-01T00:00:10Z', 'H2']),
    ('UPDATE netops.parsed_outputs SET collected_at=? WHERE snapshot_id=?', ['2026-10-01T00:00:21Z', 'H2']),
    ('UPDATE netops.parsed_outputs SET parsed_data=? WHERE snapshot_id=?', [json.dumps([row(1)]), 'H3']),
    ('UPDATE netops.parsed_outputs SET platform=? WHERE snapshot_id=?', ['juniper_junos', 'H2']),
    ('DELETE FROM netops.parsed_outputs WHERE snapshot_id=?', ['H2']),
    ('UPDATE netops.parsed_outputs SET parsed_data=? WHERE snapshot_id=?', [json.dumps([{'interface': PORT}]), 'H2']),
])
def test_incomplete_history_never_filtered_into_statistics(project, sql, params):
    mutate(project, sql, params)
    code, out = call(project, REQUEST)
    assert code == 0 and out['baseline_status'] == 'unknown', out
    assert out['statistics'] is None and out['threshold_suggestion'] is None
    assert len(out['baseline_intervals']) == 5


def test_duplicate_source_is_error(project):
    mutate(project, 'INSERT INTO netops.parsed_outputs SELECT * FROM netops.parsed_outputs WHERE snapshot_id=?', ['H2'])
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == 'ambiguous_source', out


def test_duplicate_interface_is_error(project):
    mutate(project, 'UPDATE netops.parsed_outputs SET parsed_data=? WHERE snapshot_id=?',
           [json.dumps([row(10), row(10)]), 'H2'])
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == 'ambiguous_interface_identity', out


def test_insufficient_history_unknown(project):
    code, out = call(project, REQUEST | {'baseline_snapshot_ids': HISTORY[:5]})
    assert code == 0 and out['statistics'] is None
    assert 'insufficient_baseline_intervals' in out['reasons']


def test_target_clock_and_cadence_rejected_without_polluting_history(project):
    mutate(project, 'UPDATE netops.parsed_outputs SET collected_at=? WHERE snapshot_id=?',
           ['2026-10-01T00:00:20Z', 'T0'])
    code, out = call(project, REQUEST)
    assert code == 0 and out['statistics']['mean'] == 3
    assert out['target_comparison'] == 'unknown'
    assert set(out['target_reasons']) == {'target_not_after_baseline', 'target_collection_interval_changed'}


def test_zero_baseline_is_observed(project):
    mutate(project, 'UPDATE netops.parsed_outputs SET parsed_data=?', [json.dumps([row(0)])])
    code, out = call(project, REQUEST)
    assert code == 0 and out['statistics']['p95'] == 0
    assert out['target_comparison'] == 'at_or_below_reference'


@pytest.mark.parametrize('patch', [
    {'profile': 'sql'}, {'profile': []}, {'device': None}, {'interface': ''},
    {'baseline_snapshot_ids': HISTORY + [TARGET[0]]}, {'target_snapshot_ids': ['latest', 'T1']},
    {'target_snapshot_ids': ['ALL', 'T1']}, {'target_snapshot_ids': ['T0', 'T0']}, {'baseline_snapshot_ids': ['H0']},
    {'baseline_snapshot_ids': ['S'+str(i) for i in range(33)]}, {'sql': 'SELECT 1'},
])
def test_fixed_request_validation(project, patch):
    code, out = call(project, REQUEST | patch)
    assert code == 1 and out['status'] == 'error', out


def test_child_environment_and_nonzero_gate(project, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(project))
    monkeypatch.setenv('FORBIDDEN_CREDENTIAL', 'synthetic-marker')
    def fake_run(cmd, **kw):
        assert 'FORBIDDEN_CREDENTIAL' not in kw['env']
        assert cmd[2:5] == ['-P', '-s', '-m']
        assert kw['cwd'] == kw['env']['TMPDIR']
        return subprocess.CompletedProcess(cmd, 7, b'{"status":"success"}')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert baseline.analyze_baseline(REQUEST)['error_type'] == 'worker_failed'


@pytest.mark.parametrize("capture_header", [False, True])
def test_native_importer_stored_clocks_are_not_invented(tmp_path, capture_header):
    # Reuse the public synthetic native bundle builder, with independent expected
    # arithmetic above. Test both genuine command headers and metadata-only clocks.
    from test_counter_deltas import native_bundle
    home = tmp_path / 'project'
    home.mkdir()
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'TMPDIR') if k in os.environ}
    env['OLAV_HOME'] = str(home)
    ids = []
    for i in range(8):
        stamp = f'2026-10-01T00:00:{i:02d}Z'
        bundle = native_bundle(tmp_path / f'bundle{i}', f'fixture{i}', stamp, [i*10]*5, capture_header=capture_header)
        process = subprocess.run([sys.executable, '-B', '-m', 'olav_skills', 'run', 'importer', 'ingest_snapshot'],
            input=json.dumps({'path': str(bundle)}), text=True, capture_output=True, cwd=home, env=env, timeout=40)
        assert process.returncode == 0, (process.stdout, process.stderr)
        ids.append(json.loads(process.stdout)['snapshot_id'])
    request = REQUEST | {'baseline_snapshot_ids': ids[:6], 'target_snapshot_ids': ids[6:]}
    code, unknown = call(home, request)
    assert code == 0, unknown
    if capture_header:
        assert unknown['statistics']['sample_count'] == 5, unknown
        assert unknown['statistics']['mean'] == 10 and unknown['target_interval']['rate'] == 10
    else:
        assert unknown['statistics'] is None, unknown
        assert all(item['rate'] is None for item in unknown['baseline_intervals'])
    # Explicitly synthetic supplementation of actual stored command clocks. No
    # production source is touched and the rate is not inferred from snapshot IDs.
    with duckdb.connect(str(home / DB)) as conn:
        conn.execute('ALTER TABLE netops.parsed_outputs ADD COLUMN collected_at TIMESTAMPTZ')
        for i, sid in enumerate(ids):
            conn.execute('UPDATE netops.parsed_outputs SET collected_at=? WHERE snapshot_id=?',
                         [f'2026-10-01T00:00:{i:02d}Z', sid])
    code, known = call(home, request)
    assert code == 0 and known['statistics']['sample_count'] == 5, known
    assert known['statistics']['mean'] == 10 and known['target_interval']['rate'] == 10


def test_observed_restart_rejects_increasing_history(project):
    mutate(project, 'INSERT INTO netops.parsed_outputs VALUES (?, ?, ?, ?, ?, ?, NULL)',
           ['H3', DEVICE, 'show version', 'cisco_ios',
            json.dumps([{'restarted': '00:00:25 UTC Thu Oct 1 2026'}]), '2026-10-01T00:00:30Z'])
    code, out = call(project, REQUEST)
    assert code == 0 and out['statistics'] is None
    assert out['baseline_intervals'][2]['counter_status'] == 'confirmed_discontinuity'


def test_observed_clear_rejects_increasing_history(project):
    from test_counter_deltas import ledger_row
    with duckdb.connect(str(project / DB)) as conn:
        conn.execute('CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR, device_name VARCHAR, '
                     'command VARCHAR, source_json VARCHAR, raw_output VARCHAR)')
        ledger_row(conn, 'H3', DEVICE, 'Gi0/1 is up, line protocol is up\n'
                   '  Last clearing of "show interface" counters 00:00:02\n', '2026-10-01T00:00:30Z')
    code, out = call(project, REQUEST)
    assert code == 0 and out['statistics'] is None
    assert out['baseline_intervals'][2]['counter_status'] == 'confirmed_discontinuity'


def test_source_record_budget_fail_not_partial_statistics(project):
    payload = [row(0) | {'padding': 'a' * (512 * 1024)}]
    mutate(project, 'UPDATE netops.parsed_outputs SET parsed_data=? WHERE snapshot_id=?',
           [json.dumps(payload), 'H2'])
    code, out = call(project, REQUEST)
    assert code == 1 and out['error_type'] == 'source_budget_exceeded'
    assert out['statistics'] is None


def test_deadline_cleans_parent_scratch(project, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(project))
    scratch = []
    def fake_run(cmd, **kw):
        from pathlib import Path
        scratch.append(Path(kw['cwd']))
        assert scratch[-1].exists()
        raise subprocess.TimeoutExpired(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert baseline.analyze_baseline(REQUEST)['error_type'] == 'deadline_exceeded'
    assert len(scratch) == 1 and not scratch[0].exists()


def test_caller_writer_and_default_connection_preserved(project, monkeypatch):
    monkeypatch.setenv('OLAV_HOME', str(project))
    duckdb.execute('CREATE TABLE baseline_caller_marker(value INTEGER)')
    try:
        with duckdb.connect(str(project / DB)) as writer:
            assert baseline.analyze_baseline(REQUEST)['error_type'] == 'source_locked'
            assert writer.execute('SELECT count(*) FROM netops.parsed_outputs').fetchone() == (8,)
        duckdb.execute('INSERT INTO baseline_caller_marker VALUES (7)')
        assert duckdb.execute('SELECT * FROM baseline_caller_marker').fetchall() == [(7,)]
    finally:
        duckdb.execute('DROP TABLE baseline_caller_marker')
