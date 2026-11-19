"""Synthetic native route evidence and regression counterexamples."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import ntc_templates
import pytest
import yaml


DB = '.olav/databases/main.duckdb'
BASE = {'source': 'ios_ipv4_rib', 'snapshot_id_1': 'Side-A', 'snapshot_id_2': 'Side-B'}


def invoke(project, request, skill='reporter', script='compare_routes'):
    child = subprocess.run(
        [sys.executable, '-B', '-m', 'olav_skills', 'run', skill, script],
        input=json.dumps(request), text=True, capture_output=True,
        env={**os.environ, 'OLAV_HOME': str(project)}, timeout=45,
    )
    assert child.stdout.strip().startswith('{'), (child.returncode, child.stdout, child.stderr)
    return child, json.loads(child.stdout)


def route(network='192.0.2.0', *, vrf='BLUE', hop='198.51.100.1', interface='Gi0/1'):
    return {'vrf': vrf, 'network': network, 'prefix_length': '24',
            'nexthop_ip': hop, 'nexthop_if': interface, 'nexthop_vrf': ''}


def put(conn, sid, device, rows, command='show ip route', platform='cisco_ios'):
    conn.execute('INSERT INTO netops.parsed_outputs VALUES (?, ?, ?, ?, ?)',
                 [sid, device, command, platform, json.dumps(rows)])


@pytest.fixture
def project(tmp_path):
    db = tmp_path / DB
    db.parent.mkdir(parents=True)
    same = route()
    moved = route(hop='198.51.100.2', interface='Gi0/2')
    extra = route(hop='198.51.100.3', interface='Gi0/3')
    red = route(vrf='RED')
    with duckdb.connect(str(db)) as conn:
        conn.execute('CREATE SCHEMA netops')
        conn.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR, device_name VARCHAR, command VARCHAR, platform VARCHAR, parsed_data JSON)')
        put(conn, 'Side-A', 'R1', [same, moved, same, red, route('203.0.113.0')])
        put(conn, 'Side-B', 'R1', [extra, red, same, route('198.51.100.0')])
        put(conn, 'Side-A', 'r1', [same, moved])
        put(conn, 'Side-B', 'r1', [moved, same, moved])
        put(conn, 'side-a', 'R1', [extra])
        conn.execute("CREATE VIEW netops.v_show_ip_route_auto AS SELECT * FROM netops.parsed_outputs WHERE snapshot_id='Side-B'")
    return tmp_path


def test_exact_historical_ecmp_and_device_vrf_identity(project):
    path = project / DB
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    child, out = invoke(project, BASE)
    assert child.returncode == 0, out
    assert out['scope']['coverage'] == 'unknown'
    assert out['counts'] == {'prefix_added': 1, 'prefix_removed': 1, 'next_hop_changed': 1,
                             'as_path_changed': None, 'unchanged': 2,
                             'next_hop_unknown': 0, 'as_path_unknown': None}
    changed = {tuple(row[k] for k in ('device_name', 'vrf', 'afi', 'prefix')): row for row in out['changes']}
    assert set(changed) == {('R1', 'BLUE', 'ipv4', prefix) for prefix in
                            ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')}
    shift = changed['R1', 'BLUE', 'ipv4', '192.0.2.0/24']
    assert {path['ip'] for path in shift['old_next_hops']} == {'198.51.100.1', '198.51.100.2'}
    assert {path['ip'] for path in shift['new_next_hops']} == {'198.51.100.1', '198.51.100.3'}
    assert shift['as_path_status'] == 'unavailable'
    assert out['source_version']['sha256'] == before == hashlib.sha256(path.read_bytes()).hexdigest()
    only = invoke(project, {**BASE, 'device': 'r1', 'vrf': 'BLUE'})[1]
    assert only['diff_status'] == 'no_observed_changes' and only['counts']['unchanged'] == 1
    wrong = invoke(project, {**BASE, 'device': 'R1', 'vrf': 'blue'})[1]
    assert wrong['diff_status'] == 'unknown' and wrong['counts'] is None
    sample = invoke(project, {**BASE, 'limit': 1})[1]
    assert sample['changes_more'] and sample['counts'] == out['counts'] and len(sample['changes']) == 1


def test_missing_context_null_prefix_and_next_hop(project):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, 'Side-A', 'Only-A', [route()])
        put(conn, 'Side-A', 'Unscoped', [route(vrf=None)])
        put(conn, 'Side-B', 'Unscoped', [route()])
        put(conn, 'Side-A', 'Incomplete', [route(hop=None, interface=None)])
        put(conn, 'Side-B', 'Incomplete', [route()])
        put(conn, 'Side-A', 'Masked', [route()])
        put(conn, 'Side-B', 'Masked', [route(None)])
        put(conn, 'Side-A', 'Conflict-AFI', [route()])
        put(conn, 'Side-B', 'Conflict-AFI', [dict(route(), afi='ipv6')])
        put(conn, 'Side-A', 'Empty', [route()])
        put(conn, 'Side-B', 'Empty', [])
    for device in ('Only-A', 'Unscoped', 'Masked', 'Conflict-AFI', 'Empty'):
        out = invoke(project, {**BASE, 'device': device})[1]
        assert out['diff_status'] == 'unknown' and out['counts'] is None and out['gaps'], out
        assert out['changes'] == []
    out = invoke(project, {**BASE, 'device': 'Incomplete'})[1]
    assert out['counts']['next_hop_unknown'] == 1 and out['counts']['next_hop_changed'] == 0
    assert out['diff_status'] == 'unknown' and out['changes'] == []


def test_ambiguous_native_parent_is_unknown(project):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, 'Side-A', 'R1', [route(hop='198.51.100.3')])
    out = invoke(project, {**BASE, 'device': 'R1'})[1]
    assert out['counts'] is None and out['diff_status'] == 'unknown'
    assert any('duplicate_parent' in gap['reasons'] for gap in out['gaps'])


def test_binary_identity_and_fixed_entity(project):
    with duckdb.connect(str(project / DB)) as conn:
        conn.execute('ALTER TABLE netops.parsed_outputs RENAME TO stored')
        conn.execute('CREATE VIEW netops.parsed_outputs AS SELECT * FROM netops.stored')
    child, out = invoke(project, BASE)
    assert child.returncode == 1 and out['error_type'] == 'unsupported_schema'
    assert out['counts'] is None
    with duckdb.connect(str(project / DB)) as conn:
        conn.execute('DROP VIEW netops.parsed_outputs')
        conn.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR COLLATE NOCASE, device_name VARCHAR COLLATE NOCASE, command VARCHAR COLLATE NOCASE, platform VARCHAR, parsed_data JSON)')
        conn.execute('INSERT INTO netops.parsed_outputs SELECT * FROM netops.stored')
    out = invoke(project, {**BASE, 'device': 'r1'})[1]
    assert out['counts']['unchanged'] == 1 and out['change_count'] == 0
    with duckdb.connect(str(project / DB)) as conn:
        conn.execute('DROP TABLE netops.parsed_outputs')
        conn.execute('CREATE TABLE netops.parsed_outputs AS SELECT * EXCLUDE(snapshot_id), 1 AS snapshot_id FROM netops.stored')
    assert invoke(project, BASE)[1]['error_type'] == 'unsupported_schema'


def test_database_symlink_wal_lock_and_work_budgets(project, monkeypatch):
    db = project / DB
    wal = db.with_suffix('.duckdb.wal')
    wal.touch()
    assert invoke(project, BASE)[1]['error_type'] == 'source_has_wal'
    wal.unlink()
    with duckdb.connect(str(db)):
        assert invoke(project, BASE)[1]['error_type'] == 'source_locked'
    db.rename(db.with_name('source.duckdb'))
    db.symlink_to('source.duckdb')
    assert invoke(project, BASE)[1]['error_type'] == 'source_unavailable'
    db.unlink()
    db.with_name('source.duckdb').rename(db)
    scratch_parent = project / 'temporary-work'
    scratch_parent.mkdir()
    monkeypatch.setenv('TMPDIR', str(scratch_parent))
    assert invoke(project, {**BASE, 'timeout_ms': 1})[1]['error_type'] == 'deadline_exceeded'
    assert list(scratch_parent.iterdir()) == []
    with duckdb.connect(str(db)) as conn:
        put(conn, 'Side-A', 'Large', [{'network': 'x' * (1024 * 1024)}])
    out = invoke(project, BASE)[1]
    assert out['error_type'] == 'input_budget_exceeded' and out['counts'] is None


def test_invalid_selectors(project):
    for extra in ({'snapshot_id_1': 'latest'}, {'snapshot_id_1': 'Side-B'}, {'limit': True},
                  {'device': "R1\0"}, {'sql': 'SELECT 1'}, {'vrf': 'all'}):
        assert invoke(project, {**BASE, **extra})[1]['error_type'] == 'validation_error'


def test_bgp_path_unknown_and_single_context_binding(project):
    cmd = 'show ip bgp vpnv4 vrf BLUE'
    with duckdb.connect(str(project / DB)) as conn:
        examples = {
            'Null-AS': ([bgp_row(None)], [bgp_row('64513')]),
            'Blank-AS': ([bgp_row('')], [bgp_row('64513')]),
            'Complex-AS': ([bgp_row('{64512,64513}')], [bgp_row('64513')]),
            'Same-AS': ([bgp_row(64513)], [bgp_row('64513')]),
            'Missing-Prefix': ([bgp_row('64513'), bgp_row('64514', '')], [bgp_row('64515')]),
            'Conflict': ([bgp_row('64513')], [dict(bgp_row('64515'), vrf='RED')]),
        }
        for device, (before, after) in examples.items():
            put(conn, 'Side-A', device, before, cmd)
            put(conn, 'Side-B', device, after, cmd)
        put(conn, 'Side-A', 'Null-NH', [route(hop=None)])
        put(conn, 'Side-B', 'Null-NH', [route(hop='198.51.100.2')])
    for device in examples:
        out = invoke(project, {**BASE, 'source': 'ios_ipv4_bgp', 'device': device, 'vrf': 'BLUE'})[1]
        assert out['changes'] == [], out
        if device in ('Missing-Prefix', 'Conflict'):
            assert out['counts'] is None and out['gaps']
        elif device == 'Same-AS':
            assert out['counts']['unchanged'] == 1
        else:
            assert out['counts']['as_path_unknown'] == 1 and out['counts']['as_path_changed'] == 0
    null_hop = invoke(project, {**BASE, 'device': 'Null-NH'})[1]
    assert null_hop['counts']['next_hop_unknown'] == 1 and null_hop['changes'] == []


def test_total_input_byte_limit_and_scoped_command(project):
    with duckdb.connect(str(project / DB)) as conn:
        body = {'padding': 'x' * (450 * 1024)}
        for index in range(20):
            put(conn, 'Side-A', f'dev{index}', [body], 'show ip route vrf RED')
    assert invoke(project, BASE)[1]['error_type'] == 'input_budget_exceeded'
    filtered = invoke(project, {**BASE, 'vrf': 'BLUE'})[1]
    assert filtered['counts']['next_hop_changed'] == 1


def ipv6(*ips):
    return 'IPv6 Routing Table - BLUE - 1 entries\nCodes: C - Connected, L - Local\nS   2001:DB8:1::/48 [1/0]\n' + ''.join(
        f'     via {ip}, GigabitEthernet0/{port}\n' for port, ip in enumerate(ips))


def bgp(as_path, prefix='192.0.2.0/24'):
    return ('     Network          Next Hop            Metric LocPrf Weight Path\n'
            'Route Distinguisher: 65000:1 (default for vrf BLUE)\n'
            f'*>  {prefix:16} {"198.51.100.1":15} {"0":>10} {"100":>6} {"0":>6} {as_path} i\n')


def v6_row(ip):
    return {'protocol': 'S', 'network': '2001:DB8:1::/48', 'distance': '1', 'metric': '0',
            'nexthop_ip': ip, 'nexthop_if': 'GigabitEthernet0/0'}


def bgp_row(as_path, prefix='192.0.2.0/24'):
    return {'status': '*', 'path_selection': '>', 'route_source': '', 'network': prefix, 'next_hop': '198.51.100.1',
            'metric': '0', 'local_pref': '100', 'weight': '0', 'as_path': as_path, 'origin': 'i'}


def activate_ntc(project, command, stock, training, samples):
    from olav_skills.parser_lifecycle import manage_parser

    source = Path(ntc_templates.__file__).parent / 'templates' / stock
    args = {'project': str(project), 'platform': 'cisco_ios', 'command': command, 'version': 'v1'}
    manage_parser(**args, operation='create', dsl='textfsm', source=source.read_text(), training_samples=[training])
    validation = manage_parser(**args, operation='validate', holdout=[
        {'raw_output': raw, 'expected_rows': rows} for raw, rows in samples
    ])
    assert validation['candidate']['status'] == 'validated', validation
    assert manage_parser(**args, operation='activate')['active_version'] == 'v1'


def bundle(root, workspace_id, devices):
    root.mkdir()
    manifest = {'schema_version': 1, 'collector': {'name': 'synthetic', 'version': '1', 'invocation': 'pytest'},
                'workspace_id': workspace_id, 'collected_at': '2026-05-15T10:23:30Z', 'collected_by': 'pytest',
                'hosts_collected': len(devices), 'hosts_failed': 0,
                'redaction': {'pre_scrubbed': True, 'salt_fingerprint': 'synthetic', 'netconan_version': '0.13.0'}}
    for name, (platform, commands) in devices.items():
        device = root / 'devices' / name
        device.mkdir(parents=True)
        (device / '_meta.yaml').write_text(yaml.safe_dump({'hostname': name, 'mgmt_ip': '192.0.2.1',
                                                         'platform': platform, 'vendor': 'synthetic'}))
        for n, (command, body) in enumerate(commands.items()):
            (device / f'capture-{n}.txt').write_text(f'# command: {command}\n# pre_scrubbed: true\n\n{body}')
    digest = hashlib.sha256()
    for path in sorted(root.glob('devices/*/*.txt')):
        digest.update(path.read_bytes())
    manifest['content_sha256'] = digest.hexdigest()
    (root / 'manifest.yaml').write_text(yaml.safe_dump(manifest))
    return root


def test_native_parser_source_to_real_reporter(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setenv('OLAV_HOME', str(project))
    v6_command, bgp_command = 'show ipv6 route vrf BLUE', 'show ip bgp vpnv4 vrf BLUE'
    activate_ntc(project, v6_command, 'cisco_ios_show_ipv6_route.textfsm', ipv6('FE80::9'), [
        (ipv6('FE80::1'), [v6_row('fe80::1')]), (ipv6('FE80::2'), [v6_row('fe80::2')]),
    ])
    activate_ntc(project, bgp_command, 'cisco_ios_show_ip_bgp.textfsm', bgp('64520'), [
        (bgp('64513'), [bgp_row(64513)]), (bgp('64513 64514'), [bgp_row('64513 64514')]),
    ])
    sids = []
    for side, new in (('first', False), ('second', True)):
        devices = {
            'Router': ('cisco_ios', {
                'show ip route': 'Routing Table: BLUE\nGateway of last resort is not set\n'
                                 f'O    192.0.2.0/24 [110/2] via 198.51.100.{1 + new}, 01:00:00, GigabitEthernet2\n',
                v6_command: ipv6('FE80::1', 'FE80::3' if new else 'FE80::2'),
                bgp_command: bgp('64513 64514' if new else '64513'),
            }),
            'Unscoped': ('cisco_ios', {'show ipv6 route': ipv6('FE80::7'), 'show ip bgp': bgp('64513')}),
            'Junos': ('juniper_junos', {
                'show route': 'inet.0: 1 destinations, 1 routes\n192.0.2.0/24 *[Static/5] 00:01:00\n'
                              '                    > to 198.51.100.1 via ge-0/0/0.0\n',
                'show route summary': 'Autonomous system number: 65000\nRouter ID: 192.0.2.1\n'
                                      'inet.0: 1 destinations, 1 routes\n    Static: 1 routes, 1 active\n',
            }),
        }
        fixture = bundle(tmp_path / side, side, devices)
        child, ingest = invoke(project, {'path': str(fixture)}, 'importer', 'ingest_snapshot')
        assert child.returncode == 0, (ingest, child.stderr)
        sids.append(ingest['snapshot_id'])
        assert {v6_command, bgp_command, 'show ip route', 'show ipv6 route', 'show ip bgp',
                'show route summary'} <= ingest['parser_fills'].keys()
        assert 'show route' not in ingest['parser_fills']
    assert sids[0] != sids[1]
    db = project / DB
    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute("SELECT snapshot_id, command, parsed_data FROM netops.parsed_outputs WHERE device_name='Router'").fetchall()
    facts = {(sid, command): json.loads(raw) for sid, command, raw in rows}
    assert facts[sids[0], 'show ip route'][0]['vrf'] == 'BLUE'
    assert facts[sids[1], v6_command][1]['nexthop_ip'] == 'fe80::3'
    assert facts[sids[0], bgp_command][0]['as_path'] == 64513
    assert facts[sids[1], bgp_command][0]['as_path'] == '64513 64514'
    request = {'snapshot_id_1': sids[0], 'snapshot_id_2': sids[1], 'device': 'Router', 'vrf': 'BLUE'}
    child, r4 = invoke(project, {**request, 'source': 'ios_ipv4_rib'})
    assert child.returncode == 0 and r4.get('counts') is not None, (r4, child.stderr)
    assert r4['counts']['next_hop_changed'] == 1, r4
    r6 = invoke(project, {**request, 'source': 'ios_ipv6_rib'})[1]
    assert r6['counts']['next_hop_changed'] == 1 and r6['changes'][0]['prefix'] == '2001:db8:1::/48', r6
    rb = invoke(project, {**request, 'source': 'ios_ipv4_bgp'})[1]
    assert rb['counts']['as_path_changed'] == 1 and rb['counts']['next_hop_changed'] == 0, rb
    change = rb['changes'][0]
    assert change['old_as_paths'][0]['as_path'] == [64513]
    assert change['new_as_paths'][0]['as_path'] == [64513, 64514]
    for source in ('ios_ipv6_rib', 'ios_ipv4_bgp'):
        unknown = invoke(project, {**request, 'source': source, 'device': 'Unscoped'})[1]
        assert unknown['counts'] is None and unknown['diff_status'] == 'unknown', unknown
    assert invoke(project, {**request, 'source': 'junos_route', 'device': 'Junos'})[1]['error_type'] == 'unsupported_source'
