"""Compare bounded, historical IOS route observations in one native database."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from math import isfinite
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from . import bounded_export as storage
from .native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from .native_snapshot import NativeSnapshotError, close_native_reader, open_native_snapshot
from .netops.core.platform_canonical import canonicalize_platform

SCHEMA = 'olav.route-diff/v1'
READER_MODULE = 'olav_skills.route_changes'
MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 64 * 1024
MAX_SAMPLE = 24 * 1024
MAX_PARENT_ROWS = 2048
MAX_PARENT_BYTES = 512 * 1024
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 60_000
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_TIMEOUT_MS = 15_000
MAX_PATH_SAMPLES = 12
MAX_CELL_BYTES = 256
MAX_ASNS = 128

VRF = re.compile(r'[a-zA-Z0-9_.:-]{1,128}')
IFACE = re.compile(r'[a-zA-Z][a-zA-Z0-9_./:-]{0,127}')
UNAVAILABLE_NAMES = {'all', 'any', 'unknown'}
ALIASES = {'latest', 'current', 'newest', 'all'}
SOURCE_TYPES = {'snapshot_id': 'VARCHAR', 'device_name': 'VARCHAR', 'command': 'VARCHAR',
                'platform': 'VARCHAR', 'parsed_data': 'JSON'}


@dataclass(frozen=True)
class Profile:
    command: str
    scoped_command: str
    family: int
    bgp: bool = False


PROFILES = {
    'ios_ipv4_rib': Profile('show ip route', 'show ip route vrf ', 4),
    'ios_ipv6_rib': Profile('show ipv6 route', 'show ipv6 route vrf ', 6),
    'ios_ipv4_bgp': Profile('show ip bgp', 'show ip bgp vpnv4 vrf ', 4, bgp=True),
}


class RouteError(Exception):
    """A bounded comparison cannot start or finish."""


Context = tuple[str, str]
Hop = tuple[str | None, str | None, str | None]
PathObservation = tuple[Hop | None, tuple[int, ...] | None]


@dataclass
class Observation:
    parents: set[tuple[str, str]] = field(default_factory=set)
    commands: dict[Context, str] = field(default_factory=dict)
    routes: dict[Context, dict[str, set[PathObservation]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set))
    )
    gaps: dict[tuple[str | None, str | None], set[str]] = field(default_factory=lambda: defaultdict(set))

    def block(self, key, reason):
        self.gaps[key].add(reason)

    def usable(self, device, vrf):
        return (device, vrf) in self.routes and not any(
            key in self.gaps for key in ((None, None), (device, None), (device, vrf))
        )


def _failed(kind):
    return {'schema': SCHEMA, 'status': 'error', 'error_type': kind, 'diff_status': 'unknown',
            'counts': None, 'changes': []}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _no_constants(_value):
    raise ValueError('non-JSON numeric constant')


def _label(value, max_bytes=MAX_CELL_BYTES):
    return (isinstance(value, str) and value and value == value.strip()
            and value.isprintable() and len(value.encode('utf-8')) <= max_bytes)


def _vrf(value):
    return (isinstance(value, str) and VRF.fullmatch(value) is not None
            and value.casefold() not in UNAVAILABLE_NAMES)


def _request(raw):
    if len(raw) > MAX_REQUEST:
        raise RouteError('request_byte_limit')
    try:
        args = json.loads(raw, object_pairs_hook=storage._unambiguous_object, parse_constant=_no_constants)
        required = {'source', 'snapshot_id_1', 'snapshot_id_2'}
        if not isinstance(args, dict) or not required <= args.keys() <= required | {'device', 'vrf', 'limit', 'timeout_ms'}:
            raise ValueError()
        for name in required | ({'device', 'vrf'} & args.keys()):
            if not _label(args[name]):
                raise ValueError()
        sids = args['snapshot_id_1'], args['snapshot_id_2']
        if sids[0] == sids[1] or any(sid.casefold() in ALIASES for sid in sids):
            raise ValueError()
        if 'vrf' in args and not _vrf(args['vrf']):
            raise ValueError()
        for name, default, cap in (('limit', 20, 50), ('timeout_ms', MAX_TIMEOUT_MS, MAX_TIMEOUT_MS)):
            val = args.setdefault(name, default)
            if type(val) is not int or not 1 <= val <= cap:
                raise ValueError()
    except (ValueError, UnicodeError, RecursionError, storage.ExportError) as exc:
        raise RouteError('validation_error') from exc
    if args['source'] not in PROFILES:
        raise RouteError('unsupported_source')
    return args


def _project():
    home = os.environ.get('OLAV_HOME')
    if not _label(home, 2048):
        raise RouteError('invalid_project')
    root = Path(home)
    if root.anchor != '/' or str(root) != home or root == Path('/') or '..' in root.parts:
        raise RouteError('invalid_project')
    return root


def _require_schema(conn):
    fields = tuple(SOURCE_TYPES)
    catalog = "database_name = system.main.current_database() AND schema_name = 'netops' AND table_name = 'parsed_outputs'"
    table = conn.execute(f'SELECT 1 FROM system.main.duckdb_tables() WHERE {catalog} AND NOT internal AND NOT temporary LIMIT 1').fetchone()
    if table is None:
        raise RouteError('unsupported_schema')
    marks = ','.join('?' for _ in fields)
    cursor = conn.execute(f'SELECT column_name, data_type FROM system.main.duckdb_columns() WHERE {catalog} '
                          f'AND column_name IN ({marks}) LIMIT ?', [*fields, len(fields)])
    types = dict(cursor.fetchmany(len(fields)))
    if types != SOURCE_TYPES:
        raise RouteError('unsupported_schema')


def _parents(conn, args, profile):
    # Convert identity predicates to BLOBs to avoid text collation or coercion.
    encode = 'system.main.encode'
    size = f'system.main.octet_length({encode}(CAST(p.parsed_data AS VARCHAR)))'
    if 'vrf' in args:
        scoped_predicate = f'{encode}(p.command) = {encode}(?)'
        scoped_param = profile.scoped_command + args['vrf']
    else:
        scoped_predicate = 'system.main.regexp_full_match(p.command, ?)'
        scoped_param = re.escape(profile.scoped_command) + VRF.pattern
    conditions = [f'{encode}(p.snapshot_id) IN ({encode}(?), {encode}(?))',
                  f'({encode}(p.command) = {encode}(?) OR {scoped_predicate})']
    params = [args['snapshot_id_1'], args['snapshot_id_2'], profile.command, scoped_param]
    if 'device' in args:
        conditions.append(f'{encode}(p.device_name) = {encode}(?)')
        params.append(args['device'])
    where = ' AND '.join(conditions)
    # The SQL projection withholds oversized values before they cross into Python.
    # LIMIT bounds the result even if the table has many matching parent rows.
    return conn.execute(f'''SELECT p.snapshot_id,
        CASE WHEN system.main.octet_length({encode}(p.device_name)) <= {MAX_CELL_BYTES} THEN p.device_name END,
        p.command,
        CASE WHEN system.main.octet_length({encode}(p.platform)) <= {MAX_CELL_BYTES} THEN p.platform END,
        {size} AS payload_bytes,
        CASE WHEN {size} <= ? THEN CAST(p.parsed_data AS VARCHAR) END,
        coalesce(system.main.octet_length({encode}(p.device_name)) > {MAX_CELL_BYTES}, false)
          OR coalesce(system.main.octet_length({encode}(p.platform)) > {MAX_CELL_BYTES}, false)
        FROM netops.parsed_outputs AS p WHERE {where} LIMIT ?''',
        [MAX_PARENT_BYTES, *params, MAX_PARENT_ROWS + 1])


def _prefix(row, profile):
    afi = row.get('afi')
    if afi not in (None, '') and (not isinstance(afi, str) or afi.casefold() != f'ipv{profile.family}'):
        return None
    network = row.get('network')
    if not isinstance(network, str) or len(network) > 128 or '%' in network:
        return None
    if not profile.bgp and profile.family == 4:
        length = row.get('prefix_length')
        if not isinstance(length, str) or not re.fullmatch(r'[0-9]{1,2}', length) or '/' in network:
            return None
        network = f'{network}/{length}'
    if not re.search(r'/[0-9]{1,3}$', network):
        return None
    try:
        prefix = ip_network(network)
    except ValueError:
        return None
    return str(prefix) if prefix.version == profile.family else None


def _path(row, profile):
    raw_ip = row.get('next_hop' if profile.bgp else 'nexthop_ip')
    interface = row.get('nexthop_if') if not profile.bgp else None
    has_resolving_vrf = not profile.bgp and profile.family == 4
    resolving_vrf = row.get('nexthop_vrf') if has_resolving_vrf else None
    if not isinstance(raw_ip, str) or (not profile.bgp and not isinstance(interface, str)):
        return None
    if has_resolving_vrf and resolving_vrf != '' and not _vrf(resolving_vrf):
        return None
    if interface:
        if not IFACE.fullmatch(interface):
            return None
        from netutils.interface import canonical_interface_name
        interface = canonical_interface_name(interface)
    if not raw_ip:
        return (None, interface, resolving_vrf or None) if interface else None
    if len(raw_ip) > 64 or '%' in raw_ip:
        return None
    try:
        address = ip_address(raw_ip)
    except ValueError:
        return None
    if address.version != profile.family or (address.version == 6 and address.is_link_local and not interface):
        return None
    return str(address), interface or None, resolving_vrf or None


def _as_path(value):
    if type(value) is int:
        return (value,) if 0 <= value <= 0xFFFFFFFF else None
    if not isinstance(value, str) or len(value) > 1536 or not value.isascii():
        return None
    terms = value.split()
    if not 1 <= len(terms) <= MAX_ASNS or any(not re.fullmatch(r'[0-9]{1,10}', n) for n in terms):
        return None
    asns = tuple(map(int, terms))
    return asns if max(asns) <= 0xFFFFFFFF else None


def _decode(payload):
    try:
        rows = json.loads(payload, object_pairs_hook=storage._unambiguous_object, parse_constant=_no_constants)
    except (TypeError, ValueError, RecursionError, storage.ExportError):
        return None
    return rows if isinstance(rows, list) and rows else None


def _observe(observation, device, command, platform, rows, args, profile):
    if not _label(device) or command is None:
        observation.block((None, None), 'unknown_parent_identity')
        return
    scoped = command[len(profile.scoped_command):] if command.startswith(profile.scoped_command) else None
    if scoped is not None and not _vrf(scoped):
        # Multi-VRF and wildcard commands are outside the selected profiles.
        return
    target = args.get('vrf')
    if scoped is not None and target is not None and scoped != target:
        return
    parent = (device, scoped)
    identity = (device, command)
    if identity in observation.parents:
        observation.block(parent, 'duplicate_parent')
        return
    observation.parents.add(identity)
    if canonicalize_platform(platform) != 'cisco_ios':
        observation.block(parent, 'unsupported_or_unknown_platform')
        return
    if rows is None:
        observation.block(parent, 'empty_or_invalid_parsed_source')
        return
    for row in rows:
        if not isinstance(row, dict):
            observation.block(parent, 'unsupported_record_shape')
            continue
        observed = row.get('vrf')
        if scoped is not None and observed not in (None, '') and observed != scoped:
            observation.block(parent, 'conflicting_vrf_evidence')
            continue
        vrf = scoped or observed
        if not _vrf(vrf):
            observation.block((device, None), 'unknown_vrf')
            continue
        if target is not None and vrf != target:
            continue
        key = (device, vrf)
        prefix = _prefix(row, profile)
        if prefix is None:
            observation.block(key, 'unknown_prefix_or_afi')
            continue
        prior = observation.commands.setdefault(key, command)
        if command != prior:
            observation.block(key, 'overlapping_parent_commands')
        observation.routes[key][prefix].add((_path(row, profile), _as_path(row.get('as_path')) if profile.bgp else None))


def _read(conn, args, profile):
    _require_schema(conn)
    sids = args['snapshot_id_1'], args['snapshot_id_2']
    sides = {sid: Observation() for sid in sids}
    consumed = record_count = 0
    cursor = _parents(conn, args, profile)
    for index in range(MAX_PARENT_ROWS + 1):
        record = cursor.fetchone()
        if record is None:
            break
        sid, device, command, platform, size, payload, long_label = record
        consumed += size or 0
        if index == MAX_PARENT_ROWS or long_label or (size is not None and size > MAX_PARENT_BYTES) or consumed > MAX_INPUT_BYTES:
            raise RouteError('input_budget_exceeded')
        rows = _decode(payload)
        record_count += len(rows) if rows else 0
        if record_count > MAX_RECORDS:
            raise RouteError('input_budget_exceeded')
        _observe(sides[sid], device, command, platform, rows, args, profile)
    return tuple(sides.values())


def _next_hop_status(old, new):
    if None in old or None in new:
        return 'unknown'
    if old == new:
        return 'unchanged'
    shapes = defaultdict(set)
    for ip, interface, vrf in old | new:
        if ip is not None:
            shapes[ip].add((interface is not None, vrf is not None))
    # Missing optional evidence on a shared IP must not manufacture a shift.
    if any(len(known) > 1 for known in shapes.values()):
        return 'unknown'
    return 'changed'


def _as_path_status(old, new, hop_status, profile):
    if not profile.bgp:
        return 'unavailable'
    if hop_status != 'unchanged' or any(path is None for _, path in old | new):
        return 'unknown'
    return 'unchanged' if old == new else 'changed'


def _hop_sample(hops):
    if None in hops:
        return None
    return [dict(zip(('ip', 'interface', 'resolving_vrf'), hop))
            for hop in sorted(hops, key=_json)[:MAX_PATH_SAMPLES]]


def _as_samples(paths):
    if any(hop is None or asns is None for hop, asns in paths):
        return None
    return [{'next_hop': hop[0], 'as_path': asns}
            for hop, asns in sorted(paths, key=_json)[:MAX_PATH_SAMPLES]]


def _detail(key, profile, prefix, membership, old, new, old_hops, new_hops, hop_status, as_status):
    return {'device_name': key[0], 'vrf': key[1], 'afi': f'ipv{profile.family}', 'prefix': prefix,
            'prefix_status': membership, 'next_hop_status': hop_status, 'as_path_status': as_status,
            'old_next_hops': _hop_sample(old_hops) if old else None,
            'new_next_hops': _hop_sample(new_hops) if new else None,
            'old_as_paths': _as_samples(old) if old and profile.bgp else None,
            'new_as_paths': _as_samples(new) if new and profile.bgp else None,
            'path_samples_more': any(len(paths) > MAX_PATH_SAMPLES for paths in (old, new))}


def _bounded_samples(items, limit):
    selected = []
    used = 2
    for item in items:
        size = len(_json(item)) + bool(selected)
        if len(selected) >= limit or used + size > MAX_SAMPLE:
            break
        selected.append(item)
        used += size
    return selected


def _comparison(left, right, profile, args):
    contexts = left.routes.keys() | right.routes.keys()
    common = [key for key in sorted(contexts) if left.usable(*key) and right.usable(*key)]
    for key in contexts:
        for observation in (left, right):
            if key not in observation.routes and (None, None) not in observation.gaps and (key[0], None) not in observation.gaps and key not in observation.gaps:
                observation.block(key, 'counterpart_context_missing')
    gaps = []
    for label, observation in (('old', left), ('new', right)):
        for (device, vrf), reasons in sorted(observation.gaps.items(), key=lambda item: _json(item[0])):
            gaps.append({'side': label, 'device_name': device, 'vrf': vrf, 'reasons': sorted(reasons)})
    if not contexts and not gaps:
        gaps.append({'side': 'both', 'device_name': args.get('device'), 'vrf': args.get('vrf'),
                     'reasons': ['selected_context_missing']})
    counts = {'prefix_added': 0, 'prefix_removed': 0, 'next_hop_changed': 0,
              'as_path_changed': 0 if profile.bgp else None, 'unchanged': 0,
              'next_hop_unknown': 0, 'as_path_unknown': 0 if profile.bgp else None}
    samples = []
    sample_bytes = 2
    total_changes = 0
    for key in common:
        before, after = left.routes[key], right.routes[key]
        for prefix in sorted(before.keys() | after.keys()):
            old, new = before.get(prefix, set()), after.get(prefix, set())
            old_hops, new_hops = {item[0] for item in old}, {item[0] for item in new}
            membership = 'common'
            if not old or not new:
                membership = 'added' if new else 'removed'
                counts['prefix_' + membership] += 1
                hop_status = 'not_compared'
                as_status = 'not_compared' if profile.bgp else 'unavailable'
            else:
                hop_status = _next_hop_status(old_hops, new_hops)
                as_status = _as_path_status(old, new, hop_status, profile)
                if hop_status != 'unchanged':
                    counts['next_hop_' + hop_status] += 1
                if as_status in ('changed', 'unknown'):
                    counts['as_path_' + as_status] += 1
                if hop_status == 'unchanged' and as_status in ('unchanged', 'unavailable'):
                    counts['unchanged'] += 1
            if membership == 'common' and hop_status != 'changed' and as_status != 'changed':
                continue
            total_changes += 1
            if len(samples) < args['limit'] and sample_bytes < MAX_SAMPLE:
                item = _detail(key, profile, prefix, membership, old, new, old_hops, new_hops, hop_status, as_status)
                sample_bytes += len(_json(item)) + bool(samples)
                if sample_bytes <= MAX_SAMPLE:
                    samples.append(item)
    unknown = bool(gaps or counts['next_hop_unknown'] or counts['as_path_unknown'])
    diff_status = 'observed_changes' if total_changes else ('unknown' if unknown else 'no_observed_changes')
    gap_samples = _bounded_samples(iter(gaps), args['limit'])
    return {'comparison_status': 'unknown' if not common else ('partial' if unknown else 'compared'),
            'diff_status': diff_status, 'comparable_context_count': len(common), 'counts': counts if common else None,
            'change_count': total_changes if common else None, 'changes': samples,
            'changes_more': total_changes > len(samples), 'gap_count': len(gaps), 'gaps': gap_samples,
            'gaps_more': len(gaps) > len(gap_samples)}


def _worker(args, scratch, deadline):
    import resource

    validate_native_reader_identity(READER_MODULE)
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    memory = storage.ADDRESS_SPACE_BYTES if hard == resource.RLIM_INFINITY else min(hard, storage.ADDRESS_SPACE_BYTES)
    resource.setrlimit(resource.RLIMIT_AS, (memory, hard))
    duckdb = bootstrap_native_reader(READER_MODULE)

    project = _project()
    profile = PROFILES[args['source']]
    try:
        with open_native_snapshot(project, scratch, max_bytes=MAX_SOURCE_BYTES, deadline=deadline) as (conn, version):
            conn.execute('BEGIN TRANSACTION')
            left, right = _read(conn, args, profile)
            conn.execute('COMMIT')
            result = _comparison(left, right, profile, args)
    except duckdb.OutOfMemoryException as exc:
        raise RouteError('input_budget_exceeded') from exc
    except duckdb.Error as exc:
        raise RouteError('database_read_failed') from exc
    return {'schema': SCHEMA, 'status': 'success', 'source': 'netops.parsed_outputs',
            'source_version': version,
            'scope': {'profile': args['source'], 'snapshot_id_1': args['snapshot_id_1'],
                      'snapshot_id_2': args['snapshot_id_2'], 'device': args.get('device'),
                      'vrf': args.get('vrf'), 'afi': f'ipv{profile.family}', 'command': profile.command,
                      'scoped_command_prefix': profile.scoped_command, 'coverage': 'unknown',
                      'collection_time': 'unknown', 'live_state': 'unknown'},
            **result}


def compare_routes(raw):
    """Return the historical comparison from a bounded disposable read worker."""
    try:
        args = _request(raw)
        if sys.platform != 'linux':
            raise RouteError('unsupported_platform')
        deadline = time.monotonic() + args['timeout_ms'] / 1000
        with tempfile.TemporaryDirectory(prefix='olav-route-') as scratch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RouteError('deadline_exceeded')
            child = subprocess.run(
                [sys.executable, '-B', '-m', READER_MODULE, '--worker', scratch, str(deadline)],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=remaining, check=False,
            )
        if child.returncode or len(child.stdout) > MAX_RESPONSE:
            raise RouteError('worker_failed')
        return json.loads(child.stdout)
    except RouteError as exc:
        return _failed(str(exc))
    except subprocess.TimeoutExpired:
        return _failed('deadline_exceeded')
    except (ValueError, OSError):
        return _failed('worker_failed')


def _run_worker(raw, scratch, deadline):
    try:
        args = _request(raw)
        deadline = float(deadline)
        if not isfinite(deadline):
            raise RouteError('validation_error')
        deadline = min(deadline, time.monotonic() + args['timeout_ms'] / 1000)
        return _worker(args, Path(scratch), deadline)
    except NativeSnapshotError as exc:
        return _failed(exc.code)
    except (RouteError, NativeReaderBootstrapError) as exc:
        return _failed(str(exc))
    except MemoryError:
        return _failed('input_budget_exceeded')
    except (ValueError, OSError):
        return _failed('worker_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _run_worker(raw, scratch, deadline) if scratch is not None else compare_routes(raw)
    payload = _json(result)
    if len(payload) > MAX_RESPONSE:
        result = _failed('response_byte_limit')
        payload = _json(result)
    if scratch is not None and owns_native_reader(READER_MODULE):
        close_native_reader()
    sys.stdout.buffer.write(payload + b'\n')
    return 1 if scratch is None and result['status'] == 'error' else 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        raise SystemExit(main(scratch=sys.argv[2], deadline=sys.argv[3]))
    raise SystemExit(main())
