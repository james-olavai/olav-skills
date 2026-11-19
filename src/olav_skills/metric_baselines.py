"""Fixed, recorded IOS counter-rate baselines; no alert configuration writes."""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from . import counter_deltas as counter
from .native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from .native_snapshot import NativeSnapshotError, open_native_snapshot, close_native_reader

SCHEMA = 'olav.metric-baseline/v1'
READER_MODULE = 'olav_skills.metric_baselines'
MAX_REQUEST = 16384
MAX_RESPONSE = 65536
MIN_INTERVALS = 5
MAX_SNAPSHOTS = 32
TIMEOUT_SECONDS = 15


class BaselineError(Exception):
    pass


def _failed(code):
    return {'schema': SCHEMA, 'status': 'error', 'analysis_status': 'unknown',
            'error_type': code, 'statistics': None, 'threshold_suggestion': None}


def _request(raw):
    if len(raw) > MAX_REQUEST:
        raise BaselineError('request_budget_exceeded')
    args = json.loads(raw.decode('utf-8'), object_pairs_hook=counter._object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    keys = {'baseline_snapshot_ids', 'target_snapshot_ids', 'device', 'interface', 'profile'}
    if not isinstance(args, dict) or set(args) != keys:
        raise BaselineError('invalid_request')
    if not isinstance(args['profile'], str) or args['profile'] not in counter.COUNTERS:
        raise BaselineError('unsupported_profile')
    for key in ('device', 'interface'):
        if not counter._label(args[key]):
            raise BaselineError('invalid_request')
    history, target = args['baseline_snapshot_ids'], args['target_snapshot_ids']
    if (not isinstance(history, list) or not 2 <= len(history) <= MAX_SNAPSHOTS
            or not isinstance(target, list) or len(target) != 2):
        raise BaselineError('invalid_snapshot_ids')
    ids = history + target
    if (any(not counter._label(sid) or sid.casefold() in (counter.SID_ALIASES | {'all'}) for sid in ids)
            or len(set(ids)) != len(ids)):
        raise BaselineError('invalid_snapshot_ids')
    return args


def _interval(conn, version, args, pair, deadline):
    request = dict(zip(counter.SID_FIELDS, pair)) | {
        'device': args['device'], 'interface': args['interface'], 'limit': 1}
    result = counter._compare(conn, version, request, deadline)
    sample = result['samples'][0] if len(result['samples']) == 1 else None
    item = {'snapshot_ids': pair, 'rate': None, 'elapsed_seconds': None,
            'collection_time': None, 'status': 'unknown', 'reasons': []}
    counts = result['counts']
    if (result['has_more'] or result['sample_byte_truncated']
            or counts['before_source_rows'] != 1 or counts['after_source_rows'] != 1
            or counts['before_unsupported_source_rows'] != 0 or counts['after_unsupported_source_rows'] != 0
            or counts['before_observed_interfaces'] != 1 or counts['after_observed_interfaces'] != 1
            or counts['before_only_interfaces'] != 0 or counts['after_only_interfaces'] != 0
            or counts['matched_interfaces'] != 1 or result['sample_count'] != 1 or sample is None
            or sample['observation_status'] != 'matched'):
        item['reasons'].append('missing_or_unsupported_exact_interface_source')
        return item
    item['collection_time'] = sample['collection_time']
    item['elapsed_seconds'] = sample['elapsed_seconds']
    metric = sample['counters'][args['profile']]
    item['counter_status'] = metric['delta_status']
    if metric['rate_status'] != 'known':
        item['reasons'].append('unknown_clock_value_or_counter_discontinuity')
        return item
    item.update(status='known', rate=metric['rate'], delta=metric['delta'])
    return item


def _analyze(conn, version, args, deadline):
    history = args['baseline_snapshot_ids']
    intervals = [_interval(conn, version, args, [a, b], deadline) for a, b in zip(history, history[1:])]
    target = _interval(conn, version, args, args['target_snapshot_ids'], deadline)
    reasons = set()
    if len(intervals) < MIN_INTERVALS:
        reasons.add('insufficient_baseline_intervals')
    if any(item['status'] != 'known' for item in intervals):
        reasons.add('incomplete_baseline_evidence')
    # Explicit sequence, rather than lexical SID ordering or sorting away errors.
    known = [item for item in intervals if item['status'] == 'known']
    if known and len({item['elapsed_seconds'] for item in known}) != 1:
        reasons.add('collection_interval_changed')
    stats = None
    suggestion = None
    if not reasons:
        values = sorted(item['rate'] for item in intervals)
        # Every adjacent pair has been consumed, never a bounded preview subset.
        stats = {'sample_count': len(values), 'minimum': values[0], 'maximum': values[-1],
                 'mean': statistics.mean(values), 'median': statistics.median(values),
                 'p90': values[math.ceil(.90 * len(values)) - 1],
                 'p95': values[math.ceil(.95 * len(values)) - 1],
                 'percentile_method': 'nearest_rank', 'elapsed_seconds': known[0]['elapsed_seconds']}
        suggestion = {'kind': 'historical_p95_reference', 'value': stats['p95'],
                      'unit': counter.COUNTERS[args['profile']] + '/second', 'approval': 'not_approved',
                      'automatic_alert_configuration': False}
    comparison = 'unknown'
    target_reasons = []
    if target['status'] != 'known':
        target_reasons.append('incomplete_target_evidence')
    if stats and target['status'] == 'known':
        last = datetime.fromisoformat(intervals[-1]['collection_time']['after']['value'])
        first_target = datetime.fromisoformat(target['collection_time']['before']['value'])
        if first_target <= last:
            target_reasons.append('target_not_after_baseline')
        if target['elapsed_seconds'] != stats['elapsed_seconds']:
            target_reasons.append('target_collection_interval_changed')
        if not target_reasons:
            comparison = 'above_reference' if target['rate'] > stats['p95'] else 'at_or_below_reference'
    return {'schema': SCHEMA, 'status': 'success',
            'analysis_status': 'observed' if stats and comparison != 'unknown' else 'unknown',
            'baseline_status': 'known' if stats else 'unknown',
            'scope': args, 'source_version': version,
            'source': {'table': 'netops.parsed_outputs', 'command': counter.INTERFACES,
                       'platform': counter.IOS},
            'unit': counter.COUNTERS[args['profile']] + '/second',
            'minimum_baseline_intervals': MIN_INTERVALS,
            'baseline_intervals': intervals, 'target_interval': target,
            'statistics': stats, 'threshold_suggestion': suggestion,
            'target_comparison': comparison, 'reasons': sorted(reasons), 'target_reasons': target_reasons,
            'collection_coverage': 'unknown', 'health_assessment': 'unknown',
            'counter_continuity': 'not_certified',
            'metric_semantics': 'recorded_counter_net_increase_per_second',
            'unobserved_reset_possible': True}


def analyze_baseline(request):
    """Analyze only the fixed selected historical native counter-rate profile."""
    try:
        return run_baseline(counter._wire(request))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _failed('invalid_request')


def run_baseline(raw):
    try:
        _request(raw)
        if sys.platform != 'linux':
            raise BaselineError('unsupported_platform')
        project = counter._project()
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise BaselineError('unsupported_backend')
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with tempfile.TemporaryDirectory(prefix='olav-baseline-') as scratch:
            env = {'OLAV_HOME': str(project), 'TMPDIR': scratch,
                   'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                   'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1'}
            child = subprocess.run([sys.executable, '-B', '-P', '-s', '-m',
                READER_MODULE, '--worker', scratch, str(deadline)],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=max(0, deadline-time.monotonic()), cwd=scratch, env=env, check=False)
        if child.returncode or not child.stdout or len(child.stdout) > MAX_RESPONSE:
            raise BaselineError('worker_failed')
        return json.loads(child.stdout)
    except subprocess.TimeoutExpired:
        return _failed('deadline_exceeded')
    except (BaselineError, counter.CounterDiffError) as exc:
        return _failed(getattr(exc, 'code', str(exc)))
    except (ValueError, UnicodeError, RecursionError):
        return _failed('invalid_request')
    except OSError:
        return _failed('worker_failed')


def _worker(raw, scratch, deadline):
    import resource
    try:
        validate_native_reader_identity(READER_MODULE)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if soft == resource.RLIM_INFINITY or soft > counter.ADDRESS_SPACE_BYTES:
            resource.setrlimit(resource.RLIMIT_AS, (counter.ADDRESS_SPACE_BYTES, hard))
        duckdb = bootstrap_native_reader(READER_MODULE)
    except NativeReaderBootstrapError as exc:
        return _failed(str(exc))
    try:
        args = _request(raw)
        deadline = float(deadline)
        if not math.isfinite(deadline):
            raise BaselineError('invalid_deadline')
        with open_native_snapshot(counter._project(), Path(scratch), max_bytes=counter.MAX_DB_BYTES,
                deadline=min(deadline, time.monotonic() + TIMEOUT_SECONDS)) as (conn, version):
            result = _analyze(conn, version, args, deadline)
        return result
    except (counter.CounterDiffError, NativeSnapshotError) as exc:
        return _failed(exc.code)
    except BaselineError as exc:
        return _failed(str(exc))
    except (MemoryError, duckdb.OutOfMemoryException):
        return _failed('memory_budget_exceeded')
    except duckdb.Error:
        return _failed('source_query_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _worker(raw, scratch, deadline) if scratch is not None else run_baseline(raw)
    payload = counter._wire(result)
    if len(payload) > MAX_RESPONSE:
        result = _failed('response_budget_exceeded')
        payload = counter._wire(result)
    if scratch is not None and owns_native_reader(READER_MODULE):
        close_native_reader()
    sys.stdout.buffer.write(payload + b'\n')
    return int(scratch is None and result['status'] == 'error')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        raise SystemExit(main(scratch=sys.argv[2], deadline=sys.argv[3]))
    raise SystemExit(main())
