"""Synthetic raw snapshots exercise the installed-entry-point contract end to end."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import duckdb
import pytest
import yaml


_INTERFACE_HEADER = 'Interface               Admin Link Proto    Local                 Remote\n'
_BGP_HEADER = ('Groups: 1 Peers: 2 Down peers: 1\n'
               'Peer AS InPkt OutPkt OutQ Flaps Last Up/Down State|#Active/Received/Accepted/Damped...\n')
_IOS_INTERFACE = '''Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet1       192.0.2.50      YES NVRAM  up                    up
GigabitEthernet3       unassigned      YES NVRAM  administratively down down
'''
_IOS_BGP_HEADER = '''BGP router identifier 192.0.2.50, local AS number 65001
BGP table version is 1, main routing table version 1

Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
'''


def _bundle(base, *, after, continuations=False):
    from olav_skills.core.ingest.validators import _hash_bundle_contents

    date = '2026-10-02T00:00:00Z' if after else '2026-10-01T00:00:00Z'
    admin = 'down' if after else 'up'
    junos_state = 'Idle (Admin)' if after else 'Establ '
    junos_routes = '' if after else '  inet.0: 1/2/3/0\n'
    continuation_rows = (
        '                                  inet6    2001:0db8:0001::1/64\n'
        '                                           2001:0db8:0001::2/64\n'
        '                                           2001:0db8:0001::2/64\n'
    ) if continuations else ''
    captures = {
        'qa-junos': ('juniper_junos', 'Juniper', '192.0.2.111', {
            'show interfaces terse': _INTERFACE_HEADER + (
                'ge-0/0/0                up    up\n'
                f'ge-0/0/0.0              {admin:<5} up   inet     192.0.2.11/31\n'
                f'{continuation_rows}'
                'ge-0/0/1.0              up    down inet6    2001:0db8:0000::2/64\n'
                'ge-0/0/2.0              up    up   aenet    --> ae0.0\n'
            ),
            'show bgp summary': _BGP_HEADER + (
                '192.0.2.2 64512 0 0 0 0 00:00 Idle (Admin)\n'
                f'192.0.2.1 64512 100 100 0 0 1 01:20 {junos_state}\n{junos_routes}'
            ),
        }),
        'qa-ios': ('cisco_ios', 'Cisco', '192.0.2.112', {
            'show ip interface brief': _IOS_INTERFACE,
            'show ip bgp summary': _IOS_BGP_HEADER + (
                f"192.0.2.60 4 65002 10 10 1 0 0 1d10h {3 if after else 0}\n"
                '192.0.2.61 4 65002 10 10 1 0 0 1d10h Active\n'
            ),
        }),
    }
    for host, (platform, vendor, address, commands) in captures.items():
        path = base / 'devices' / host
        path.mkdir(parents=True)
        meta = dict(hostname=host, mgmt_ip=address, platform=platform, vendor=vendor,
                    commands_attempted=len(commands), commands_succeeded=len(commands),
                    commands_failed=0, collected_at=date)
        (path / '_meta.yaml').write_text(yaml.safe_dump(meta), encoding='utf-8')
        for command, output in commands.items():
            body = f'# command: {command}\n# collected_at: {date}\n# pre_scrubbed: true\n\n{output}'
            (path / (command.replace(' ', '_') + '.txt')).write_text(body, encoding='utf-8')
    content_hash, _, _ = _hash_bundle_contents(base)
    manifest = dict(schema_version=1, collector=dict(name='olav-collector', version='0.0.0-fixture'),
                    collected_at=date, workspace_id='native-contract-after' if after else 'native-contract-before',
                    hosts_collected=2,
                    hosts_failed=0, redaction=dict(pre_scrubbed=True, salt_fingerprint='fixture0'),
                    content_sha256=content_hash)
    (base / 'manifest.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
    return base


def _call(home, skill, script, args, *, expected_exit=0):
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'TMPDIR') if k in os.environ}
    env['OLAV_HOME'] = str(home)
    argv = [sys.executable, '-B', '-m', 'olav_skills', 'run', skill, script]
    result = subprocess.run(argv, cwd=home, env=env, input=json.dumps(args),
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == expected_exit, (argv, args, result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    home = tmp_path_factory.mktemp('native-contract')
    snapshots = []
    for label in ('before', 'after'):
        bundle = _bundle(home / label, after=label == 'after')
        result = _call(home, 'importer', 'ingest_snapshot', {'path': str(bundle)})
        assert result['hosts'] == 2 and result['commands'] == 4
        snapshots.append(result['snapshot_id'])
    assert len(set(snapshots)) == 2
    db = home / '.olav/databases/main.duckdb'
    stat = db.stat()
    fingerprint = (hashlib.sha256(db.read_bytes()).hexdigest(), stat.st_mtime_ns)
    yield home, db, *snapshots
    assert (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns) == fingerprint


def test_raw_junos_reaches_native_views(native):
    _, db, old, new = native
    with duckdb.connect(str(db), read_only=True) as conn:
        commands = conn.execute('''SELECT snapshot_id,command,parsed_data FROM netops.parsed_outputs
                                   WHERE device_name='qa-junos' ORDER BY snapshot_id,command''').fetchall()
        assert {(sid, cmd) for sid, cmd, _ in commands} == {
            (sid, cmd) for sid in (old, new) for cmd in ('show bgp summary', 'show interfaces terse')
        }
        peers = conn.execute('''SELECT peer_ip,state FROM netops.v_show_bgp_summary_auto
                                WHERE snapshot_id=? ORDER BY peer_ip''', [new]).fetchall()
        assert peers == [('192.0.2.1', 'Idle'), ('192.0.2.2', 'Idle')]
        interfaces = conn.execute('''SELECT interface,admin_state,link_state,proto,ip_address
                                     FROM netops.v_show_interfaces_terse_auto WHERE snapshot_id=?
                                     ORDER BY interface''', [new]).fetchall()
        assert interfaces == [
            ('ge-0/0/0', 'up', 'up', '', ''),
            ('ge-0/0/0.0', 'down', 'up', 'inet', '192.0.2.11/31'),
            ('ge-0/0/1.0', 'up', 'down', 'inet6', '2001:db8::2/64'),
            ('ge-0/0/2.0', 'up', 'up', 'aenet', ''),
        ]


@pytest.mark.parametrize(('device', 'recipe', 'expected', 'identities'), [
    ('qa-ios', 'interfaces', (2, 1, 1, 0), ['GigabitEthernet3']),
    ('qa-junos', 'interfaces', (4, 2, 2, 0), ['ge-0/0/0.0', 'ge-0/0/1.0']),
    ('qa-ios', 'bgp', (2, 1, 1, 0), ['192.0.2.61']),
    ('qa-junos', 'bgp', (2, 0, 2, 0), ['192.0.2.1', '192.0.2.2']),
])
def test_legacy_batch_and_investigation_share_state(native, device, recipe, expected, identities):
    home, _, _, new = native
    interface = recipe == 'interfaces'
    operation = 'interface_health' if interface else 'bgp_health'
    selector = {'snapshot_id': new, 'devices': [device], 'limit': 8}
    batch = _call(home, 'analyzer', 'query_batch', {'operation': operation, **selector})
    investigation = _call(home, 'analyzer', 'run_investigation', {'recipe': recipe, **selector})['steps'][0]
    fields = ('record_count', 'healthy_count', 'unhealthy_count', 'unknown_count')
    recipe_fields = ('record_count', 'healthy_record_count', 'nonhealthy_record_count', 'unknown_record_count')
    assert tuple(batch['counts'].get(k) for k in fields) == expected
    assert tuple(investigation['counts'][k] for k in recipe_fields) == expected
    identity = 'interface' if interface else 'bgp_neighbor'
    assert [row[identity] for row in batch['rows']] == identities
    assert [row[identity] for row in investigation['rows']] == identities
    if device == 'qa-junos' and interface:
        assert batch['rows'][0]['proto'] == 'inet' and batch['rows'][0]['admin_state'] == 'down'


@pytest.mark.parametrize(('source', 'device', 'chg'), [
    ('interfaces_junos', 'qa-junos', 1),
    ('bgp_junos', 'qa-junos', 1),
    ('bgp_ios', 'qa-ios', 0),
])
def test_compare_actual_historical_parsed_records(native, source, device, chg):
    home, _, old, new = native
    out = _call(home, 'reporter', 'compare_state', {'source': source, 'device': device,
                                                  'snapshot_id_1': old, 'snapshot_id_2': new})
    assert (out['counts']['changed'], out['counts']['added'], out['counts']['removed']) == (chg, 0, 0)
    if source == 'interfaces_junos':
        row, = out['samples']
        assert (row['interface'], row['old_admin_state'], row['new_admin_state']) == ('ge-0/0/0.0', 'up', 'down')
        assert (row['old_status'], row['new_status'], row['old_proto'], row['new_proto']) == ('up', 'up', 'inet', 'inet')
    if source == 'bgp_junos':
        row, = out['samples']
        assert (row['bgp_neighbor'], row['old_state'], row['new_state']) == ('192.0.2.1', 'Established', 'Idle')


def test_real_multifamily_and_repeated_continuations(tmp_path):
    bundle = _bundle(tmp_path / 'capture', after=False, continuations=True)
    snapshot = _call(tmp_path, 'importer', 'ingest_snapshot', {'path': str(bundle)})['snapshot_id']
    db = tmp_path / '.olav/databases/main.duckdb'
    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute("""SELECT proto,ip_address FROM netops.v_show_interfaces_terse_auto
                              WHERE snapshot_id=? AND interface='ge-0/0/0.0'
                              ORDER BY proto,ip_address""", [snapshot]).fetchall()
    assert rows == [('inet', '192.0.2.11/31'), ('inet6', '2001:db8:1::1/64'),
                    ('inet6', '2001:db8:1::2/64'), ('inet6', '2001:db8:1::2/64')]
    selector = {'snapshot_id': snapshot, 'devices': ['qa-junos']}
    batch = _call(tmp_path, 'analyzer', 'query_batch', {'operation': 'interface_health', **selector})
    assert batch['counts'] == {'record_count': 7, 'observed_device_count': 1,
                               'unhealthy_count': 1, 'healthy_count': 6, 'unknown_count': 0}
    assert [row['interface'] for row in batch['rows']] == ['ge-0/0/1.0']
    out = _call(tmp_path, 'reporter', 'compare_state', {
        'source': 'interfaces_junos', 'snapshot_id_1': snapshot, 'snapshot_id_2': snapshot,
    }, expected_exit=1)
    assert out['error_type'] == 'ambiguous_identity' and out['counts'] is None


def test_native_template_trace_and_closure_identity():
    from pathlib import Path
    from olav_skills.netops.core.ingest.closure import parser_identity
    from olav_skills.netops.tools import textfsm_parse

    trace = []
    args = ('junos', 'show interfaces terse', _INTERFACE_HEADER + 'ge-0/0/0 up up\n')
    assert textfsm_parse.parse_output(*args, trace=trace.append)[0]['interface'] == 'ge-0/0/0'
    identity, = trace
    expected = hashlib.sha256(textfsm_parse._JUNOS_TERSE_TEMPLATE.encode()).hexdigest()
    assert (identity['tier'], identity['template_sha256']) == ('package_textfsm', expected)
    module_hash = hashlib.sha256(Path(textfsm_parse.__file__).read_bytes()).hexdigest()
    assert parser_identity()['files']['module/olav_skills.netops.tools.textfsm_parse'] == module_hash


def test_native_parser_rejects_unbound_or_incomplete_input():
    from olav_skills.netops.tools.textfsm_parse import parse_output

    assert parse_output('junos', 'show interfaces terse', _INTERFACE_HEADER + '   inet 192.0.2.1\n') is None
    bad = _INTERFACE_HEADER + 'ge-0/0/0 up up\n  inet6 malformed-address\n'
    assert parse_output('junos', 'show interfaces terse', bad) is None
    oversized = _INTERFACE_HEADER + 'a' * (1024 * 1024)
    assert parse_output('junos', 'show interfaces terse', oversized) is None
    peer = '2001:0DB8::3 64512 0 0 0 0 00:00 Idle (Admin)\n'
    assert parse_output('junos', 'show bgp summary', _BGP_HEADER + peer) is None
    trace = []
    rows = parse_output('junos', 'show bgp summary', _BGP_HEADER + peer + peer, trace=trace.append)
    assert [(row['peer_ip'], row['state']) for row in rows] == [('2001:db8::3', 'Idle')] * 2
    assert trace[0]['tier'] == 'ntc_templates'


def test_legacy_batch_exact_nocase_and_typed_profiles(tmp_path):
    db = tmp_path / '.olav/databases/main.duckdb'
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as conn:
        conn.execute('CREATE SCHEMA netops')
        conn.execute('CREATE TABLE netops.devices(hostname VARCHAR COLLATE NOCASE,vendor VARCHAR COLLATE NOCASE)')
        conn.execute("INSERT INTO netops.devices VALUES ('J1','Juniper'),('j1','juniper')")
        conn.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR COLLATE NOCASE,device_name VARCHAR,command VARCHAR)')
        conn.execute("INSERT INTO netops.parsed_outputs VALUES ('Cap','J1','show bgp summary'),('cap','j1','show bgp summary')")
        conn.execute('CREATE TABLE netops.peers(device_name VARCHAR COLLATE NOCASE,snapshot_id VARCHAR COLLATE NOCASE,peer_ip VARCHAR,state VARCHAR)')
        conn.execute("""INSERT INTO netops.peers VALUES ('J1','Cap','192.0.2.1','7'),
                       ('J1','Cap','192.0.2.2','Idle'),('j1','Cap','192.0.2.3','Idle'),
                       ('J1','cap','192.0.2.4','Idle')""")
        conn.execute('CREATE VIEW netops.v_show_bgp_summary_auto AS SELECT * FROM netops.peers')
    req = {'operation': 'bgp_health', 'snapshot_id': 'Cap', 'devices': ['J1'], 'vendors': ['Juniper']}
    out = _call(tmp_path, 'analyzer', 'query_batch', req)
    assert out['counts'] == {'record_count': 2, 'healthy_count': 0, 'unhealthy_count': 1,
                             'unknown_count': 1, 'observed_device_count': 1}
    assert [row['bgp_neighbor'] for row in out['rows']] == ['192.0.2.2']
    out = _call(tmp_path, 'analyzer', 'query_batch', {**req, 'snapshot_id': 'CAP'}, expected_exit=1)
    assert out['error_type'] == 'source_missing'
    with duckdb.connect(str(db)) as conn:
        conn.execute('CREATE OR REPLACE VIEW netops.v_show_bgp_summary_auto AS SELECT device_name,1 AS snapshot_id,peer_ip,state FROM netops.peers')
    out = _call(tmp_path, 'analyzer', 'query_batch', req, expected_exit=1)
    assert out['error_type'] == 'source_missing'
    assert out['sources'][1]['status'] == 'unsupported_columns_or_not_view'
