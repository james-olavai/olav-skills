"""Bounded candidate paths over exact historical neighbor observations."""
from __future__ import annotations
import json
from math import isfinite
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from . import bounded_export as storage
from .native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from .native_snapshot import NativeSnapshotError, close_native_reader, open_native_snapshot
from .neighbor_observations import NeighborError, read_history, _label, _interface, _no_constants

SCHEMA = 'olav.topology-paths/v1'
READER_MODULE = 'olav_skills.topology_paths'
MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_TIMEOUT_MS = 15000
MAX_NODES = 512
MAX_EDGES = 2048
MAX_STEPS = 20000
MAX_SAMPLE_BYTES = 40 * 1024
ALIASES = {'latest', 'current', 'newest', 'all'}
UNKNOWN = {'unknown','none','null','n/a','-'}

class PathError(Exception):
    pass

def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

def _failed(reason):
    return {'schema': SCHEMA, 'status': 'error', 'error_type': reason,
            'path_status': 'unknown', 'paths': [], 'path_count': None,
            'search_complete': False}

def _request(raw):
    if len(raw) > MAX_REQUEST:
        raise PathError('request_byte_limit')
    try:
        args = json.loads(raw, object_pairs_hook=storage._unambiguous_object, parse_constant=_no_constants)
        required = {'snapshot_id', 'source_device', 'destination_device'}
        optional = {'source_interface', 'destination_interface', 'max_hops', 'limit', 'timeout_ms'}
        if not isinstance(args, dict) or not required <= args.keys() <= required | optional:
            raise ValueError()
        if any(not _label(args[k]) or args[k].casefold() in UNKNOWN for k in required) or args['snapshot_id'].casefold() in ALIASES:
            raise ValueError()
        if args['source_device'] == args['destination_device']:
            raise ValueError()
        for name in ('source_interface', 'destination_interface'):
            if name in args:
                normalized = _interface(args[name])
                if normalized is None:
                    raise ValueError()
                args[name] = normalized
        for key, default, cap in [('limit', 20, 50), ('max_hops', 8, 16), ('timeout_ms', MAX_TIMEOUT_MS, MAX_TIMEOUT_MS)]:
            value = args.setdefault(key, default)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError()
    except (ValueError, UnicodeError, RecursionError, storage.ExportError):
        raise PathError('validation_error') from None
    return args

def _project():
    home = os.environ.get('OLAV_HOME')
    if not _label(home, 2048):
        raise PathError('invalid_project')
    path = Path(home)
    if path.anchor != '/' or str(path) != home or path == Path('/') or '..' in path.parts:
        raise PathError('invalid_project')
    return path

def _paths(side, args, deadline):
    """Enumerate device-simple undirected multigraph paths in requested hop scope."""
    source_reasons = set(side['reasons'])
    if any(a[0] == b[0] for a, b in side['links']):
        source_reasons.add('self_device_observation')
    if source_reasons:
        # Conflicting/ambiguous parents must not be unioned into synthetic paths.
        return {'path_status':'unknown', 'paths':[], 'path_count':None,
                'search_complete':False, 'paths_more':None, 'source_complete':False,
                'reasons':sorted(source_reasons),
                'search':{'scope':'observed_graph_within_requested_hops_and_filters',
                          'max_hops':args['max_hops'], 'hop_boundary_reached':None,
                          'steps':0, 'node_count':None, 'edge_count':None},
                'source_observations':{'row_count':side['row_count'], 'parent_count':len(side['parents'])}}
    links = side['links']
    devices = {endpoint[0] for edge in links for endpoint in edge}
    if len(devices) > MAX_NODES or len(links) > MAX_EDGES:
        raise PathError('graph_budget_exceeded')
    adjacency = {device: [] for device in devices}
    reasons = set(side['reasons'])
    for edge, content in sorted(links.items()):
        a, b = edge
        if a[0] == b[0]:
            reasons.add('self_device_observation')
            continue
        for local, remote in ((a,b), (b,a)):
            adjacency[local[0]].append((local,remote,content[0]))
    source, target = args['source_device'], args['destination_device']
    if source not in devices or target not in devices:
        reasons.add('endpoint_not_observed')
    results = []
    used = 2
    steps = 0
    stop_reason = None
    hop_limited = False
    # Iterator frames bound live stack to max_hops; no exponential pending queue.
    frames = [(iter(adjacency.get(source, [])), [], frozenset([source]))]
    while frames:
        if time.monotonic() >= deadline:
            stop_reason = 'enumeration_deadline'
            break
        iterator, path, visited = frames[-1]
        try:
            local, remote, protocols = next(iterator)
        except StopIteration:
            frames.pop()
            continue
        steps += 1
        if steps > MAX_STEPS:
            stop_reason = 'enumeration_step_limit'
            break
        next_device = remote[0]
        if next_device in visited:
            continue
        if not path and args.get('source_interface', local[1]) != local[1]:
            continue
        hop = {'from': {'device':local[0], 'interface':local[1]},
               'to': {'device':remote[0], 'interface':remote[1]},
               'discovery_protocols': list(protocols)}
        candidate = path + [hop]
        if next_device == target:
            if args.get('destination_interface', remote[1]) != remote[1]:
                continue
            row = {'devices': [source] + [h['to']['device'] for h in candidate],
                   'hop_count':len(candidate), 'hops':candidate}
            cost = len(_json(row)) + bool(results)
            if len(results) >= args['limit'] or used + cost > MAX_SAMPLE_BYTES:
                stop_reason = 'path_output_limit'
                break
            results.append(row)
            used += cost
        elif len(candidate) < args['max_hops']:
            frames.append((iter(adjacency.get(next_device, [])), candidate, visited | {next_device}))
        elif any(edge[1][0] not in visited | {next_device} for edge in adjacency.get(next_device, [])):
            hop_limited = True
    if stop_reason:
        reasons.add(stop_reason)
    complete = stop_reason is None
    if not results:
        reasons.add('no_candidate_discovered_not_absence_proof')
    return {'path_status': 'candidates_found' if results else 'unknown',
            'paths': results, 'path_count':len(results) if results else None,
            'search_complete':complete,
            'paths_more': True if stop_reason == 'path_output_limit' else (None if not complete or hop_limited else False),
            'source_complete':False, 'reasons':sorted(reasons),
            'search':{'scope':'observed_graph_within_requested_hops_and_filters', 'max_hops':args['max_hops'], 'hop_boundary_reached':hop_limited,
                      'steps':min(steps,MAX_STEPS), 'node_count':len(devices), 'edge_count':len(links)},
            'source_observations':{'row_count':side['row_count'], 'parent_count':len(side['parents'])}}

