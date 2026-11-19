"""Bounded evidence coverage for an explicit device/command manifest."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from . import counter_deltas as common
from .parser_lifecycle import _source_metadata, LifecycleError
from .native_snapshot import NativeSnapshotError, open_native_snapshot, close_native_reader
from .netops.core.query.temporal import time_value, unknown

SCHEMA = 'olav.evidence-readiness/v1'
MAX_REQUEST = 32768
MAX_RESPONSE = 256 * 1024
MAX_RECORD = 512 * 1024
MAX_TOTAL = 16 * 1024 * 1024
TIMEOUT_SECONDS = 15


class EvidenceError(Exception):
    pass


def _failed(code):
    return {'schema': SCHEMA, 'status': 'error', 'readiness': 'unknown',
            'error_type': code, 'summary': None, 'requirements': []}


def _request(raw):
    if len(raw) > MAX_REQUEST:
        raise EvidenceError('request_budget_exceeded')
    args = json.loads(raw.decode('utf-8'), object_pairs_hook=common._object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    if not isinstance(args, dict) or set(args) != {'snapshot_id', 'requirements', 'as_of', 'max_age_seconds'}:
        raise EvidenceError('invalid_request')
    sid = args['snapshot_id']
    if not common._label(sid) or sid.casefold() in common.SID_ALIASES | {'all'}:
        raise EvidenceError('invalid_snapshot_id')
    stamp = time_value(args['as_of'], 'explicit_as_of')
    if stamp['status'] != 'known' or stamp['precision'] != 'timestamp':
        raise EvidenceError('invalid_as_of')
    if type(args['max_age_seconds']) is not int or not 0 <= args['max_age_seconds'] <= 315576000:
        raise EvidenceError('invalid_max_age_seconds')
    requirements = args['requirements']
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 128:
        raise EvidenceError('invalid_requirements')
    seen = set()
    for item in requirements:
        if (not isinstance(item, dict) or set(item) != {'device', 'command'}
                or not common._label(item['device'], 512) or not common._label(item['command'])):
            raise EvidenceError('invalid_requirements')
        key = (item['device'], item['command'])
        if key in seen:
            raise EvidenceError('duplicate_requirement')
        seen.add(key)
    args['as_of'] = stamp['value']
    return args


def _rows(conn, table, args, item, projection):
    return conn.execute(
        'SELECT ' + projection + ' FROM netops.' + table +
        ' WHERE system.main.encode(snapshot_id)=system.main.encode(?)'
        ' AND system.main.encode(device_name)=system.main.encode(?)'
        ' AND system.main.encode(command)=system.main.encode(?) LIMIT 2',
        [args['snapshot_id'], item['device'], item['command']]).fetchall()


def _freshness(capture, args):
    if capture['status'] != 'known' or capture['precision'] != 'timestamp':
        return 'unknown', None, 'collection_clock_' + capture['status'] + '_' + capture['precision']
    # Manifest and device metadata can describe a batch rather than this command.
    if capture['source'] != 'command_header':
        return 'unknown', None, 'command_collection_clock_unproven'
    age = (datetime.fromisoformat(args['as_of']) - datetime.fromisoformat(capture['value'])).total_seconds()
    if age < 0:
        return 'unknown', age, 'future_collection_clock_or_clock_skew'
    return ('fresh' if age <= args['max_age_seconds'] else 'stale'), age, None


def _analyze(conn, version, args, deadline):
    ident = {key: {'VARCHAR'} for key in ('snapshot_id', 'device_name', 'command')}
    raw_cols = common._columns(conn, 'ingest_sources', ident | {'raw_output': {'VARCHAR'}, 'source_json': {'VARCHAR'}}, optional=True)
    parsed_cols = common._columns(conn, 'parsed_outputs', ident | {'parsed_data': {'JSON', 'VARCHAR'}}, optional=True)
    ledger_covered = bool(raw_cols) and conn.execute(
        'SELECT 1 FROM netops.ingest_sources WHERE system.main.encode(snapshot_id)=system.main.encode(?) LIMIT 1',
        [args['snapshot_id']]).fetchone() is not None
    output = []
    total = 0
    for item in args['requirements']:
        if time.monotonic() >= deadline:
            raise EvidenceError('deadline_exceeded')
        out = dict(item, raw_status='unknown', parsed_status='unknown', freshness='unknown',
                   collected_at=unknown(), age_seconds=None, raw_sha256=None,
                   parsed_record_count=None, reasons=[])
        if ledger_covered:
            rows = _rows(conn, 'ingest_sources', args, item,
                'substr(command,1,257), ' + common._when_bounded('raw_output', MAX_RECORD) + ', ' +
                common._when_bounded('source_json', 65536))
            if len(rows) > 1:
                raise EvidenceError('ambiguous_raw_source')
            if not rows:
                out['raw_status'] = 'missing'
                out['reasons'].append('missing_exact_historical_raw')
            else:
                command, raw, meta = rows[0]
                if not isinstance(raw, str) or not isinstance(meta, str) or len(command.encode()) > 256:
                    raise EvidenceError('source_budget_exceeded')
                total += len(raw.encode()) + len(meta.encode())
                if total > MAX_TOTAL:
                    raise EvidenceError('source_budget_exceeded')
                digest = hashlib.sha256(raw.encode()).hexdigest()
                key = {'table': 'netops.ingest_sources', 'snapshot_id': args['snapshot_id'],
                       'device_name': item['device'], 'command': command}
                try:
                    _, capture, _ = _source_metadata(meta, key, digest)
                except (LifecycleError, ValueError, TypeError, RecursionError):
                    raise EvidenceError('invalid_raw_provenance') from None
                out.update(raw_status='verified_nonempty' if raw.strip() else 'empty', raw_sha256=digest,
                           collected_at=capture)
                provenance = json.loads(meta)
                if (('device_name' in provenance and provenance['device_name'] != item['device'])
                        or ('command' in provenance and provenance['command'] != command)):
                    raise EvidenceError('invalid_raw_provenance')
                evidence = provenance.get('capture_evidence')
                if capture['status'] == 'known' and capture['source'] == 'command_header':
                    if (not isinstance(evidence, list) or any(not isinstance(v, dict) for v in evidence)
                            or [v for v in evidence if v.get('source') == 'command_header'] != [capture]
                            or any(v.get('status') in {'invalid', 'partial', 'conflict'}
                                   or (v.get('status') == 'known' and
                                       (v.get('value'), v.get('precision')) != (capture['value'], capture['precision']))
                                   for v in evidence)):
                        capture = unknown('capture_evidence', 'conflict')
                        out['collected_at'] = capture
                fresh, age, reason = _freshness(capture, args)
                out.update(freshness=fresh, age_seconds=age)
                if reason:
                    out['reasons'].append(reason)
        else:
            out['reasons'].append('historical_raw_ledger_unavailable')
        if parsed_cols:
            rows = _rows(conn, 'parsed_outputs', args, item,
                common._when_bounded('CAST(parsed_data AS VARCHAR)', MAX_RECORD) + ', system.main.octet_length(system.main.encode(CAST(parsed_data AS VARCHAR)))')
            if len(rows) > 1:
                raise EvidenceError('ambiguous_parsed_source')
            if not rows:
                out['parsed_status'] = 'missing'
            else:
                value, size = rows[0]
                if size is not None and size > MAX_RECORD:
                    raise EvidenceError('source_budget_exceeded')
                if value is None:
                    # SQL NULL and an oversized payload are deliberately not interpreted as empty.
                    out['reasons'].append('parsed_null_or_over_budget')
                else:
                    total += len(value.encode())
                    if total > MAX_TOTAL:
                        raise EvidenceError('source_budget_exceeded')
                    try:
                        parsed = json.loads(value, object_pairs_hook=common._object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                    except (ValueError, RecursionError):
                        parsed = None
                    if isinstance(parsed, list) and all(isinstance(row, dict) and row for row in parsed):
                        out.update(parsed_status='nonempty' if parsed else 'empty', parsed_record_count=len(parsed))
                    else:
                        out['reasons'].append('unsupported_parsed_record_shape')
        else:
            out['reasons'].append('parsed_history_unavailable')
        known_failure = out['raw_status'] in {'missing', 'empty'} or out['parsed_status'] in {'missing', 'empty'} or out['freshness'] == 'stale'
        ready = out['raw_status'] == 'verified_nonempty' and out['parsed_status'] == 'nonempty' and out['freshness'] == 'fresh'
        out['readiness'] = 'ready' if ready else 'not_ready' if known_failure else 'unknown'
        output.append(out)
    summary = {'requirement_count': len(output)}
    for name in ('ready', 'not_ready', 'unknown'):
        summary[name] = sum(row['readiness'] == name for row in output)
    for key, status in [('raw_status', 'verified_nonempty'), ('parsed_status', 'nonempty'), ('freshness', 'fresh')]:
        summary[{'raw_status': 'raw_verified', 'parsed_status': 'parsed_nonempty', 'freshness': 'fresh'}[key]] = sum(row[key] == status for row in output)
    readiness = 'not_ready' if summary['not_ready'] else 'unknown' if summary['unknown'] else 'ready'
    return {'schema': SCHEMA, 'status': 'success', 'readiness': readiness,
            'effective_request': args, 'source_version': version, 'summary': summary, 'requirements': output,
            'source_scope': 'historical_ledger_exact_label', 'expected_scope': 'explicit_requirements_only',
            'collection_completeness': 'unknown', 'parsing_success': 'unknown',
            'source_authenticity': 'not_certified', 'health_assessment': 'unknown'}


def check_evidence(request):
    try:
        return run_evidence(common._wire(request))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _failed('invalid_request')


def run_evidence(raw):
    try:
        expected = _request(raw)
        if sys.platform != 'linux':
            raise EvidenceError('unsupported_platform')
        project = common._project()
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise EvidenceError('unsupported_backend')
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with tempfile.TemporaryDirectory(prefix='olav-evidence-') as scratch:
            env = {'OLAV_HOME': str(project), 'TMPDIR': scratch,
                   'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                   'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1'}
            child = subprocess.run([sys.executable, '-B', '-P', '-s', '-m',
                'olav_skills.evidence_readiness', '--worker', scratch, str(deadline)],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=max(0, deadline-time.monotonic()), cwd=scratch, env=env, check=False)
        if child.returncode or not child.stdout or len(child.stdout) > MAX_RESPONSE:
            raise EvidenceError('worker_failed')
        result = json.loads(child.stdout, object_pairs_hook=common._object)
        if (not isinstance(result, dict) or result.get('schema') != SCHEMA
                or result.get('status') not in {'success', 'error'}):
            raise EvidenceError('worker_result_invalid')
        if result['status'] == 'success':
            version = result.get('source_version')
            if (result.get('effective_request') != expected
                    or not isinstance(version, dict) or not isinstance(version.get('sha256'), str)
                    or len(version['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in version['sha256'])
                    or result.get('readiness') not in {'ready', 'not_ready', 'unknown'}
                    or not isinstance(result.get('requirements'), list)
                    or len(result['requirements']) != len(expected['requirements'])
                    or any(not isinstance(row, dict)
                           or {k: row.get(k) for k in ('device', 'command')} != requested
                           for row, requested in zip(result['requirements'], expected['requirements']))
                    or not isinstance(result.get('summary'), dict)
                    or result['summary'].get('requirement_count') != len(expected['requirements'])):
                raise EvidenceError('worker_result_invalid')
        elif result.get('readiness') != 'unknown':
            raise EvidenceError('worker_result_invalid')
        return result
    except subprocess.TimeoutExpired:
        return _failed('deadline_exceeded')
    except (EvidenceError, common.CounterDiffError) as exc:
        return _failed(getattr(exc, 'code', str(exc)))
    except (ValueError, UnicodeError, RecursionError):
        return _failed('invalid_request')
    except OSError:
        return _failed('worker_failed')


def _worker(raw, scratch, deadline):
    from .native_reader_bootstrap import (validate_native_reader_identity,
        bootstrap_native_reader, NativeReaderBootstrapError)
    try:
        validate_native_reader_identity('olav_skills.evidence_readiness')
    except NativeReaderBootstrapError:
        return _failed('native_reader_bootstrap_rejected')
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if soft == resource.RLIM_INFINITY or soft > common.ADDRESS_SPACE_BYTES:
        resource.setrlimit(resource.RLIMIT_AS, (common.ADDRESS_SPACE_BYTES, hard))
    try:
        duckdb = bootstrap_native_reader('olav_skills.evidence_readiness')
    except NativeReaderBootstrapError:
        return _failed('native_reader_bootstrap_rejected')
    try:
        args = _request(raw)
        deadline = float(deadline)
        if not math.isfinite(deadline):
            raise EvidenceError('invalid_deadline')
        with open_native_snapshot(common._project(), Path(scratch), max_bytes=256*1024*1024,
                deadline=min(deadline, time.monotonic()+TIMEOUT_SECONDS)) as (conn, version):
            result = _analyze(conn, version, args, deadline)
        return result
    except (NativeSnapshotError, common.CounterDiffError) as exc:
        return _failed(exc.code)
    except EvidenceError as exc:
        return _failed(str(exc))
    except (MemoryError, duckdb.OutOfMemoryException):
        return _failed('memory_budget_exceeded')
    except duckdb.Error:
        return _failed('source_query_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _worker(raw, scratch, deadline) if scratch is not None else run_evidence(raw)
    payload = common._wire(result)
    if len(payload) > MAX_RESPONSE:
        result = _failed('response_budget_exceeded')
        payload = common._wire(result)
    if scratch is not None:
        from .native_reader_bootstrap import owns_native_reader
        if owns_native_reader('olav_skills.evidence_readiness'):
            close_native_reader()
    sys.stdout.buffer.write(payload + b'\n')
    return int(scratch is None and result['status'] == 'error')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        raise SystemExit(main(scratch=sys.argv[2], deadline=sys.argv[3]))
    raise SystemExit(main())
