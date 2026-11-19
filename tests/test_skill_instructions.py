"""Standalone skill routing and executable contracts, using only public fixtures.

Can be run against an installed wheel from outside the source checkout.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from olav_skills import cli


def call(home, skill, script, payload):
    env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
    env.update(OLAV_HOME=str(home), PYTHONNOUSERSITE='1')
    proc = subprocess.run([sys.executable, '-m', 'olav_skills', 'run', skill, script],
                          input=json.dumps(payload), text=True, capture_output=True,
                          cwd=home, env=env, timeout=120)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


def test_progressive_entrypoints_have_real_local_resources():
    root = cli.resource_root()
    for skill in cli.SKILLS:
        path = root / skill / 'SKILL.md'
        text = path.read_text()
        assert len(text.encode()) < 8192
        assert 'olav-skills run' in text
        # These APIs are unavailable, so no entrypoint may require them.
        for old in ('execute_skill_script', 'format_and_export', 'olav --agent',
                    'olav_recall_memory', 'task(', 'automatically stops'):
            assert old not in text, (path, old)
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' not in target:
                assert (path.parent / target).is_file(), (path, target)


@pytest.fixture(scope='module')
def recorded(tmp_path_factory):
    home = tmp_path_factory.mktemp('skill-instructions')
    # Works with both source and non-editable installed resources.
    root = cli.resource_root()
    bundle = root.parents[1] / 'examples/synthetic_2host_bundle'
    if not bundle.is_dir():
        bundle = Path(__file__).parents[1] / 'examples/synthetic_2host_bundle'
    survey = call(home, 'importer', 'survey_bundle', {'path': str(bundle)})
    assert survey.get('ingest_supported') is True, survey
    valid = call(home, 'importer', 'validate_bundle', {'path': survey['path']})
    assert valid['ok'] is True, valid
    imported = call(home, 'importer', 'ingest_snapshot', {'path': survey['path']})
    assert imported['hosts'] == 2 and imported['commands'] == 6
    return home, imported['snapshot_id']


def test_template_discovery_is_bounded_and_unknown_roles_stay_unknown(recorded):
    home, snapshot = recorded
    rows = call(home, 'analyzer', 'query_batch',
                {'operation': 'devices', 'snapshot_id': snapshot, 'devices': [], 'limit': 1})
    assert rows['status'] == 'success', rows
    # Shape is deliberately asserted from the actual public batch contract.
    assert rows['has_more'] is True, rows
    exact = call(home, 'analyzer', 'execute_sql',
                 {'sql': 'SELECT hostname, role FROM netops.devices ORDER BY hostname'})
    assert [r['hostname'] for r in exact['data']] == ['R1', 'R2']
    assert all(r['role'] is None for r in exact['data'])
    denied = call(home, 'analyzer', 'execute_sql', {'sql': 'DELETE FROM netops.devices'})
    assert denied['status'] == 'requires_approval'
    count = call(home, 'analyzer', 'execute_sql', {'sql': 'SELECT count(*) AS n FROM netops.devices'})
    assert count['data'] == [{'n': 2}]


def test_recorded_coverage_and_parser_negative_are_observable(recorded):
    home, snapshot = recorded
    topology = call(home, 'topology', 'query_topology', {'concept': 'ospf', 'snapshot_id': snapshot})
    assert topology['ospf_adjacencies'] == [] and 'ospf' in topology['absent_layers']
    prepared = call(home, 'learner', 'prepare_learn',
                    {'platform': 'cisco_ios', 'command': 'show sample',
                     'samples': [{'device': 'R1', 'raw_output': 'Interface State\nEth1 up\n'}]})
    assert prepared['status'] == 'ok' and prepared['sample_count'] == 1
    invalid = call(home, 'learner', 'finish_learn',
                   {'platform': 'cisco_ios', 'command': 'show sample',
                    'samples': [{'device': 'R1', 'raw_output': 'Eth1 up'}],
                    'parser_response': 'not a parser'})
    assert invalid['status'] == 'error'
    assert not (home / '.olav/templates/cisco_ios/show_sample.textfsm').exists()


def test_bulk_generator_is_honest_draft_and_keeps_existing_template(recorded):
    home, _ = recorded
    # Controlled synthetic fixture extension solely for the template's declared scope.
    import duckdb
    db = home / '.olav/databases/main.duckdb'
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE netops.devices SET model='SYNTHETIC-IOS' WHERE hostname='R2'")
    result = call(home, 'analyzer', 'generate_change_plan',
                  {'model_pattern': 'SYNTHETIC-IOS', 'output_filename': 'synthetic-draft'})
    assert result['status'] == 'success' and result['device_count'] == 1
    text = Path(result['file']).read_text()
    assert 'Cisco IOS' in text and '<NEW_IOS_IMAGE>' in text
    assert 'role-based stage sorting, not graph BFS' in text
    assert 'No configuration simulation or device execution is provided' in text
    assert 'olav --agent' not in text and '`sim`' not in text
    assert 'Unreviewed draft' in text
