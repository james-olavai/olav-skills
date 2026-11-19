"""Synthetic actual-stdin checks, with independently stated expected facts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/analyzer/scripts/check_change_plan.py'


@pytest.fixture
def project(tmp_path):
    db = tmp_path / '.olav/databases/main.duckdb'
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as con:
        con.execute('CREATE SCHEMA netops')
        con.execute('CREATE TABLE netops.parsed_outputs(device_name VARCHAR, platform VARCHAR, command VARCHAR, parsed_data JSON, snapshot_id VARCHAR)')
        con.execute('CREATE TABLE netops.devices(hostname VARCHAR, platform VARCHAR, model VARCHAR, ip_address VARCHAR, role VARCHAR, vendor VARCHAR, os_version VARCHAR)')
        con.execute("INSERT INTO netops.devices VALUES ('global-only','cisco_ios','synthetic','192.0.2.1','leaf','Cisco','synthetic')")
        records = [('r1', 'cisco_ios', 'show ip interface brief', [{'IP_ADDRESS': '192.0.2.1', 'PREFIX_LENGTH': '24', 'VRF': 'blue'}, {'IP_ADDRESS': '2001:db8::1/64'}]),
                   ('r2', None, 'show version', []), ('j1', 'juniper_junos', 'show version', [])]
        for host, platform, command, data in records:
            con.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)', [host, platform, command, json.dumps(data), 's1'])
        con.execute("INSERT INTO netops.parsed_outputs VALUES ('historical-only','cisco_ios','show version','[]','s0')")
    return tmp_path


def plan(device='r1', platform='cisco_ios'):
    return {'schema': 'olav.change-plan/v1', 'targets': [{'device': device, 'platform': platform, 'template': 'cisco_ios',
        'steps': ['description planned'], 'rollback': ['description prior'],
        'verification': [{'command': 'show interfaces', 'expected': 'configured description matches'}], 'addresses': []}]}


def invoke(project, value, *, expected_code=0):
    env = dict(os.environ, OLAV_HOME=str(project))
    env.pop('OLAV_SQL_TEMP_DIR', None)
    env['PYTHONPATH'] = str(ROOT / 'src')
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=data, capture_output=True, env=env, timeout=20)
    if os.environ.get('OW164_EVIDENCE_LOG'):
        with open(os.environ['OW164_EVIDENCE_LOG'], 'a') as evidence:
            evidence.write(json.dumps({'command': proc.args, 'stdin': data.decode(errors='replace'), 'exit': proc.returncode, 'stdout': proc.stdout.decode(), 'stderr': proc.stderr.decode()})+'\n')
    assert proc.returncode == expected_code, (proc.returncode, proc.stdout, proc.stderr)
    result = json.loads(proc.stdout)
    assert result['decision'] == 'not_approved'
    assert proc.stderr == b''
    return result


def request(p):
    return {'snapshot_id': 's1', 'plan': p}


def check(result, name):
    return [c for c in result['checks'] if c['check'] == name]


def test_valid_plan_is_only_checks_and_database_unchanged(project):
    db = project / '.olav/databases/main.duckdb'
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = invoke(project, request(plan()))
    assert check(result, 'target')[0]['status'] == 'pass'
    assert check(result, 'platform')[0]['status'] == 'pass'
    assert result['checks_outcome'] == 'unknown'
    assert all(check(result, name)[0]['status'] == 'unknown' for name in ['cli_syntax', 'simulation', 'collection_time', 'address_safety'])
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('device', ['missing', 'global-only', 'historical-only'])
def test_no_inventory_or_other_snapshot_membership(project, device):
    result = invoke(project, request(plan(device)))
    assert check(result, 'target')[0]['status'] == 'fail'
    assert check(result, 'platform')[0]['status'] == 'unknown'


def test_unknown_and_unsupported_platform(project):
    result = invoke(project, request(plan('r2')))
    assert check(result, 'platform')[0]['status'] == 'unknown'
    result = invoke(project, request(plan('j1', 'juniper_junos')))
    assert check(result, 'platform')[0]['status'] == 'pass'
    assert check(result, 'template')[0]['status'] == 'unknown'
    result = invoke(project, request(plan('j1')))
    assert check(result, 'platform')[0]['status'] == 'fail'


def test_missing_rollback_verification_and_placeholders(project):
    p = plan()
    p['targets'][0].update(rollback=[], verification=[{'command': 'show version'}], steps=['copy <IMAGE>'])
    result = invoke(project, request(p))
    assert all(check(result, name)[0]['status'] == 'fail' for name in ['rollback', 'verification', 'placeholders'])


def test_duplicates_overlap_ipv4_ipv6_and_vrf_unknown(project):
    p = plan()
    p['targets'][0]['addresses'] = [{'address': '192.0.2.1/24', 'vrf': 'blue'}, {'address': '2001:db8::2/64'}, {'address': '192.0.2.128/25', 'vrf': 'red'}]
    result = invoke(project, request(p))
    observed = check(result, 'address_overlap')
    assert any(c['evidence']['kind'] == 'duplicate' and c['evidence']['vrf_relation'] == 'same' for c in observed)
    assert any(c['evidence']['address'] == '2001:db8::2' and c['evidence']['vrf_relation'] == 'unknown' for c in observed)
    assert any(c['evidence']['vrf_relation'] == 'different' for c in observed)
    assert check(result, 'address_safety')[0]['status'] == 'unknown'


def test_bare_ip_is_not_subnet_evidence(project):
    db = project / '.olav/databases/main.duckdb'
    with duckdb.connect(str(db)) as con:
        con.execute('UPDATE netops.parsed_outputs SET parsed_data=? WHERE device_name=?', [json.dumps([{'IP_ADDRESS': '198.51.100.1'}]), 'r1'])
    p = plan()
    p['targets'][0]['addresses'] = [{'address': '198.51.100.2/24'}]
    result = invoke(project, request(p))
    assert check(result, 'address_overlap') == []
    assert check(result, 'address_safety')[0]['status'] == 'unknown'


@pytest.mark.parametrize('mode', ['missing', 'schema_drift', 'empty_snapshot', 'truncated'])
def test_source_failure_is_not_empty_safety(project, mode):
    db = project / '.olav/databases/main.duckdb'
    if mode == 'missing':
        db.unlink()
    else:
        with duckdb.connect(str(db)) as con:
            if mode == 'schema_drift':
                con.execute('ALTER TABLE netops.parsed_outputs DROP COLUMN platform')
            elif mode == 'empty_snapshot':
                con.execute('DELETE FROM netops.parsed_outputs')
            else:
                con.execute("INSERT INTO netops.parsed_outputs SELECT 'bulk', 'cisco_ios', 'command'||i, '[]', 's1' FROM range(1002) t(i)")
    result = invoke(project, request(plan()))
    assert check(result, 'source')[0]['status'] == 'unknown'
    assert result['checks_outcome'] != 'pass'


@pytest.mark.parametrize('value', [None, {}, {'snapshot_id': 'latest', 'plan': plan()}, {'snapshot_id': 's1', 'plan': None}, {'snapshot_id': 's1', 'plan': ''}, {'snapshot_id': 's1', 'plan': {'schema': 'olav.change-plan/v1', 'targets': []}}, b'[]', b'{', b'x'*98305])
def test_invalid_input(project, value):
    result = invoke(project, value, expected_code=2)
    assert result['execution_status'] == 'error'


def test_input_budgets(project):
    p = plan()
    p['targets'][0]['steps'] = ['x']*257
    invoke(project, request(p), expected_code=2)
    p = plan()
    p['targets'][0]['steps'] = ['x'*66000]
    invoke(project, request(p), expected_code=2)
    p = plan()
    p['targets'] *= 33
    invoke(project, request(p), expected_code=2)


def test_plan_paths_and_symlink(project, tmp_path):
    directory = project / 'exports/change_plans'
    directory.mkdir(parents=True)
    (directory/'safe.json').write_text(json.dumps(plan()))
    invoke(project, {'snapshot_id':'s1', 'plan_path':'exports/change_plans/safe.json'})
    for name in ['../secrets', '/etc/passwd', '.olav/databases/main.duckdb', 'exports/change_plans/../../../secrets']:
        invoke(project, {'snapshot_id':'s1', 'plan_path':name}, expected_code=2)
    (directory/'link.json').symlink_to(directory/'safe.json')
    invoke(project, {'snapshot_id':'s1', 'plan_path':'exports/change_plans/link.json'}, expected_code=2)


def test_actual_generator_markdown_stays_unreviewed(project):
    env = dict(os.environ, OLAV_HOME=str(project), PYTHONPATH=str(ROOT/'src'))
    args = {'model_pattern':'synthetic', 'output_filename':'synthetic'}
    proc = subprocess.run([sys.executable, str(ROOT/'skills/analyzer/scripts/generate_change_plan.py')], cwd=project,
                          input=json.dumps(args).encode(), capture_output=True, env=env, timeout=20)
    assert proc.returncode == 0, proc.stderr
    path = Path(json.loads(proc.stdout)['file']).relative_to(project)
    result = invoke(project, {'snapshot_id':'s1', 'plan_path':str(path)})
    assert check(result, 'placeholders')[0]['status'] == 'fail'
    assert check(result, 'markdown_structure')[0]['status'] == 'pass'
    assert check(result, 'targets')[0]['status'] == 'unknown'
    assert result['source']['status'] == 'not_read'


def test_query_resource_failure_is_visible(project):
    db = project / '.olav/databases/main.duckdb'
    with duckdb.connect(str(db)) as con:
        con.execute('DROP TABLE netops.parsed_outputs')
        con.execute("CREATE VIEW netops.parsed_outputs AS SELECT 'r1' AS device_name, 'cisco_ios' AS platform, md5(CAST(i AS VARCHAR)) AS command, '[]' AS parsed_data, 's1' AS snapshot_id FROM range(1000000000) t(i)")
    result = invoke(project, request(plan()))
    assert result['source']['status'] == 'error'
    assert result['source']['error_type'] in {'timeout', 'budget_exceeded'}
    assert check(result, 'source')[0]['status'] == 'unknown'


def test_invalid_ip_and_no_meaningful_verification(project):
    p = plan()
    p['targets'][0]['addresses'] = [{'address':'not-an-ip'}]
    p['targets'][0]['verification'] = [{'command':'show version', 'expected':'   '}]
    result = invoke(project, request(p))
    assert check(result, 'address_format')[0]['status'] == 'fail'
    assert check(result, 'verification')[0]['status'] == 'fail'


def set_address_records(project, records):
    with duckdb.connect(str(project / '.olav/databases/main.duckdb')) as con:
        con.execute('DELETE FROM netops.parsed_outputs')
        con.execute('INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)',
                    ['r1', 'cisco_ios', 'show ip interface brief', json.dumps(records), 's1'])


def test_real_native_parser_output_address_observed(project):
    # Exercise the actual parser; do not transform its headers to match checker.
    raw = ROOT / 'examples/synthetic_2host_bundle/devices/R2/show_ip_interface_brief.txt'
    code = ('import json,sys; from olav_skills.netops.tools.textfsm_parse import parse_output; '
            'print(json.dumps(parse_output("cisco_ios", "show ip interface brief", sys.stdin.read())))')
    env = dict(os.environ, OLAV_HOME=str(project), PYTHONPATH=str(ROOT / 'src'))
    proc = subprocess.run([sys.executable, '-c', code], input=raw.read_text(), text=True, capture_output=True,
                          env=env, cwd=project, timeout=20)
    if os.environ.get('OW164_EVIDENCE_LOG'):
        with open(os.environ['OW164_EVIDENCE_LOG'], 'a') as evidence:
            evidence.write(json.dumps({'stage': 'actual_native_parser', 'command': proc.args, 'stdin': raw.read_text(), 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})+'\n')
    assert proc.returncode == 0, proc.stderr
    records = json.loads(proc.stdout)
    assert any(row.get('ip_address') == '10.1.12.2' for row in records)
    set_address_records(project, records)
    p = plan()
    p['targets'][0]['addresses'] = [{'address': '10.1.12.2'}, {'address': '10.1.12.3/24'}]
    db = project / '.olav/databases/main.duckdb'
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = invoke(project, request(p))
    found = check(result, 'address_overlap')
    assert len(found) == 1
    assert found[0]['evidence']['kind'] == 'duplicate'
    assert found[0]['evidence']['address'] == '10.1.12.2'
    assert found[0]['evidence']['vrf_relation'] == 'unknown'
    assert check(result, 'address_source_fields')[0]['evidence']['observed_address_records'] == 4
    assert check(result, 'address_safety')[0]['status'] == 'unknown'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('record', [
    {'ip_address': '192.0.2.1', 'prefix_length': '24', 'vrf': 'blue'},
    {'ip_address': '192.0.2.1/24', 'vrf': 'blue'},
    {'ip_address': '192.0.2.1/24', 'prefix_length': 24, 'vrf': 'blue'},
    {'ip_address': '192.0.2.1', 'IP_ADDRESS': '192.0.2.1', 'prefix_length': '24', 'PREFIX_LENGTH': '24', 'vrf': 'blue', 'VRF': 'blue'},
])
def test_canonical_source_address_fields_and_matching_aliases(project, record):
    set_address_records(project, [record])
    p = plan()
    p['targets'][0]['addresses'] = [{'address': '192.0.2.128/25', 'vrf': 'blue'}]
    result = invoke(project, request(p))
    found = check(result, 'address_overlap')
    assert len(found) == 1 and found[0]['evidence']['kind'] == 'prefix_overlap'
    assert found[0]['evidence']['vrf_relation'] == 'same'
    assert check(result, 'address_safety')[0]['status'] == 'unknown'


@pytest.mark.parametrize('record,reason', [
    ({'ip_address':'192.0.2.1', 'IP_ADDRESS':'192.0.2.2'}, 'conflicting_ip_address_aliases'),
    ({'ip_address':'192.0.2.1', 'prefix_length':'24', 'PREFIX_LENGTH':'25'}, 'conflicting_prefix_length_aliases'),
    ({'ip_address':'192.0.2.1', 'vrf':'blue', 'VRF':'red'}, 'conflicting_vrf_aliases'),
    ({'ip_address':'192.0.2.1/24', 'prefix_length':'25'}, 'inconsistent_embedded_prefix'),
    ({'ip_address':'192.0.2.1', 'prefix_length':'bad'}, 'invalid_prefix_length'),
    ({'ip_address':['192.0.2.1']}, 'missing_or_unsupported_ip_address'),
    ({'Ip_Address':'192.0.2.1'}, 'missing_or_unsupported_ip_address'),
    ({'address':'192.0.2.1'}, 'missing_or_unsupported_ip_address'),
])
def test_conflicting_or_unsupported_source_fields_unknown(project, record, reason):
    set_address_records(project, [record])
    p = plan()
    p['targets'][0]['addresses'] = [{'address':'192.0.2.1/24', 'vrf':'blue'}]
    result = invoke(project, request(p))
    assert check(result, 'address_overlap') == []
    source = check(result, 'address_source_fields')[0]
    assert source['status'] == 'unknown'
    assert source['evidence']['issues'][reason] == 1
    assert check(result, 'address_safety')[0]['status'] == 'unknown'


def test_canonical_ipv6_cidr_source_overlap(project):
    set_address_records(project, [{'ip_address':'2001:db8::1/64', 'vrf':'blue'}])
    p = plan()
    p['targets'][0]['addresses'] = [{'address':'2001:db8::2/64', 'vrf':'blue'}]
    result = invoke(project, request(p))
    found = check(result, 'address_overlap')
    assert len(found) == 1 and found[0]['evidence']['kind'] == 'prefix_overlap'
    assert found[0]['evidence']['vrf_relation'] == 'same'
    assert check(result, 'address_safety')[0]['status'] == 'unknown'
