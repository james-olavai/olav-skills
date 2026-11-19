"""Compare historical parsed neighbor observations or partial current labels.

The default uses exact supported LLDP/CDP history; topology_links is an explicit
partial fallback because its snapshot labels can be overwritten.
"""
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

SCHEMA = 'olav.topology-diff/v1'
READER_MODULE = 'olav_skills.topology_changes'
MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_ROWS = 4096
MAX_INPUT_BYTES = 1024 * 1024
MAX_CELL_BYTES = 256
MAX_SAMPLE_BYTES = 24 * 1024
MAX_TIMEOUT_MS = 15000
IDENTITY = ('source_device', 'source_interface', 'destination_device', 'destination_interface')
CONTENT = ('discovery_protocol', 'link_status', 'link_type', 'link_speed')
ALIASES = {'latest', 'current', 'newest', 'all'}


class TopologyError(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _failed(reason):
    return {'schema': SCHEMA, 'status': 'error', 'error_type': reason,
            'diff_status': 'unknown', 'counts': None, 'changes': []}


def _label(value, cap=MAX_CELL_BYTES):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and value.isprintable() and len(value.encode()) <= cap)


def _no_constants(value):
    raise ValueError('non JSON number')


def _request(raw):
    if len(raw) > MAX_REQUEST:
        raise TopologyError('request_byte_limit')
    try:
        args = json.loads(raw, object_pairs_hook=storage._unambiguous_object, parse_constant=_no_constants)
        required = {'snapshot_id_1', 'snapshot_id_2'}
        if not isinstance(args, dict) or not required <= args.keys() <= required | {'limit', 'timeout_ms', 'source'}:
            raise ValueError()
        if any(not _label(args[k]) or args[k].casefold() in ALIASES for k in required):
            raise ValueError()
        source = args.setdefault('source', 'parsed_neighbors')
        if not isinstance(source, str) or source not in {'parsed_neighbors', 'topology_links'}:
            raise ValueError()
        if args['snapshot_id_1'] == args['snapshot_id_2']:
            raise ValueError()
        for key, default, cap in [('limit', 20, 50), ('timeout_ms', MAX_TIMEOUT_MS, MAX_TIMEOUT_MS)]:
            value = args.setdefault(key, default)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError()
    except (ValueError, UnicodeError, RecursionError, storage.ExportError):
        raise TopologyError('validation_error') from None
    return args


def _project():
    home = os.environ.get('OLAV_HOME')
    if not _label(home, 2048):
        raise TopologyError('invalid_project')
    path = Path(home)
    if path.anchor != '/' or str(path) != home or path == Path('/') or '..' in path.parts:
        raise TopologyError('invalid_project')
    return path


def _columns(conn):
    catalog = "database_name=system.main.current_database() AND schema_name='netops' AND table_name='topology_links'"
    if conn.execute(f'SELECT 1 FROM system.main.duckdb_tables() WHERE {catalog} AND NOT internal AND NOT temporary LIMIT 1').fetchone() is None:
        raise TopologyError('source_table_absent')
    fields = ('snapshot_id', *IDENTITY, *CONTENT)
    marks = ','.join('?' for _ in fields)
    columns = dict(conn.execute(f'SELECT column_name,data_type FROM system.main.duckdb_columns() WHERE {catalog} AND column_name IN ({marks}) LIMIT ?', [*fields, len(fields)]).fetchall())
    if any(columns.get(k) != 'VARCHAR' for k in ('snapshot_id', *IDENTITY)):
        raise TopologyError('unsupported_schema')
    if any(columns[k] != 'VARCHAR' for k in CONTENT if k in columns):
        raise TopologyError('unsupported_schema')
    return columns


