"""Bounded historical content comparison, independent of key-only snapshot diff.

Only fixed canonical field spellings are accepted. Historical parsed outputs
are used because per-command auto views hide non-latest snapshots.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
from olav_skills import state_contracts as states

from .snapshot_summary import SnapshotDiffError, _run_with_timeout, _resolve_connect_config, _classify_db_error

SCHEMA = 'olav.state-diff/v1'
MAX_INPUT = 16_384
MAX_BYTES = 65_536
SAMPLE_BYTES = 24_576
CELL = 256
TIMEOUT_MS = 15_000
L2_KEYS = ('source_device', 'source_interface', 'destination_device', 'destination_interface', 'discovery_protocol')
LIMITATIONS = [
    'Recorded observations only; record additions/removals do not prove network events.',
    'Collection coverage and collection time unknown; explicit SIDs are not time ordering.',
    'Missing VRF/AFI is unknown, never a default routing context; unsupported parser shapes fail.',
    'NULL state values remain unknown. No bidirectional collection is inferred from a directed L2 record.',
    'Routes/next-hop and legacy warehouse are unsupported by this native comparison.',
]


def _wire(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def finalize(result: dict) -> dict:
    if len(_wire(result).encode()) > MAX_BYTES:
        return {'schema': SCHEMA, 'status': 'error', 'error_type': 'budget_exceeded',
                'diff_status': 'unknown', 'counts': None, 'error': 'response byte budget exceeded'}
    return result


class Reader:
    def __init__(self, conn, timeout_ms=TIMEOUT_MS):
        self.conn = conn
        self.deadline = time.monotonic() + timeout_ms / 1000

    def run(self, sql, params=(), cap=1):
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise SnapshotDiffError('timeout', 'whole comparison deadline exceeded')
        return _run_with_timeout(self.conn, lambda: self.conn.execute(sql, list(params)).fetchmany(cap),
                                 timeout_ms=remaining, stage='compare_state')

    def require(self, table, fields, *, text_fields=()):
        # Fetch only required metadata; no catalog-sized materialization.
        marks = ','.join('?' for _ in fields)
        rows = self.run(f"SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='netops' AND table_name=? AND column_name IN ({marks}) LIMIT ?",
                        [table, *fields, len(fields)], len(fields))
        present = dict(rows)
        if not present:
            raise SnapshotDiffError('source_missing', 'required source absent: netops.' + table)
        if not set(fields) <= set(present):
            raise SnapshotDiffError('unsupported_schema', 'source lacks required columns')
        if any(present[field] != 'VARCHAR' for field in text_fields):
            raise SnapshotDiffError('unsupported_schema', 'source identity columns must be VARCHAR')
        return present


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > CELL:
        raise ValueError(label + ' must be a nonempty string of at most 256 characters')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError(label + ' must be valid UTF-8') from exc
    return value


def _request(args):
    if not isinstance(args, dict) or set(args) - {'source', 'snapshot_id_1', 'snapshot_id_2', 'device', 'limit'}:
        raise ValueError('expected object with source, snapshot_id_1, snapshot_id_2, optional device/limit')
    a = dict(args)
    for field in ('source', 'snapshot_id_1', 'snapshot_id_2'):
        _text(a.get(field), field)
    if a['source'] not in (*states.SOURCE_PROFILES, 'l2', 'routes'):
        raise ValueError('source not in supported whitelist')
    if any(a[k].strip().lower() == 'latest' for k in ('snapshot_id_1', 'snapshot_id_2')):
        raise ValueError('two explicit snapshot IDs are required; latest is unsupported')
    if a.get('device') is not None:
        _text(a['device'], 'device')
    limit = a.setdefault('limit', 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('limit must be an integer from 1 to 50')
    return a


def _json_value(field):
    return f"json_extract_string(value,'$.{field}')"


def _json_shape(profile, *, numeric=()):
    conditions = []
    for field in profile.columns:
        types = "'VARCHAR','NULL'"
        if field in numeric:
            types += ",'UBIGINT','BIGINT','DOUBLE'"
        conditions.append(f"coalesce(json_type(value,'$.{field}'),'MISSING') IN ({types})")
    return '(' + ' AND '.join(conditions) + ')'


def _json_has_fields(profile):
    return '(' + ' AND '.join(f"json_exists(value,'$.{field}')" for field in profile.columns) + ')'


def _parsed(q, a):
    source = a['source']
    command, profiles = states.SOURCE_PROFILES[source]
    params = [a['snapshot_id_1'], a['snapshot_id_2'], command]
    where = 'snapshot_id COLLATE "binary" IN (?,?) AND command COLLATE "binary"=?'
    if a.get('device') is not None:
        where += ' AND device_name COLLATE "binary"=?'
        params.append(a['device'])
    parent = ('SELECT snapshot_id COLLATE "binary" AS snapshot_id,'
              'device_name COLLATE "binary" AS device_name,parsed_data FROM netops.parsed_outputs WHERE ' + where)
    for sid in (a['snapshot_id_1'], a['snapshot_id_2']):
        present = q.run('SELECT count(*) FROM (' + parent + ') p WHERE snapshot_id=?', [*params, sid])[0][0]
        if not present:
            raise SnapshotDiffError('source_incomplete', 'selected command has no captured parent rows in one requested snapshot/filter')
    invalid = q.run('SELECT count(*) FROM (' + parent + ") p WHERE parsed_data IS NULL OR NOT json_valid(parsed_data) OR json_type(TRY_CAST(parsed_data AS JSON)) IS DISTINCT FROM 'ARRAY'", params)[0][0]
    if invalid:
        raise SnapshotDiffError('unsupported_schema', 'parsed source must contain non-null JSON arrays')
    expanded = f'''SELECT p.snapshot_id,p.device_name,unnest(from_json(p.parsed_data,'["JSON"]')) AS value FROM ({parent}) p'''
    bgp = source.startswith('bgp')
    select = ['snapshot_id', 'device_name', "nullif(json_extract_string(value,'$.vrf'),'') AS vrf",
              "nullif(json_extract_string(value,'$.afi'),'') AS afi"]
    if bgp:
        # A snapshot can be imported with a different, explicitly recognized
        # profile. A single object with both profiles is ambiguous.
        native, *alternate = profiles
        legacy_count = ('state_or_prefixes_received',)
        native_shape = _json_shape(native, numeric=legacy_count)
        native_state = _json_value(native.fields[0])
        identity, raw_state, state = (_json_value(native.identity), native_state,
                                      states.bgp_state_sql(native_state, native))
        valid = native_shape
        if alternate:
            legacy, = alternate
            legacy_shape = _json_shape(legacy, numeric=legacy_count)
            legacy_state = _json_value(legacy.fields[0])
            valid = (f'(({native_shape} AND NOT {_json_has_fields(legacy)})'
                     f' OR ({legacy_shape} AND NOT {_json_has_fields(native)}))')
            identity = f'CASE WHEN {native_shape} THEN {identity} ELSE {_json_value(legacy.identity)} END'
            raw_state = f'CASE WHEN {native_shape} THEN {native_state} ELSE {legacy_state} END'
            state = f'CASE WHEN {native_shape} THEN {state} ELSE {states.bgp_state_sql(legacy_state, legacy)} END'
        key = 'bgp_neighbor'
        values = ('state',)
        select.extend((f'{identity} AS {key}', f'{state} AS state', f'{raw_state} AS raw_state'))
    else:
        key = 'interface'
        junos = source == 'interfaces_junos'
        primary = profiles[0]
        valid = _json_shape(primary)
        if junos:
            legacy_shape = _json_shape(states.JUNOS_LEGACY_INTERFACE)
            valid = f"({valid} OR ({legacy_shape} AND NOT json_exists(value,'$.admin_state')))"
        status = states.interface_state_sql(_json_value('link_state' if junos else 'status'))
        proto = _json_value('proto') if junos else states.interface_state_sql(_json_value('proto'))
        select.extend((f'{_json_value(key)} AS {key}', f'{status} AS status',
                       f'{proto} AS proto', f"{_json_value('ip_address')} AS ip_address"))
        values = ('status', 'proto', 'ip_address')
        if junos:
            admin = states.interface_state_sql(_json_value('admin_state'))
            select.append(f'{admin} AS admin_state')
            values += ('admin_state',)
    checks = ["json_type(value) IS DISTINCT FROM 'OBJECT'", f'NOT {valid}']
    for field in ('vrf', 'afi'):
        checks.append(f"coalesce(json_type(value,'$.{field}'),'NULL') NOT IN ('VARCHAR','NULL')")
    if q.run(f'SELECT count(*) FROM ({expanded}) e WHERE ' + ' OR '.join(checks), params)[0][0]:
        raise SnapshotDiffError('unsupported_schema', 'parser records lack one canonical scalar profile or contain unsupported fields')
    keys = ('device_name', key, 'vrf', 'afi')
    return 'SELECT ' + ','.join(select) + f' FROM ({expanded}) e', params, keys, values, ('device_name', key)


def _compare(q, a):
    identity_fields = ('snapshot_id', 'device_name', 'command')
    types = q.require('parsed_outputs', (*identity_fields, 'parsed_data'), text_fields=identity_fields)
    if types['parsed_data'] not in ('JSON', 'VARCHAR'):
        raise SnapshotDiffError('unsupported_schema', 'parsed_data must be JSON or VARCHAR')
    for sid in (a['snapshot_id_1'], a['snapshot_id_2']):
        if not q.run('SELECT 1 FROM netops.parsed_outputs WHERE snapshot_id COLLATE "binary"=? LIMIT 1', [sid]):
            raise SnapshotDiffError('snapshot_missing', 'explicit snapshot not present in parsed evidence')
    if a['source'] == 'l2':
        keys, values = L2_KEYS, ('link_status',)
        q.require('v_l2_links_auto', ('snapshot_id', *keys, *values))
        sql = 'SELECT snapshot_id,' + ','.join((*keys, *values)) + ' FROM netops.v_l2_links_auto WHERE snapshot_id IN (?,?)'
        params = [a['snapshot_id_1'], a['snapshot_id_2']]
        if a.get('device') is not None:
            sql += ' AND source_device=?'
            params.append(a['device'])
        mandatory = keys
    else:
        sql, params, keys, values, mandatory = _parsed(q, a)
    base = 'WITH s AS (' + sql + ') '
    if q.run(base + 'SELECT count(*) FROM s WHERE ' + ' OR '.join(f'"{k}" IS NULL OR trim("{k}")=\'\'' for k in mandatory), params)[0][0]:
        raise SnapshotDiffError('unknown_identity', 'record identity is incomplete; comparison refused')
    group = ','.join(('snapshot_id', *keys))
    if q.run(base + f'SELECT 1 FROM s GROUP BY {group} HAVING count(*)>1 LIMIT 1', params):
        raise SnapshotDiffError('ambiguous_identity', 'duplicate identity within snapshot; comparison refused')
    match = ' AND '.join(f'a."{k}" IS NOT DISTINCT FROM b."{k}"' for k in keys)
    different = ' OR '.join(f'a."{k}" IS DISTINCT FROM b."{k}"' for k in values)
    unknown = ' OR '.join(f'a."{k}" IS NULL OR b."{k}" IS NULL' for k in values)
    projection = [f'coalesce(a."{k}",b."{k}") AS "{k}"' for k in keys]
    fields = list(keys)
    if a['source'].startswith('bgp'):
        output_values = (*values, 'raw_state')
    else:
        output_values = values
    for field in output_values:
        for prefix, alias in (('a', 'old_'), ('b', 'new_')):
            name = alias + field
            fields.append(name)
            projection.append(f'{prefix}."{field}" AS "{name}"')
    projection += [f"CASE WHEN a.snapshot_id IS NULL THEN 'added' WHEN b.snapshot_id IS NULL THEN 'removed' WHEN {different} THEN 'changed' ELSE 'unchanged' END AS change", f'({unknown}) AS value_unknown']
    fields += ['change', 'value_unknown']
    base += ', a AS (SELECT * FROM s WHERE snapshot_id=?), b AS (SELECT * FROM s WHERE snapshot_id=?), d AS (SELECT ' + ','.join(projection) + ' FROM a FULL OUTER JOIN b ON ' + match + ') '
    params += [a['snapshot_id_1'], a['snapshot_id_2']]
    counts = q.run(base + "SELECT count(*) FILTER (WHERE change='added'),count(*) FILTER (WHERE change='removed'),count(*) FILTER (WHERE change='changed'),count(*) FILTER (WHERE change='unchanged'),count(*) FILTER (WHERE value_unknown) FROM d", params)[0]
    count_dict = dict(zip(('added', 'removed', 'changed', 'unchanged', 'value_unknown'), counts))
    total = sum(counts[:3])
    # Preserve full values for joins/counts/order; clip only bounded sample cells.
    clipped = ','.join(f'left("{f}",{CELL}) AS "{f}"' if f not in ('change', 'value_unknown') else f'"{f}"' for f in fields)
    long = ' OR '.join(f'length("{f}")>{CELL}' for f in fields if f not in ('change', 'value_unknown'))
    order = ','.join('"' + k + '" ASC NULLS LAST' for k in keys)
    rows = q.run(base + f"SELECT {clipped},coalesce(({long}),false) AS cell_truncated FROM (SELECT * FROM d WHERE change<>'unchanged' ORDER BY {order} LIMIT ?) sampled ORDER BY {order}", [*params, a['limit']], a['limit'])
    samples, byte_truncated = [], False
    for row in rows:
        item = dict(zip([*fields, 'cell_truncated'], row))
        item['certainty'] = 'unknown' if item['value_unknown'] else 'known_values'
        if len(_wire([*samples, item]).encode()) > SAMPLE_BYTES:
            byte_truncated = True
            break
        samples.append(item)
    return {'counts': count_dict, 'total_changes': total, 'samples': samples,
            'sample_count': len(samples), 'has_more': total > len(samples), 'byte_truncated': byte_truncated,
            'identity': list(keys), 'compared_fields': list(values),
            'diff_status': 'observed_changes' if total else ('unknown' if counts[4] else 'no_observed_changes')}


def _binding(source):
    if source not in states.SOURCE_PROFILES:
        return {'view': 'netops.v_l2_links_auto', 'fields': [*L2_KEYS, 'link_status']}
    command, profiles = states.SOURCE_PROFILES[source]
    # Keep the v1 field list for readers that display legacy source names.
    result = {'command': command, 'json_fields': list(profiles[-1].columns)}
    if len(profiles) > 1:
        result['supported_json_field_profiles'] = [list(profile.columns) for profile in profiles]
    return result


def compare_state(args: dict, *, db_path=None, timeout_ms=TIMEOUT_MS) -> dict:
    """Compare explicit snapshots. db_path/deadline are trusted host-only arguments."""
    scope = {}
    conn = None
    try:
        a = _request(args)
        scope = {'source': a['source'], 'snapshot_id_1': a['snapshot_id_1'], 'snapshot_id_2': a['snapshot_id_2'],
                 'filters': {'device': a.get('device')}, 'collection_time': 'unknown', 'coverage': 'unknown', 'comparison_scope': 'selected_source_observed_records_only',
                 'canonical_binding': _binding(a['source'])}
        if a['source'] == 'routes':
            raise SnapshotDiffError('unsupported_source', 'route next-hop schema/VRF/AFI/multipath contract unavailable')
        if db_path is None:
            home = os.environ.get('OLAV_HOME')
            if not home:
                raise ValueError('OLAV_HOME must be explicitly bound to the authorized project')
            db_path = Path(home) / '.olav' / 'databases' / 'main.duckdb'
        if not Path(db_path).is_file():
            raise SnapshotDiffError('source_missing', 'authorized database not found')
        conn = duckdb.connect(str(db_path), read_only=True, config=_resolve_connect_config())
        result = _compare(Reader(conn, min(TIMEOUT_MS, max(0, timeout_ms))), a)
        return finalize({'schema': SCHEMA, 'status': 'success', 'scope': scope, **result,
                         'source': 'netops.v_l2_links_auto' if a['source']=='l2' else 'netops.parsed_outputs',
                         'limits': {'sample_rows': a['limit'], 'sample_bytes': SAMPLE_BYTES, 'cell_chars': CELL,
                                    'response_bytes': MAX_BYTES, 'timeout_ms': min(TIMEOUT_MS, max(0, timeout_ms)),
                                    'memory_limit': _resolve_connect_config()['memory_limit'], 'threads': _resolve_connect_config()['threads']},
                         'limitations': LIMITATIONS})
    except (ValueError, TypeError, RecursionError) as exc:
        kind, message = 'validation_error', str(exc)
    except SnapshotDiffError as exc:
        kind, message = exc.error_type, exc.message
    except Exception as exc:
        kind, message = _classify_db_error(exc), 'read-only comparison failed'
    finally:
        if conn is not None:
            conn.close()
    return finalize({'schema': SCHEMA, 'status': 'error', 'error_type': kind, 'error': message[:800],
                     'diff_status': 'unknown', 'counts': None, 'scope': scope, 'limitations': LIMITATIONS})


def main() -> int:
    try:
        raw = __import__('sys').stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('stdin exceeds 16384-byte budget')
        args = json.loads(raw or b'{}')
        result = compare_state(args)
    except (ValueError, TypeError, RecursionError) as exc:
        result = {'schema': SCHEMA, 'status': 'error', 'error_type': 'validation_error',
                  'error': str(exc)[:800], 'counts': None, 'diff_status': 'unknown'}
    result = finalize(result)
    print(_wire(result))
    return 0 if result['status'] == 'success' else 1