def _worker(args, scratch, deadline):
    import resource
    validate_native_reader_identity(READER_MODULE)
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    memory = storage.ADDRESS_SPACE_BYTES if hard == resource.RLIM_INFINITY else min(hard, storage.ADDRESS_SPACE_BYTES)
    resource.setrlimit(resource.RLIMIT_AS, (memory, hard))
    duckdb = bootstrap_native_reader(READER_MODULE)
    try:
        with open_native_snapshot(_project(), scratch, max_bytes=MAX_SOURCE_BYTES, deadline=deadline) as (conn, version):
            conn.execute('BEGIN TRANSACTION')
            side = read_history(conn, [args['snapshot_id']])[args['snapshot_id']]
            conn.execute('COMMIT')
            result = _paths(side, args, deadline)
    except duckdb.OutOfMemoryException:
        raise PathError('input_budget_exceeded') from None
    except duckdb.Error:
        raise PathError('database_read_failed') from None
    return {'schema': SCHEMA, 'status': 'success', 'source': 'netops.parsed_outputs',
            'source_version': version, 'scope': {'snapshot_id': args['snapshot_id'],
            'coverage': 'unknown', 'collection_time': 'unknown', 'live_state': 'unknown',
            'profile': 'historical_ios_cdp_lldp', 'meaning': 'observed_neighbor_graph_candidates_not_forwarding'}, **result}


def inspect_path(raw):
    """Exec a disposable reader; never open the source in the caller process."""
    try:
        args = _request(raw)
        if sys.platform != 'linux':
            raise PathError('unsupported_platform')
        project = _project()
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise PathError('unsupported_backend')
        deadline = time.monotonic() + args['timeout_ms'] / 1000
        with tempfile.TemporaryDirectory(prefix='olav-topology-') as scratch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PathError('deadline_exceeded')
            env = {'OLAV_HOME': str(project), 'TMPDIR': scratch,
                   'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                   'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1'}
            child = subprocess.run([sys.executable, '-B', '-P', '-s', '-m', READER_MODULE, '--worker', scratch, str(deadline)],
                                   input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=remaining, check=False, env=env, cwd=scratch)
        if child.returncode or len(child.stdout) > MAX_RESPONSE:
            raise PathError('worker_failed')
        return json.loads(child.stdout)
    except (PathError, NeighborError) as exc:
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
            raise PathError('validation_error')
        return _worker(args, Path(scratch), min(deadline, time.monotonic() + args['timeout_ms'] / 1000))
    except NativeSnapshotError as exc:
        return _failed(exc.code)
    except (PathError, NeighborError, NativeReaderBootstrapError) as exc:
        return _failed(str(exc))
    except MemoryError:
        return _failed('input_budget_exceeded')
    except (ValueError, OSError):
        return _failed('worker_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _run_worker(raw, scratch, deadline) if scratch is not None else inspect_path(raw)
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