def _read_current(conn, args):
    columns = _columns(conn)
    selected = [name for name in (*IDENTITY, *CONTENT) if name in columns]
    encode = 'system.main.encode'
    sizes = [f'coalesce(system.main.octet_length({encode}(t.{name})),0)' for name in selected]
    projections = [f'CASE WHEN {size}<={MAX_CELL_BYTES} THEN t.{name} END' for name, size in zip(selected, sizes)]
    query = (f'SELECT t.snapshot_id,{",".join(projections)}, {"+".join(sizes)}, '
             f'{" OR ".join(size + ">" + str(MAX_CELL_BYTES) for size in sizes)} '
             f'FROM netops.topology_links AS t WHERE {encode}(t.snapshot_id) IN ({encode}(?),{encode}(?)) LIMIT ?')
    cursor = conn.execute(query, [args['snapshot_id_1'], args['snapshot_id_2'], MAX_ROWS + 1])
    sides = {args[k]: {'links': {}, 'reasons': set(), 'row_count': 0} for k in ('snapshot_id_1', 'snapshot_id_2')}
    consumed = 0
    for index in range(MAX_ROWS + 1):
        record = cursor.fetchone()
        if record is None:
            break
        sid, *values, size, oversized = record
        consumed += size
        if index == MAX_ROWS or oversized or consumed > MAX_INPUT_BYTES:
            raise TopologyError('input_budget_exceeded')
        side = sides[sid]
        side['row_count'] += 1
        row = dict(zip(selected, values))
        endpoints = tuple(row[k] for k in IDENTITY)
        if any(not _label(v) or v.casefold() in {'unknown', 'none', 'null', 'n/a', '-'} for v in endpoints):
            side['reasons'].add('unknown_endpoint_identity')
            continue
        # Exact labels are preserved: no hostname guessing or interface alias mapping.
        a, b = endpoints[:2], endpoints[2:]
        if a == b:
            side['reasons'].add('self_endpoint')
            continue
        key = tuple(sorted((a, b)))
        content = tuple(row.get(name) if _label(row.get(name)) and row[name].casefold() not in {'unknown', 'none', 'null', 'n/a', '-'} else None for name in CONTENT)
        if key in side['links']:
            side['reasons'].add('duplicate_or_conflicting_endpoint_observation')
        else:
            side['links'][key] = content
    for side in sides.values():
        if side['row_count'] == 0:
            side['reasons'].add('empty_or_uncollected_snapshot_source')
    return tuple(sides.values())



def _read_history(conn, args):
    from .neighbor_observations import NeighborError, read_history
    try:
        sides = read_history(conn, [args['snapshot_id_1'], args['snapshot_id_2']])
    except NeighborError as exc:
        raise TopologyError(str(exc)) from None
    left, right = (sides[args[key]] for key in ('snapshot_id_1', 'snapshot_id_2'))
    if left['parents'] != right['parents']:
        left['reasons'].add('counterpart_parent_scope_missing')
        right['reasons'].add('counterpart_parent_scope_missing')
    return left, right


def _sample(items, limit):
    result = []
    used = 2
    for item in items:
        cost = len(_json(item)) + bool(result)
        if len(result) >= limit or used + cost > MAX_SAMPLE_BYTES:
            break
        used += cost
        result.append(item)
    return result


