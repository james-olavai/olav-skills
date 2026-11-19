"""Native layer scope through the real stdin-JSON CLI, synthetic data only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import pytest
import yaml


def run(home, payload, skill='topology', script='query_topology'):
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'PYTHONPATH', 'TMPDIR') if k in os.environ}
    env.update(OLAV_HOME=str(home), PYTHONNOUSERSITE='1')
    return subprocess.run([sys.executable, '-B', '-m', 'olav_skills', 'run',
                           skill, script], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=home, env=env, timeout=30)


def result(home, payload):
    proc = run(home, payload)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


@pytest.fixture
def recorded(tmp_path):
    db = tmp_path / '.olav/databases/main.duckdb'
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as con:
        con.execute('CREATE SCHEMA netops')
        con.execute("""CREATE VIEW netops.v_l2_links_auto AS SELECT
            'capture-a' AS snapshot_id, 'edge-west' AS source_device,
            'Ethernet1' AS source_interface, 'edge-east' AS destination_device,
            'Ethernet2' AS destination_interface, 'LLDP' AS discovery_protocol,
            'up' AS link_status""")
        con.execute("""CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT
            'capture-a' AS snapshot_id, 'edge-west' AS device_name,
            '192.0.2.9' AS bgp_neighbor, 64513 AS neighbor_as,
            64512 AS local_as, '192.0.2.1' AS router_id,
            '3' AS state_or_prefixes_received""")
    return tmp_path


def assert_contract(out, concept, requested, snapshot):
    c = out['result_contract']
    assert c['schema'] == 'olav.topology-result/v1'
    assert c['backend'] == 'native'
    assert c['effective_concept'] == concept
    assert c['requested_layers'] == requested
    assert c['resolved_snapshot_id'] == snapshot
    fields = {'bgp': 'bgp_sessions', 'ospf': 'ospf_adjacencies', 'l2': 'l2_links'}
    assert set(c['layers']) == set(fields)
    for layer, key in fields.items():
        state = c['layers'][layer]
        assert state['requested'] == (layer in requested)
        assert state['collection_coverage'] == 'unknown'
        if state['result_state'] in ('rows', 'empty'):
            assert state['source_view'] == 'available'
            assert state['row_count'] == len(out[key])
            assert (state['row_count'] > 0) == (state['result_state'] == 'rows')
        else:
            assert state['row_count'] is None
            assert out[key] == []
    return c['layers']


def test_l2_does_not_report_unrequested_layers_as_empty(recorded):
    out = result(recorded, {'concept': 'l2', 'snapshot_id': 'capture-a'})
    layers = assert_contract(out, 'l2', ['l2'], 'capture-a')
    for layer in ('bgp', 'ospf'):
        assert layers[layer] == {'requested': False, 'source_view': 'not_checked',
                                 'result_state': 'not_requested', 'row_count': None,
                                 'collection_coverage': 'unknown'}
    assert layers['l2']['result_state'] == 'rows'
    assert out['absent_layers'] == []
    assert out['l2_links'][0]['source_device'] == 'edge-west'


@pytest.mark.parametrize('payload', [{}, {'concept': 'all', 'snapshot_id': 'capture-a'}])
def test_default_all_reports_rows_and_missing_view_separately(recorded, payload):
    out = result(recorded, payload)
    layers = assert_contract(out, 'all', ['bgp', 'ospf', 'l2'], 'capture-a')
    assert layers['bgp']['row_count'] == 1
    assert out['bgp_sessions'][0]['state'] == 'Established'
    assert layers['ospf'] == {'requested': True, 'source_view': 'absent',
                             'result_state': 'unavailable', 'row_count': None,
                             'collection_coverage': 'unknown', 'reason': 'source_view_absent'}
    assert out['absent_layers'] == ['ospf']


def test_global_view_presence_does_not_prove_collection_in_empty_snapshot(recorded):
    out = result(recorded, {'concept': 'all', 'snapshot_id': 'capture-new-empty'})
    layers = assert_contract(out, 'all', ['bgp', 'ospf', 'l2'], 'capture-new-empty')
    for layer in ('bgp', 'l2'):
        assert layers[layer] == {'requested': True, 'source_view': 'available',
                                'result_state': 'empty', 'row_count': 0,
                                'collection_coverage': 'unknown'}
    assert layers['ospf']['reason'] == 'source_view_absent'


@pytest.mark.parametrize('concept,requested', [('all', ['bgp', 'ospf', 'l2']), ('l2', ['l2'])])
def test_no_snapshot_keeps_complete_contract_without_view_claims(tmp_path, concept, requested):
    db = tmp_path / '.olav/databases/main.duckdb'
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as con:
        con.execute('CREATE SCHEMA netops')
    out = result(tmp_path, {'concept': concept})
    assert out['snapshot_id'] == 'none' and out['absent_layers'] == []
    layers = assert_contract(out, concept, requested, None)
    for layer in requested:
        assert layers[layer] == {'requested': True, 'source_view': 'not_checked',
                                'result_state': 'unavailable', 'row_count': None,
                                'collection_coverage': 'unknown', 'reason': 'snapshot_unavailable'}


@pytest.mark.parametrize('concept', ['custom', '', None, [], 'L2'])
def test_invalid_concept_is_rejected_without_success_empty_result(recorded, concept):
    proc = run(recorded, {'concept': concept, 'snapshot_id': 'capture-a'})
    assert proc.returncode != 0 and proc.stdout == ''
    assert 'concept must be one of' in proc.stderr


def test_source_error_does_not_publish_success_contract(recorded):
    with duckdb.connect(str(recorded / '.olav/databases/main.duckdb')) as con:
        con.execute('DROP VIEW netops.v_show_ip_bgp_summary_auto')
        con.execute('CREATE VIEW netops.v_show_ip_bgp_summary_auto AS SELECT 1 AS wrong_column')
    # Unrequested BGP is not queried/validated simply to answer L2.
    out = result(recorded, {'concept': 'l2', 'snapshot_id': 'capture-a'})
    assert out['result_contract']['layers']['bgp']['source_view'] == 'not_checked'
    proc = run(recorded, {'concept': 'bgp', 'snapshot_id': 'capture-a'})
    assert proc.returncode != 0 and proc.stdout == ''


def test_stock_ospf_view_preserves_observed_phases_and_roles(tmp_path):
    from olav_skills.core.ingest.validators import _hash_bundle_contents

    bundle = tmp_path / 'bundle'
    fixture = Path(__file__).parent / 'fixtures/synthetic_2host_bundle'
    shutil.copytree(fixture, bundle)
    host = bundle / 'devices/R2'
    metadata = host / '_meta.yaml'
    device = yaml.safe_load(metadata.read_text())
    device['commands_attempted'] += 1
    device['commands_succeeded'] += 1
    metadata.write_text(yaml.safe_dump(device))
    (host / 'show_ip_ospf_neighbor.txt').write_text(
        '# command: show ip ospf neighbor\n# pre_scrubbed: true\n\n'
        'Neighbor ID     Pri   State           Dead Time   Address         Interface\n'
        '2.2.2.2           1   FULL/DR         00:00:33    192.0.2.2       GigabitEthernet0/1\n'
        '3.3.3.3           1   2WAY/DROTHER    00:00:34    192.0.2.3       GigabitEthernet0/1\n'
        '4.4.4.4           1   EXSTART/DR      00:00:35    192.0.2.4       GigabitEthernet0/1\n'
        '5.5.5.5           0   INIT/-          00:00:36    192.0.2.5       GigabitEthernet0/1\n'
    )
    manifest = bundle / 'manifest.yaml'
    data = yaml.safe_load(manifest.read_text())
    data['content_sha256'] = _hash_bundle_contents(bundle)[0]
    manifest.write_text(yaml.safe_dump(data))
    project = tmp_path / 'project'
    project.mkdir()

    imported = run(project, {'path': str(bundle)}, 'importer', 'ingest_snapshot')
    assert imported.returncode == 0, (imported.stdout, imported.stderr)
    sid = json.loads(imported.stdout)['snapshot_id']
    topology = result(project, {'concept': 'ospf', 'snapshot_id': sid})
    layers = assert_contract(topology, 'ospf', ['ospf'], sid)
    assert layers['ospf']['row_count'] == 4
    assert {row['neighbor_id']: row['state'] for row in topology['ospf_adjacencies']} == {
        '2.2.2.2': 'FULL/DR', '3.3.3.3': '2WAY/DROTHER',
        '4.4.4.4': 'EXSTART/DR', '5.5.5.5': 'INIT/-',
    }

    observed = run(project, {'snapshot_id': sid, 'devices': ['R2']}, 'analyzer', 'analyze_ospf')
    assert observed.returncode == 0, (observed.stdout, observed.stderr)
    analysis = json.loads(observed.stdout)
    assert analysis['health'] == 'unknown'
    assert [(row['classification'], row['neighbor_role']) for row in analysis['observations']] == [
        ('full_observed', 'DR'), ('two_way_unknown', 'DROTHER'),
        ('transitional_observed', 'DR'), ('transitional_observed', '-'),
    ]


@pytest.mark.parametrize('state', ['mystery', 'FULL/UNKNOWN'])
def test_ospf_view_rejects_unsupported_state_or_role(recorded, state):
    with duckdb.connect(str(recorded / '.olav/databases/main.duckdb')) as con:
        con.execute(f"""CREATE VIEW netops.v_show_ip_ospf_neighbor_auto AS SELECT
            'capture-a' AS snapshot_id, 'edge-west' AS device_name,
            '2.2.2.2' AS neighbor_id, '192.0.2.2' AS ip_address,
            'GigabitEthernet0/1' AS interface, '{state}' AS state, '00:00:01' AS dead_time""")
    proc = run(recorded, {'concept': 'ospf', 'snapshot_id': 'capture-a'})
    assert proc.returncode != 0 and proc.stdout == ''
    assert 'validation error for OSPFAdjacency' in proc.stderr
