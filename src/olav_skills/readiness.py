"""Explicit, bounded read-only capability inspection of one native project.

No platform/config imports: importing this module never selects or creates data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

SCHEMA = 'olav.data-readiness/v1'
TIMEOUT_MS = 5000
MAX_BYTES = 24000
MAX_CELL = 256
MAX_COLUMNS = 32
MAX_SNAPSHOTS = 20
MEMORY = '128MB'
# Fixed catalog only; names never come from caller input or database values.
SOURCES = {
    'devices': ('hostname',),
    'parsed_outputs': ('snapshot_id', 'device_name', 'command'),
    'raw_output_store': ('device_name', 'command'),
    'topology_links': ('snapshot_id', 'source_device', 'destination_device'),
    'bundle_ingests': ('snapshot_id', 'collected_at'),
    'v_show_ip_interface_brief_auto': ('snapshot_id', 'device_name', 'interface'),
    'v_show_interfaces_terse_auto': ('snapshot_id', 'device_name', 'interface'),
    'v_show_ip_bgp_summary_auto': ('snapshot_id', 'device_name', 'bgp_neighbor'),
    'v_show_bgp_summary_auto': ('snapshot_id', 'device_name', 'bgp_neighbor'),
}
LIMITS = {'timeout_ms': TIMEOUT_MS, 'memory': MEMORY, 'threads': 1,
          'disk_spill_bytes': 0, 'response_bytes': MAX_BYTES,
          'columns_per_source': MAX_COLUMNS, 'snapshot_labels': MAX_SNAPSHOTS,
          'cell_chars': MAX_CELL, 'external_access': False}


def _base(project: str | None, sid: str | None) -> dict:
    return {'schema': SCHEMA, 'status': 'success', 'data_readiness': 'unknown',
            'scope': {'project': project, 'database': '.olav/databases/main.duckdb',
                      'snapshot_id': sid, 'snapshot_selection': 'exact' if sid else 'not_selected',
                      'snapshot_order': 'label_order_only_no_latest_inference',
                      'supported_sources_only': True},
            'limits': dict(LIMITS), 'truncated': False,
            'coverage': {'collection': 'unknown', 'parsing': 'unknown', 'network_health': 'not_assessed'},
            'limitations': ['Inventory is current intent, not snapshot membership.',
                            'Views and empty records do not prove collection, parsing completeness or absent protocols.',
                            'Raw store and topology links retain current last-writer records, not complete snapshot history.'],
            'next_steps': []}


def _error(result: dict, kind: str, message: str) -> dict:
    result.update(status='error', error_type=kind, error=message[:500], data_readiness='unknown')
    return result


def _project(project: str | Path | None) -> Path:
    raw = str(project) if project is not None else os.environ.get('OLAV_HOME')
    if not raw:
        raise ValueError('data doctor requires --project ABS or explicit OLAV_HOME; no cwd fallback')
    root = Path(raw)
    if not root.is_absolute():
        raise ValueError('project must be an absolute path')
    for component in (root, *root.parents):
        if component.is_symlink():
            raise ValueError('symlink project components are refused')
    if not root.is_dir():
        raise ValueError('project must be an existing directory')
    root = root.resolve()
    if len(str(root).encode('utf-8')) > 2048:
        raise ValueError('project path exceeds 2048-byte budget')
    path = root
    for name in ('.olav', 'databases', 'main.duckdb'):
        path = path / name
        if path.is_symlink():
            raise ValueError('symlink database components are refused')
    if path.exists() and not path.is_file():
        raise ValueError('database path is not a regular file')
    return root


def _data_doctor(project: str | Path | None = None, snapshot_id: str | None = None,
                *, timeout_ms: int = TIMEOUT_MS, max_bytes: int = MAX_BYTES) -> dict:
    """Inspect explicitly authorized native data, never initializing it.

    Trusted host limits can only lower fixed resource caps. A timeout fails the
    whole request; no partial capability report is presented as complete.
    """
    result = _base(None, snapshot_id)
    try:
        if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id.strip()
                or len(snapshot_id) > MAX_CELL or snapshot_id.lower() in ('latest', 'current', 'newest')):
            raise ValueError('snapshot_id must be an exact nonempty label of at most 256 characters; aliases refused')
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= TIMEOUT_MS:
            raise ValueError('timeout_ms must lower the fixed 1..5000 ms cap')
        if type(max_bytes) is not int or not 512 <= max_bytes <= MAX_BYTES:
            raise ValueError('max_bytes must lower the fixed 512..24000 byte cap')
        root = _project(project)
        result = _base(str(root), snapshot_id)
        result['limits'].update(timeout_ms=timeout_ms, response_bytes=max_bytes)
        path = root / '.olav/databases/main.duckdb'
        if not path.is_file():
            result.update(data_readiness='unavailable', database_status='missing')
            result['next_steps'] = ['Import an authorized offline bundle into this explicitly selected project; doctor never creates a database.']
            return result
        # Entire worker (startup/connect/query/serialize) is deadline bounded.
        process = subprocess.run([sys.executable, '-m', 'olav_skills.readiness'],
                                 input=json.dumps({'project': str(root), 'snapshot_id': snapshot_id}),
                                 text=True, capture_output=True, timeout=timeout_ms / 1000, check=False)
        if process.returncode:
            return _error(result, 'worker_error', 'read-only worker failed; no data readiness verdict')
        response = json.loads(process.stdout)
        response['limits'].update(timeout_ms=timeout_ms, response_bytes=max_bytes)
        if len(serialize(response).encode('utf-8')) > max_bytes:
            return {'schema': SCHEMA, 'status': 'error', 'error_type': 'output_budget',
                    'data_readiness': 'unknown', 'limits': {'response_bytes': max_bytes},
                    'error': 'response exceeds byte budget; no partial verdict'}
        return response
    except subprocess.TimeoutExpired:
        return _error(result, 'timeout', 'data doctor exceeded total request deadline')
    except (ValueError, OSError) as exc:
        return _error(result, 'validation_error', str(exc))


def data_doctor(project: str | Path | None = None, snapshot_id: str | None = None,
                *, timeout_ms: int = TIMEOUT_MS, max_bytes: int = MAX_BYTES) -> dict:
    """Bound every response, including validation and missing-source branches."""
    result = _data_doctor(project, snapshot_id, timeout_ms=timeout_ms, max_bytes=max_bytes)
    cap = max_bytes if type(max_bytes) is int and 512 <= max_bytes <= MAX_BYTES else MAX_BYTES
    if len(serialize(result).encode('utf-8')) > cap:
        return {'schema': SCHEMA, 'status': 'error', 'error_type': 'output_budget',
                'data_readiness': 'unknown', 'limits': {'response_bytes': cap},
                'error': 'response exceeds byte budget; no partial verdict'}
    return result


def serialize(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'), default=str)


def _inspect(project: str, snapshot_id: str | None) -> dict:
    import duckdb  # Only explicit --data worker imports the database engine.
    root = _project(project)  # Revalidate in child; never follow static symlinks.
    result = _base(str(root), snapshot_id)
    path = root / '.olav/databases/main.duckdb'
    if not path.is_file():
        return _error(result, 'source_missing', 'database disappeared before inspection')
    config = {'enable_external_access': 'false', 'autoload_known_extensions': 'false',
              'autoinstall_known_extensions': 'false', 'memory_limit': MEMORY,
              'threads': '1', 'max_temp_directory_size': '0B'}
    try:
        with duckdb.connect(str(path), read_only=True, config=config) as conn:
            result['database_status'] = 'readable'
            result['netops_schema'] = 'present' if conn.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name='netops' LIMIT 1").fetchone() else 'missing'
            entries = []
            for name, required in SOURCES.items():
                kind = conn.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='netops' AND table_name=? LIMIT 1", [name]).fetchone()
                entry: dict[str, Any] = {'source': 'netops.' + name, 'status': 'missing', 'record_count': None}
                entries.append(entry)
                if not kind:
                    continue
                entry['source_type'] = kind[0]
                rows = conn.execute("SELECT substr(column_name,1,256),substr(data_type,1,256) FROM information_schema.columns WHERE table_schema='netops' AND table_name=? ORDER BY ordinal_position LIMIT ?", [name, MAX_COLUMNS + 1]).fetchmany(MAX_COLUMNS + 1)
                entry['columns'] = [{'name': a, 'type': b} for a, b in rows[:MAX_COLUMNS]]
                entry['columns_truncated'] = len(rows) > MAX_COLUMNS
                result['truncated'] |= entry['columns_truncated']
                # Eligibility checks fixed required names directly, not clipped catalog values.
                cols = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='netops' AND table_name=? AND column_name IN (" + ','.join('?' for _ in required) + ')', [name, *required]).fetchmany(len(required))}
                entry['missing_required_columns'] = sorted(set(required) - cols)
                if entry['missing_required_columns']:
                    entry['status'] = 'unsupported_schema'
                    continue
                if name == 'devices':
                    scope, predicate, params = 'current_inventory', '', []
                elif name == 'raw_output_store':
                    scope, predicate, params = 'current_last_writer_all_records', '', []
                elif snapshot_id is None:
                    entry.update(status='snapshot_not_selected', record_scope='not_read')
                    continue
                else:
                    scope = ('current_last_writer_label' if name == 'topology_links' else
                             ('current_view_projection_filtered_by_label' if name.startswith('v_') else 'exact_snapshot_label'))
                    predicate, params = ' WHERE snapshot_id=?', [snapshot_id]
                entry['record_scope'] = scope
                # Fixed names + bound predicates. Bodies/parsed JSON are never fetched.
                count = conn.execute(f'SELECT count(*) FROM netops."{name}"' + predicate, params).fetchone()[0]
                entry.update(status='records_present' if count else 'empty', record_count=count)
                if name == 'devices':
                    entry['distinct_identities'] = conn.execute('SELECT count(DISTINCT hostname) FROM netops.devices').fetchone()[0]
                    entry['null_identities'] = conn.execute('SELECT count(*) FROM netops.devices WHERE hostname IS NULL').fetchone()[0]
                if name == 'parsed_outputs':
                    entry['observed_devices'] = conn.execute('SELECT count(DISTINCT device_name) FROM netops.parsed_outputs WHERE snapshot_id=?', params).fetchone()[0]
                    entry['membership_scope'] = 'observed parsed-output device identities only; not full collected estate'
                if name == 'bundle_ingests':
                    # No SID/ingest-date inference. Non-null counts do not prove completeness.
                    total, timed, low, high = conn.execute('SELECT count(*),count(collected_at),substr(CAST(min(collected_at) AS VARCHAR),1,256),substr(CAST(max(collected_at) AS VARCHAR),1,256) FROM netops.bundle_ingests WHERE snapshot_id=?', params).fetchone()
                    from .netops.core.query.temporal import time_value
                    lo, hi = time_value(low, 'netops.bundle_ingests.collected_at'), time_value(high, 'netops.bundle_ingests.collected_at')
                    time_type = next((t for n, t in rows if n == 'collected_at'), '')
                    typed_time = time_type.startswith(('TIMESTAMP', 'DATE'))
                    entry['collection_time'] = {'status': ('unknown' if not typed_time or not timed or lo['status'] != 'known' or hi['status'] != 'known' else ('known' if total == timed else 'partial')),
                                                'type_qualified': typed_time,
                                                'min': lo, 'max': hi, 'rows_with_time': timed,
                                                'scope': 'bundle metadata only; not per-device/command coverage'}
            result['sources'] = entries
            parsed = next(e for e in entries if e['source'] == 'netops.parsed_outputs')
            if parsed.get('missing_required_columns') == []:
                samples = conn.execute('SELECT substr(snapshot_id,1,256),length(snapshot_id) FROM (SELECT DISTINCT snapshot_id FROM netops.parsed_outputs WHERE snapshot_id IS NOT NULL) s ORDER BY snapshot_id LIMIT ?', [MAX_SNAPSHOTS + 1]).fetchmany(MAX_SNAPSHOTS + 1)
                labels_clipped = any(n > MAX_CELL for _, n in samples[:MAX_SNAPSHOTS])
                result['snapshot_labels'] = {'source': 'netops.parsed_outputs', 'labels': [s for s, _ in samples[:MAX_SNAPSHOTS]],
                                             'truncated': len(samples) > MAX_SNAPSHOTS or labels_clipped,
                                             'labels_may_be_clipped': labels_clipped, 'order': 'lexical_not_collection_time'}
                result['truncated'] |= result['snapshot_labels']['truncated']
            else:
                result['snapshot_labels'] = {'source': 'netops.parsed_outputs', 'labels': None, 'status': 'source_unavailable'}
            if snapshot_id is None:
                result['next_steps'].append('Select an exact snapshot_id from the authorized import receipt; label samples are not latest-time evidence.')
            if result['netops_schema'] == 'missing':
                result['data_readiness'] = 'unavailable'
                result['next_steps'].append('Import an authorized offline bundle; the native netops schema is absent.')
            elif snapshot_id is not None and parsed['status'] == 'records_present':
                result['data_readiness'] = 'recorded_queries_available'
            elif snapshot_id is not None:
                result['data_readiness'] = 'insufficient_recorded_data'
                result['next_steps'].append('Check exact snapshot/import receipt and parsed_outputs schema; zero matching rows is not evidence of zero devices or absent protocols.')
            result['next_steps'].append('Use the existing ingest result parse_report, report_path and learnable_commands for parser gaps; doctor does not rebuild a second ledger.')
            result['next_steps'].append('For missing/empty canonical views or topology, inspect authorized import evidence and supported topology query scope; do not infer collection or health.')
        if len(serialize(result).encode('utf-8')) > MAX_BYTES:
            return _error(_base(str(root), snapshot_id), 'output_budget', 'response exceeds byte budget; no partial verdict')
        return result
    except Exception as exc:
        # Errors remain errors, never converted to empty sources. Avoid returning
        # database exception text which can contain view SQL or private filenames.
        kind = ('resource_budget' if isinstance(exc, duckdb.OutOfMemoryException) else
                ('external_access_denied' if isinstance(exc, duckdb.PermissionException) else 'database_error'))
        return _error(result, kind, 'read-only inspection failed (' + type(exc).__name__[:80] + '); source/schema may be unsupported or resource/external-access budget exceeded')


def _worker() -> int:
    try:
        args = json.loads(sys.stdin.read(4096))
        result = _inspect(args['project'], args['snapshot_id'])
    except Exception as exc:
        result = _error(_base(None, None), 'worker_error', type(exc).__name__)
    if len(serialize(result).encode('utf-8')) > MAX_BYTES:
        result = {'schema': SCHEMA, 'status': 'error', 'error_type': 'output_budget',
                  'data_readiness': 'unknown', 'error': 'worker output budget exceeded'}
    print(serialize(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(_worker())