def _comparison(left, right, args):
    source_states = []
    for key, side in zip(('snapshot_id_1', 'snapshot_id_2'), (left, right)):
        source_states.append({'snapshot_id': args[key], 'row_count': side['row_count'],
                              'coverage': 'unknown', 'reasons': sorted(side['reasons'])})
    if left['reasons'] or right['reasons']:
        return {'comparison_status': 'unknown', 'diff_status': 'unknown', 'counts': None,
                'change_count': None, 'changes': [], 'changes_more': False, 'sources': source_states}
    before, after = left['links'], right['links']
    counts = dict(observed_added=0, observed_removed=0, content_changed=0, unchanged=0, content_unknown=0)
    changes = []
    unknown = 0
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        membership = 'common' if old is not None and new is not None else ('observed_added' if new is not None else 'observed_removed')
        states = {name: ('not_compared' if membership != 'common' else
                         'unknown' if old[i] is None or new[i] is None else
                         'unchanged' if old[i] == new[i] else 'changed') for i, name in enumerate(CONTENT)}
        changed = 'changed' in states.values()
        incomplete = 'unknown' in states.values()
        if membership != 'common':
            counts[membership] += 1
        else:
            if incomplete:
                counts['content_unknown'] += 1
                unknown += 1
            if changed:
                counts['content_changed'] += 1
            elif not incomplete:
                counts['unchanged'] += 1
        if membership != 'common' or changed or incomplete:
            changes.append({'endpoints': [{'device': p[0], 'interface': p[1]} for p in key],
                            'membership_status': membership, 'field_status': states,
                            'old': dict(zip(CONTENT, old)) if old is not None else None,
                            'new': dict(zip(CONTENT, new)) if new is not None else None})
    actual_changes = counts['observed_added'] + counts['observed_removed'] + counts['content_changed']
    samples = _sample(changes, args['limit'])
    return {'comparison_status': 'partial' if unknown or args['source']=='topology_links' else 'compared',
            'diff_status': 'observed_changes' if actual_changes else ('unknown' if unknown else 'no_observed_changes'),
            'counts': counts, 'change_count': actual_changes, 'changes': samples,
            'changes_more': len(changes) > len(samples), 'sources': source_states}


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
            left, right = (_read_history(conn, args) if args['source']=='parsed_neighbors' else _read_current(conn, args))
            conn.execute('COMMIT')
            result = _comparison(left, right, args)
    except duckdb.OutOfMemoryException:
        raise TopologyError('input_budget_exceeded') from None
    except duckdb.Error:
        raise TopologyError('database_read_failed') from None
    return {'schema': SCHEMA, 'status': 'success', 'source': 'netops.parsed_outputs' if args['source']=='parsed_neighbors' else 'netops.topology_links',
            'source_version': version, 'scope': {'snapshot_id_1': args['snapshot_id_1'],
            'snapshot_id_2': args['snapshot_id_2'], 'coverage': 'unknown', 'collection_time': 'unknown',
            'live_state': 'unknown', 'profile': args['source'], 'source_scope': 'supported_historical_parsed_neighbors' if args['source']=='parsed_neighbors' else 'stored_snapshot_labels_not_historical_reconstruction'}, **result}


def compare_topology(raw):
    """Exec a disposable reader; never open the source in the caller process."""
    try:
        args = _request(raw)
        if sys.platform != 'linux':
            raise TopologyError('unsupported_platform')
        project = _project()
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise TopologyError('unsupported_backend')
        deadline = time.monotonic() + args['timeout_ms'] / 1000
        with tempfile.TemporaryDirectory(prefix='olav-topology-') as scratch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TopologyError('deadline_exceeded')
            env = {'OLAV_HOME': str(project), 'TMPDIR': scratch,
                   'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                   'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1'}
            child = subprocess.run([sys.executable, '-B', '-P', '-s', '-m', READER_MODULE, '--worker', scratch, str(deadline)],
                                   input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=remaining, check=False, env=env, cwd=scratch)
        if child.returncode or len(child.stdout) > MAX_RESPONSE:
            raise TopologyError('worker_failed')
        return json.loads(child.stdout)
    except TopologyError as exc:
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
            raise TopologyError('validation_error')
        return _worker(args, Path(scratch), min(deadline, time.monotonic() + args['timeout_ms'] / 1000))
    except NativeSnapshotError as exc:
        return _failed(exc.code)
    except (TopologyError, NativeReaderBootstrapError) as exc:
        return _failed(str(exc))
    except MemoryError:
        return _failed('input_budget_exceeded')
    except (ValueError, OSError):
        return _failed('worker_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _run_worker(raw, scratch, deadline) if scratch is not None else compare_topology(raw)
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
