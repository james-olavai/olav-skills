"""Bounded historical domain queries for the trusted, configured Lake reader.

No client connection selectors. Native dispatch remains with existing callers.
Sources are persisted B3 evidence, not independent operation reconciliation.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import cmp_to_key, lru_cache

from olav_skills.core.read_connection import read_host_config, open_read_connection
from olav_skills.tools.sql_policy import load_query_policy


FUNCTIONS = frozenset('count count_star sum min max length octet_length encode substr lower trim coalesce nullif replace contains regexp_matches regexp_extract strpos sha256 to_json json_extract json_extract_string json_type json_valid epoch_us json_each json_array_length row_number list_extract current_database current_schema current_setting duckdb_functions duckdb_tables duckdb_views duckdb_columns'.split())
OPERATORS = frozenset({'=', '!=', '<>', '<', '>', '<=', '>=', '+', '-', '||', '~~', '~~*', '!~~*'})
TABLES = {
    'devices': ('hostname', 'ip_address', 'platform', 'vendor', 'model', 'role'),
    'parsed_outputs': ('snapshot_id', 'device_name', 'command', 'parsed_data'),
    'bundle_ingests': ('snapshot_id',),
    'raw_output_store': ('snapshot_id', 'device_name', 'command', 'raw_output'),
    'topology_links': ('snapshot_id', 'link_id', 'source_device'),
    'oc_outputs': ('snapshot_id', 'device_name', 'oc_module'),
    'ingest_sources': ('snapshot_id', 'device_name', 'command', 'source_json', 'raw_output', 'parser_json', 'parsed_at', 'ingested_at', 'task_id', 'commit_token'),
}
KEYS = {'parsed_outputs': ('device_name', 'command'), 'raw_output_store': ('device_name', 'command'), 'topology_links': ('link_id',), 'oc_outputs': ('device_name', 'oc_module')}
TIME_RELATIONS = {'parsed_outputs', 'bundle_ingests'}
UNKNOWN = {'value': None, 'precision': 'unknown', 'status': 'unknown', 'source': None}

_EPOCH = datetime(1970, 1, 1)
_CLOCK_MIN_US = (datetime(1, 1, 1) - _EPOCH).days * 86_400_000_000
_CLOCK_MAX_US = (datetime(9999, 12, 31, 23, 59, 59, 999999) - _EPOCH).days * 86_400_000_000 + 86_399_999_999

# Fixed top-level source_json provenance paths (order is the slot ordinal for
# the single multi-path projection in _source_scalar_audit).
_SOURCE_PATHS = ('$.device_name', '$.command', '$.platform', '$.raw_artifact', '$.collected_at', '$.actual_raw_sha256', '$.body_sha256', '$.input_artifact', '$.input_sha256', '$.parse_status', '$.parser_fingerprint', '$.parser_versions')

_OSPF_STATES = frozenset('Full FULL/DR FULL/BDR FULL/DROTHER 2-Way 2WAY/DROTHER Init ExStart Exchange Loading Down'.split())
_OSPF_TITLE_FIX = {'Exstart': 'ExStart'}


def _ospf_state(raw):
    """Normalize an OSPF state to the declared OspfState set (opt-in bounded path)."""
    s = (str(raw) if raw is not None else '').strip()
    state = s.upper() if '/' in s else s.title()
    if state.replace('-', '').upper() == '2WAY' and '/' not in s:
        state = '2-Way'
    state = _OSPF_TITLE_FIX.get(state, state)
    if state not in _OSPF_STATES:
        raise DomainError('SOURCE_UNAVAILABLE', 'ospf_state')
    return state


def _ospf_neighbor_ip(raw):
    """Validate an optional OSPF neighbor IP; None/empty is fine, invalid raises."""
    if raw is None or raw == '':
        return None
    try:
        return str(ipaddress.ip_address(str(raw)))
    except ValueError:
        raise DomainError('SOURCE_UNAVAILABLE', 'ospf_neighbor_ip') from None


class DomainError(ValueError):
    def __init__(self, code, reason='domain_request_failed'):
        self.code, self.reason = code, reason
        super().__init__(code)


def failure(code, reason='domain_request_failed'):
    return {'status': 'error', 'error_type': code, 'error_kind': code,
            'error': {'code': code, 'retryable': code in ('CONCURRENCY_LIMIT', 'WAREHOUSE_UNAVAILABLE', 'SOURCE_UNAVAILABLE', 'EXECUTION_TIMEOUT'), 'details': {'reason': reason}}}


def configured():
    """Bad configuration is a configured failure, never permission to go native."""
    try:
        return read_host_config() is not None
    except Exception:
        return True


def dumps(value):
    def scalar(v):
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if v.tzinfo is not None else v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        raise TypeError('unsupported domain scalar')
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False, default=scalar)


def clock(value, source):
    if value is None:
        return dict(UNKNOWN)
    raw = dumps(value)[1:-1] if isinstance(value, (datetime, date)) else value
    if not isinstance(raw, str):
        raise DomainError('SOURCE_UNAVAILABLE', 'clock_type')
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        normalized = normalize_time({'value': raw, 'precision': 'timestamp' if dt.tzinfo else 'timestamp_without_timezone', 'status': 'known', 'source': source})
        if normalized['status'] != 'known':
            raise ValueError()
        return normalized
    except ValueError:
        raise DomainError('SOURCE_UNAVAILABLE', 'clock_value') from None


def text(value, *, optional=False):
    if optional and value is None:
        return value
    if not isinstance(value, str) or not value or len(value) > 256:
        raise DomainError('INVALID_REQUEST')
    return value


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise DomainError('INVALID_REQUEST')
    return value


def strings(value):
    if not isinstance(value, list) or len(value) > 50:
        raise DomainError('INVALID_REQUEST')
    return list(dict.fromkeys(text(v) for v in value))


def finalize(result, *, byte_cap=65536, char_cap=None):
    """Only candidate serializer. Error envelopes cannot be erased by row caps."""
    if result.get('status') == 'error':
        return result
    cell_cap = 256 if 'operation' in result else 2000
    def cells(value):
        if isinstance(value, str) and len(value) > cell_cap:
            raise DomainError('RESULT_ROW_TOO_LARGE', 'cell_chars')
        if isinstance(value, dict):
            for item in value.values(): cells(item)
        elif isinstance(value, (list, tuple)):
            for item in value: cells(item)
    try:
        cells(result)
    except DomainError as exc:
        return failure(exc.code, exc.reason)
    def attach():
        from olav_skills.core.query_result import contract, provenance, limits, count, returned
        meta = result.get('limit_metadata', {})
        sources = result.get('limits', {}).get('physical_sources', [])
        rows = result.get('rows', result.get('matches', []))
        op = result.get('operation')
        units = {'devices': 'devices', 'device_coverage': 'vendor_groups', 'command_coverage': 'commands', 'commands': 'device_command_groups', 'multi_device_summary': 'devices', 'top_n': 'groups', 'interface_health': 'historical_interface_records', 'bgp_health': 'historical_bgp_records'}
        unit = units.get(op, 'evidence_records' if 'matches' in result else 'sample_rows')
        if op == 'diff_summary':
            from .result_contract import adapt_diff
            result['result_contract'] = adapt_diff(result['diff'], requested_limit=meta.get('requested'), host_cap=meta.get('host', 50), physical_sources=sources, cell_cap=None)
            return
        block = contract(level='schema' if result.get('operation') == 'context' else 'operation',
                         limits=limits(requested={} if meta.get('requested') is None else {'limit': meta['requested']}, defaults={'limit': meta.get('default', 50)}, server_caps={'limit': min(meta.get('host', 50), meta.get('fixed_direction_cap', 50))}),
                         returned=returned(value=len(rows), unit=unit),
                         provenance=provenance(executed=result.get('operation') != 'context', physical_sources=sources),
                         semantics='historical parsed/source evidence; current inventory separate; bounded samples, no stable pagination')
        total = result.get('total_rows', result.get('total'))
        if type(total) is int:
            block['total'] = count(value=total, unit=unit, scope='full_filtered_operation')
        if op == 'snapshot_summary':
            block['counts'] = {k: count(value=v, unit='records' if k == 'record_count' else 'commands' if k == 'command_count' else 'devices', scope='selected_snapshot_current_inventory_intersection') for k, v in result['counts'].items()}
            block.pop('returned', None)
        if op == 'context':
            block.pop('returned', None)
        block['truncation'] = {'rows': {'truncated': bool(result.get('has_more')), 'returned': len(rows)}, 'bytes': {'truncated': bool(result.get('byte_truncated'))}}
        result['result_contract'] = block
    def fits():
        if 'rows' in result:
            result['returned_rows'] = len(result['rows'])
        if 'matches' in result:
            result['returned'] = len(result['matches'])
            result['excerpt_truncated_rows'] = sum(bool(row.get('excerpt_truncated')) for row in result['matches'])
            omitted = result.get('budget', {}).get('rows_omitted_by_char_budget', 0)
            result['has_more'] = result['offset'] + result['returned'] < result['total']
            result['next_offset'] = result['offset'] + result['returned'] if result['has_more'] else None
            result['truncated'] = bool(result['has_more'] or omitted or result['excerpt_truncated_rows'] or any(row.get('char_budget_truncated') for row in result['matches']))
        attach()
        wire = dumps(result)
        return len(wire.encode()) <= byte_cap and (char_cap is None or len(wire) <= char_cap)
    while not fits():
        if result.get('rows'):
            if len(result['rows']) == 1:
                return failure('RESULT_ROW_TOO_LARGE', 'first_complete_row')
            result['rows'].pop()
            result['has_more'] = True
            result['byte_truncated'] = True
            if result.get('result_contract'):
                result.pop('result_contract')
        elif result.get('matches'):
            if len(result['matches']) == 1:
                return failure('RESULT_ROW_TOO_LARGE', 'first_complete_row')
            result['matches'].pop()
            result['budget']['rows_omitted_by_char_budget'] += 1
        elif result.get('tables') or result.get('diff'):
            groups = list((result.get('tables') or {'single': result['diff']}).values())
            choices = [(g, d) for g in groups for d in ('removed', 'added') if g.get(d + '_sample')]
            if not choices:
                return failure('RESULT_ROW_TOO_LARGE', 'response_metadata')
            g, d = choices[-1]
            g[d + '_sample'].pop()
            g['sample_truncated'][d] = True
            g[d] = g[d + '_sample']
            g[d + '_has_more'] = True
            g[d + '_truncated'] = True
        else:
            return failure('RESULT_ROW_TOO_LARGE', 'response_metadata')
    return result


def wire(result, **kwargs):
    return dumps(finalize(result, **kwargs))


def stdin_args(stream, *, byte_cap=65536, allowed=None):
    raw = stream.buffer.read(byte_cap + 1)
    if len(raw) > byte_cap:
        raise DomainError('INPUT_BUDGET_EXCEEDED')
    try:
        args = json.loads(raw.decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise DomainError('INVALID_REQUEST') from None
    if not isinstance(args, dict) or (allowed is not None and set(args) - set(allowed)):
        raise DomainError('INVALID_REQUEST')
    return args


def normalize_time(value):
    if value is None:
        return dict(UNKNOWN)
    if not isinstance(value, dict):
        return {**UNKNOWN, 'status': 'invalid'}
    status = value.get('status', 'unknown')
    precision = value.get('precision', 'unknown')
    source = value.get('source')
    if source is not None and (not isinstance(source, str) or len(source) > 256):
        return {**UNKNOWN, 'status': 'invalid'}
    if status != 'known':
        if status in ('partial', 'derived') and value.get('value') is not None:
            limited = normalize_time({**value, 'status': 'known'})
            if limited['status'] == 'known':
                return {**limited, 'status': status}
        return {**UNKNOWN, 'status': status if status in ('derived', 'unknown', 'invalid', 'conflict', 'partial') else 'invalid', 'source': source}
    raw = value.get('value')
    try:
        if precision == 'date':
            if not isinstance(raw, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                raise ValueError()
            normalized = date.fromisoformat(raw).isoformat()
        else:
            if not isinstance(raw, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.]\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?', raw):
                raise ValueError()
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if precision == 'timestamp' and dt.tzinfo is not None and dt.utcoffset() is not None:
                normalized = dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
            elif precision == 'timestamp_without_timezone' and dt.tzinfo is None:
                normalized = dt.isoformat()
            else:
                raise ValueError()
        return {'value': normalized, 'precision': precision, 'status': 'known', 'source': source}
    except (ValueError, TypeError, AttributeError, OverflowError):
        return {**UNKNOWN, 'status': 'invalid', 'source': source}


def aggregate_time(records):
    values = [normalize_time(r.get('collected_at')) for r in records]
    if not values:
        return dict(UNKNOWN)
    if len({(v['value'], v['precision']) for v in values}) > 1:
        return {**UNKNOWN, 'status': 'conflict', 'source': 'persisted_ingest_sources'}
    if any(v['status'] != 'known' for v in values):
        statuses = {v['status'] for v in values}
        status = next((s for s in ('conflict', 'invalid', 'partial', 'derived', 'unknown') if s in statuses), 'unknown')
        return {**UNKNOWN, 'status': status, 'source': 'persisted_ingest_sources'}
    return values[0]


def select_latest(times):
    if not times or any(t['status'] != 'known' for t in times.values()):
        raise DomainError('TIME_ORDER_UNDETERMINED')
    if len({t['precision'] for t in times.values()}) != 1:
        raise DomainError('TIME_ORDER_UNDETERMINED')
    def key(t):
        return date.fromisoformat(t['value']) if t['precision'] == 'date' else datetime.fromisoformat(t['value'].replace('Z', '+00:00'))
    ordered = sorted(times, key=lambda sid: key(times[sid]), reverse=True)
    if len(ordered) > 1 and key(times[ordered[0]]) == key(times[ordered[1]]):
        raise DomainError('TIME_ORDER_UNDETERMINED')
    return ordered[0]


class Session:
    def __init__(self, read, policy, *, timeout=5000):
        self.conn, self.version, self.policy = read.connection, read.read_version, policy
        self.deadline = time.monotonic() + timeout / 1000
        self.sources, self.checked, self.cache = set(), set(), {}
        self.total_bytes = 0
        self.guard()

    def run(self, sql, params=(), cap=1000):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DomainError('EXECUTION_TIMEOUT')
        expired = threading.Event()
        def interrupt():
            expired.set()
            self.conn.interrupt()
        timer = threading.Timer(remaining, interrupt)
        timer.daemon = True
        timer.start()
        try:
            rows = self.conn.execute(sql, list(params)).fetchmany(cap + 1)
            if expired.is_set() or time.monotonic() >= self.deadline:
                raise DomainError('EXECUTION_TIMEOUT')
            if len(rows) > cap:
                raise DomainError('RESULT_BUDGET_EXCEEDED', 'metadata_rows')
            size = len(dumps(rows).encode())
            self.total_bytes += size
            if self.total_bytes > 1_000_000:
                raise DomainError('RESULT_BUDGET_EXCEEDED', 'metadata_bytes')
            return rows
        except DomainError:
            raise
        except Exception as exc:
            if expired.is_set():
                raise DomainError('EXECUTION_TIMEOUT') from None
            if 'memory' in type(exc).__name__.lower():
                raise DomainError('RESULT_BUDGET_EXCEEDED', 'memory') from None
            raise DomainError('SOURCE_UNAVAILABLE') from None
        finally:
            timer.cancel()

    def guard(self):
        if self.run("SELECT system.main.current_database(),system.main.current_schema(),system.main.current_setting('search_path')", cap=1) != [('reader', 'main', 'reader.main')]:
            raise DomainError('WAREHOUSE_UNAVAILABLE')
        for name, internal in self.run('SELECT function_name,internal FROM system.main.duckdb_functions() LIMIT 4097', cap=4096):
            if isinstance(name, str) and name.isascii() and name.lower() in FUNCTIONS | OPERATORS and internal is not True:
                raise DomainError('SQL_NOT_ALLOWED', 'shadow_function')
        self.tables = {(d.lower(), s.lower(), n.lower()) for d, s, n in self.run('SELECT database_name,schema_name,table_name FROM system.main.duckdb_tables() WHERE internal=false LIMIT 1025', cap=1024)}
        self.views = {(d.lower(), s.lower(), n.lower()) for d, s, n in self.run('SELECT database_name,schema_name,view_name FROM system.main.duckdb_views() LIMIT 1025', cap=1024)}
        self.columns = {}
        for db, schema, name, col, dtype in self.run("SELECT database_name,schema_name,table_name,column_name,data_type FROM system.main.duckdb_columns() WHERE database_name='reader' AND schema_name='netops' LIMIT 4097", cap=4096):
            self.columns.setdefault(name.lower(), {})[col.lower()] = dtype

    def require(self, table, columns=None, *, internal=False):
        if table not in TABLES:
            raise DomainError('INVALID_REQUEST')
        if not internal and ('netops', table) not in self.policy.relations:
            raise DomainError('OPERATION_DENIED')
        key = ('reader', 'netops', table)
        if key not in self.tables or key in self.views or ('temp', 'main', table) in self.tables | self.views:
            raise DomainError('SOURCE_UNAVAILABLE', 'base_table_required')
        if not set(columns or TABLES[table]) <= set(self.columns.get(table, {})):
            raise DomainError('SOURCE_UNAVAILABLE', 'required_columns')
        for col in columns or TABLES[table]:
            dtype = self.columns[table][col].upper()
            allowed = {'VARCHAR', 'JSON'} if col in ('source_json', 'parser_json', 'parsed_data') else {'VARCHAR'}
            if table == 'bundle_ingests' and col in ('commands_count', 'hosts_count'):
                allowed = {'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT'}
            if table == 'ingest_sources' and col in ('parsed_at', 'ingested_at'):
                allowed |= {'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMPTZ'}
            if dtype not in allowed:
                raise DomainError('SOURCE_UNAVAILABLE', 'column_type')
        self.checked.add(table)

    def bounded(self, table, columns, *, predicate='TRUE', params=()):
        self.require(table, columns, internal=table == 'ingest_sources')
        fields = ','.join('"' + c + '"' for c in columns)
        cells = [f"system.main.octet_length(system.main.encode(COALESCE(CAST(\"{c}\" AS VARCHAR),'')))" for c in columns]
        lens = '+'.join(cells)
        cell_maxima = ','.join(f'COALESCE(max({cell}),0)' for cell in cells)
        row = self.run(f'SELECT count(*),COALESCE(sum({lens}),0),{cell_maxima} FROM reader.netops.{table} WHERE {predicate}', params, cap=1)[0]
        if row[0] > 1000 or row[1] > 1_000_000 or max(row[2:]) > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'source_metadata')
        rows = self.run(f'SELECT {fields} FROM reader.netops.{table} WHERE {predicate} LIMIT 1001', params)
        self.sources.add('netops.' + table)
        return [dict(zip(columns, r)) for r in rows]

    def source_records(self, sid=None):
        cache_key = ('sources', sid)
        if cache_key in self.cache:
            return self.cache[cache_key]
        pred, params = ('snapshot_id=?', [sid]) if sid is not None else ('TRUE', [])
        result = self._materialize_sources(pred, params)
        self.cache[cache_key] = result
        return result

    def _materialize_sources(self, pred, params):
        """Fetch and validate ingest_sources rows for an arbitrary bounded predicate."""
        self.require('ingest_sources', internal=True)
        cells = [f"system.main.octet_length(system.main.encode(COALESCE(CAST(\"{c}\" AS VARCHAR),'')))" for c in TABLES['ingest_sources'] if c != 'raw_output']
        length_sql = '+'.join(cells)
        cell_maxima = ','.join(f'COALESCE(max({cell}),0)' for cell in cells)
        pre = self.run(f"SELECT count(*),COALESCE(sum({length_sql}),0),{cell_maxima} FROM reader.netops.ingest_sources WHERE {pred}", params, cap=1)[0]
        if pre[0] > 1000 or pre[1] > 1_000_000 or max(pre[2:]) > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'source_metadata')
        invalid = self.run(f"SELECT count(*) FROM reader.netops.ingest_sources WHERE ({pred}) AND (NOT system.main.json_valid(source_json) OR (parser_json IS NOT NULL AND NOT system.main.json_valid(parser_json)))", params, cap=1)[0][0]
        if invalid:
            raise DomainError('SOURCE_UNAVAILABLE', 'source_json')
        # Only fixed provenance projections. Free manifests/metadata never fetched.
        paths = ('device_name', 'command', 'platform', 'raw_artifact', 'collected_at', 'actual_raw_sha256', 'body_sha256', 'input_artifact', 'input_sha256', 'parse_status', 'parser_fingerprint', 'parser_versions')
        projection = ','.join("system.main.json_extract(source_json,'$." + k + "')" for k in paths)
        rows = self.run(f'SELECT snapshot_id,device_name,command,parser_json,parsed_at,ingested_at,task_id,commit_token,system.main.sha256(raw_output),{projection} FROM reader.netops.ingest_sources WHERE {pred} LIMIT 1001', params)
        result, seen, tokens = [], {}, {}
        for row in rows:
            try:
                top_sid, device, command, parser, parsed, ingested, task, token, rawhash, *values = row
                text(top_sid); text(device); text(command)
                meta = {k: json.loads(v) if v is not None else None for k, v in zip(paths, values)}
                artifact = meta['raw_artifact']
                if meta['device_name'] != device or meta['command'] != command or artifact != {'table': 'netops.ingest_sources', 'snapshot_id': top_sid, 'device_name': device, 'command': command}:
                    raise ValueError()
                if not task or not token or meta['actual_raw_sha256'] != rawhash:
                    raise ValueError()
                for field in ('parser_fingerprint', 'input_sha256', 'body_sha256'):
                    if not isinstance(meta[field], str) or not re.fullmatch('[0-9a-fA-F]{64}', meta[field]):
                        raise ValueError()
                if meta['platform'] is not None and (not isinstance(meta['platform'], str) or len(meta['platform']) > 256):
                    raise ValueError()
                if not isinstance(meta['parse_status'], str) or not meta['parse_status'] or len(meta['parse_status']) > 256:
                    raise ValueError()
                if not isinstance(meta['input_artifact'], str) or not meta['input_artifact'] or len(meta['input_artifact']) > 256 or meta['input_artifact'].startswith('/') or '..' in meta['input_artifact'].split('/') or '://' in meta['input_artifact']:
                    raise ValueError()
                parser = json.loads(parser) if parser else None
                if meta['parse_status'] == 'parsed' and (not isinstance(parser, dict) or not parsed or not all(k in parser for k in ('tier', 'template_id', 'template_sha256'))):
                    raise ValueError()
                if parser is not None:
                    if not isinstance(parser, dict) or not all(isinstance(parser.get(k), str) and len(parser[k]) <= 256 for k in ('tier', 'template_id', 'template_sha256')) or not re.fullmatch('[0-9a-fA-F]{64}', parser['template_sha256']):
                        raise ValueError()
                    if not parser['template_id'] or parser['template_id'].startswith('/') or '..' in parser['template_id'].split('/') or '://' in parser['template_id']:
                        raise ValueError()
                    parser = {k: parser[k] for k in ('tier', 'template_id', 'template_sha256')}
                versions = meta.get('parser_versions')
                if not isinstance(versions, dict) or any(k in versions and (not isinstance(versions[k], str) or len(versions[k]) > 256) for k in ('ntc-templates', 'textfsm', 'netutils', 'netconan')):
                    raise ValueError()
                meta['parser_versions'] = {k: versions[k] for k in ('ntc-templates', 'textfsm', 'netutils', 'netconan') if k in versions}
                if top_sid in tokens and tokens[top_sid] != (task, token):
                    raise ValueError()
                tokens[top_sid] = (task, token)
                meta.update(snapshot_id=top_sid, device_name=device, command=command, parser=parser, parsed_at=clock(parsed, 'ingest_sources.parsed_at'), ingested_at=clock(ingested, 'ingest_sources.ingested_at'), collected_at=normalize_time(meta['collected_at']))
                key = (top_sid, device, command)
                if key in seen and seen[key] != meta:
                    raise ValueError()
                if key not in seen:
                    result.append(meta)
                    seen[key] = meta
            except (ValueError, TypeError, KeyError):
                raise DomainError('SOURCE_UNAVAILABLE', 'source_identity') from None
        self.sources.add('netops.ingest_sources')
        result.sort(key=lambda r: (r['snapshot_id'].encode(), r['device_name'].encode(), r['command'].encode()))
        return result

    def source_records_for_keys(self, keys):
        """Source records only for the given (snapshot_id, device, command) keys.

        NULL-safe: plain ``=`` would drop a NULL key and silently skip an invalid
        selected source record; ``IS NOT DISTINCT FROM`` keeps NULL/'' distinct
        and matches the body-match key semantics, surfacing selected invalid sources.
        """
        if not keys:
            return {}
        clauses, params = [], []
        for sid, device, command in keys:
            clauses.append('(snapshot_id IS NOT DISTINCT FROM ? AND device_name IS NOT DISTINCT FROM ? AND command IS NOT DISTINCT FROM ?)')
            params.extend([sid, device, command])
        records = self._materialize_sources(' OR '.join(clauses), params)
        return {(r['snapshot_id'], r['device_name'], r['command']): r for r in records}

    def bundle_source_coverage(self, ids, observed, requested):
        """B3 observed record cardinality, not manifest inference or a commit proof."""
        required = ('snapshot_id', 'commands_count', 'hosts_count')
        if not set(required) <= set(self.columns.get('bundle_ingests', {})):
            return {sid: {'status': 'unverified', 'reason': 'count_columns_missing', 'expected': None, 'observed': observed[sid], 'gap': None} for sid in ids}
        rows = self.bounded('bundle_ingests', required, predicate='TRUE' if requested == 'latest' else 'snapshot_id=?', params=[] if requested == 'latest' else [requested])
        result = {}
        for sid in ids:
            matches = [r for r in rows if r['snapshot_id'] == sid]
            if any(type(r[k]) is not int or r[k] < 0 for r in matches for k in required[1:] if r[k] is not None):
                raise DomainError('SOURCE_UNAVAILABLE', 'bundle_count_value')
            if len(matches) != 1:
                result[sid] = {'status': 'conflict' if matches else 'unverified', 'reason': 'multiple_bundle_rows' if matches else 'bundle_row_missing', 'bundle_rows': len(matches), 'expected': None, 'observed': observed[sid], 'gap': None}
            else:
                expected = {k: matches[0][k] for k in required[1:]}
                if any(v is None for v in expected.values()):
                    result[sid] = {'status': 'unverified', 'reason': 'null_counts', 'expected': expected, 'observed': observed[sid], 'gap': None}
                else:
                    gap = {k: expected[k] - observed[sid][k] for k in required[1:]}
                    result[sid] = {'status': 'complete' if all(v == 0 for v in gap.values()) else 'conflict' if any(v < 0 for v in gap.values()) else 'partial', 'expected': expected, 'observed': observed[sid], 'gap': gap}
        return result

    def _present(self, table):
        key = ('reader', 'netops', table)
        return key in self.tables and key not in self.views and ('temp', 'main', table) not in (self.tables | self.views)

    def _exists(self, table, sid):
        self.sources.add('netops.' + table)
        return bool(self.run(f'SELECT 1 FROM reader.netops.{table} WHERE snapshot_id=? LIMIT 1', [sid], cap=1))

    def _snapshot_present(self, sid):
        key = ('snapshot_present', sid)
        if key in self.cache:
            return self.cache[key]
        present = self._exists('bundle_ingests', sid) or self._exists('parsed_outputs', sid) or self._exists('ingest_sources', sid)
        self.cache[key] = present
        return present

    def _bundle_counts(self, sid):
        if not {'commands_count', 'hosts_count'} <= set(self.columns.get('bundle_ingests', {})):
            return None
        row = self.run('SELECT commands_count,hosts_count FROM reader.netops.bundle_ingests WHERE snapshot_id=? LIMIT 1', [sid], cap=1)
        if not row:
            return None
        cmd, hosts = row[0]
        if type(cmd) is not int or type(hosts) is not int:
            return None
        return {'commands_count': cmd, 'hosts_count': hosts}

    def _ingest_narrow(self, sid):
        row = self.run('SELECT count(*),count(DISTINCT device_name) FROM reader.netops.ingest_sources WHERE snapshot_id=?', [sid], cap=1)[0]
        gap = self.run('SELECT count(*) FROM (SELECT device_name,command FROM reader.netops.parsed_outputs WHERE snapshot_id=? EXCEPT SELECT device_name,command FROM reader.netops.ingest_sources WHERE snapshot_id=?) d', [sid, sid], cap=1)[0][0]
        self.sources.add('netops.ingest_sources')
        return {'count': int(row[0]), 'hosts': int(row[1]), 'gap': int(gap)}

    def _bundle_temporal_row(self, sid=None):
        if {'collected_at', 'ingested_at'} <= set(self.columns.get('bundle_ingests', {})):
            if sid is None:
                return self.run('SELECT snapshot_id,collected_at,ingested_at FROM reader.netops.bundle_ingests ORDER BY collected_at DESC NULLS LAST,ingested_at DESC NULLS LAST LIMIT 1', cap=1)
            return self.run('SELECT collected_at,ingested_at FROM reader.netops.bundle_ingests WHERE snapshot_id=? LIMIT 1', [sid], cap=1)
        return []

    def _light_temporal(self, sid, requested, deep=True):
        collected, ingested, rows = dict(UNKNOWN), dict(UNKNOWN), self._bundle_temporal_row(sid)
        if rows:
            collected = clock(rows[0][0], 'bundle_ingests.collected_at')
            ingested = clock(rows[0][1], 'bundle_ingests.ingested_at')
        if deep:
            facts = self._ingest_narrow(sid)
            if facts['count'] == 0:
                collected = dict(UNKNOWN)
            elif facts['gap']:
                collected = {**collected, 'status': 'partial', 'source': 'missing_persisted_source'}
            observed = {sid: {'commands_count': facts['count'], 'hosts_count': facts['hosts']}}
            bundle_coverage = self.bundle_source_coverage([sid], observed, requested)
            if facts['count'] and bundle_coverage[sid]['status'] != 'complete':
                collected = {**collected, 'status': 'conflict' if bundle_coverage[sid]['status'] == 'conflict' else 'partial', 'source': 'bundle_source_coverage'}
            provenance_status = 'missing' if facts['count'] == 0 else 'partial' if facts['gap'] or bundle_coverage[sid]['status'] != 'complete' else 'present'
            coverage = {'scope': 'snapshot', 'provenance_status': provenance_status, 'source_gap_records': facts['gap'],
                        'bundle_source_counts': bundle_coverage[sid], 'naive_date_limitation': 'recorded value order; no inferred UTC instant'}
            record_evidence_count = facts['count']
        else:
            bundle = self._bundle_counts(sid)
            count = bundle['commands_count'] if bundle else None
            if count is not None and count == 0:
                collected = dict(UNKNOWN)
            provenance_status = 'present' if (bundle and count is not None and bundle['hosts_count'] is not None) else 'unverified'
            bundle_counts = None
            if bundle:
                bundle_counts = {'status': 'unverified', 'reason': 'bundle_counts_without_ingest_scan',
                                 'expected': {'commands_count': bundle['commands_count'], 'hosts_count': bundle['hosts_count']},
                                 'observed': None, 'gap': None}
            coverage = {'scope': 'snapshot', 'provenance_status': provenance_status, 'source_gap_records': None,
                        'bundle_source_counts': bundle_counts, 'naive_date_limitation': 'recorded value order; no inferred UTC instant'}
            record_evidence_count = count if type(count) is int else 0
        family = {'timestamp': 'aware', 'timestamp_without_timezone': 'naive', 'date': 'date'}.get(collected['precision'])
        return sid, {'snapshot_id': sid, 'collected_at': collected, 'time_family': family,
                     'ordering_basis': {'aware': 'utc_instant', 'naive': 'recorded_naive_value', 'date': 'recorded_date_value'}.get(family),
                     'evidence_basis': 'bundle_ingests' if rows else 'none',
                     'ingested_at': ingested, 'record_updated_at': dict(UNKNOWN), 'event_time': dict(UNKNOWN),
                     'record_evidence_count': record_evidence_count, 'record_evidence': [], 'record_evidence_limit': 3,
                     'sample_truncated': False, 'coverage': coverage}

    def snapshot(self, requested='latest', temporal_sources=None, *, deep=None):
        for name in TIME_RELATIONS:
            self.require(name, ('snapshot_id',))
        if temporal_sources is not None:
            if not isinstance(temporal_sources, tuple) or len(set(temporal_sources)) != len(temporal_sources) or not set(temporal_sources) <= {'netops.parsed_outputs', 'netops.bundle_ingests', 'netops.ingest_sources'}:
                raise DomainError('INVALID_REQUEST')
            if not {'netops.parsed_outputs', 'netops.bundle_ingests', 'netops.ingest_sources'} <= set(temporal_sources):
                raise DomainError('OPERATION_DENIED')
        text(requested)
        self.require('ingest_sources', internal=True)
        if deep is None:
            deep = requested != 'latest'
        if requested == 'latest':
            rows = self._bundle_temporal_row()
            if rows and isinstance(rows[0][0], str) and rows[0][0]:
                self.sources.update(('netops.parsed_outputs', 'netops.bundle_ingests'))
                return self._light_temporal(rows[0][0], 'latest', deep=deep)
            return self._snapshot_legacy(requested)
        return self._snapshot_explicit(requested, deep=deep)

    def _snapshot_explicit(self, requested, deep=True):
        if not self._snapshot_present(requested):
            raise DomainError('SOURCE_UNAVAILABLE', 'snapshot_missing')
        self.sources.update(('netops.parsed_outputs', 'netops.bundle_ingests'))
        return self._light_temporal(requested, requested, deep=deep)

    def _snapshot_legacy(self, requested):
        union = ' UNION '.join(f'SELECT snapshot_id FROM reader.netops.{t}' for t in ('parsed_outputs', 'bundle_ingests', 'ingest_sources'))
        where, params = ('snapshot_id IS NOT NULL', []) if requested == 'latest' else ('snapshot_id=?', [requested])
        ids = [r[0] for r in self.run(f'SELECT snapshot_id FROM ({union}) c WHERE {where} LIMIT 513', params, cap=512)]
        if not ids:
            raise DomainError('SOURCE_UNAVAILABLE', 'snapshot_missing')
        if any(not isinstance(v, str) or not v or len(v) > 256 for v in ids):
            raise DomainError('RESULT_ROW_TOO_LARGE', 'snapshot_identity')
        sources = self.source_records(None if requested == 'latest' else requested)
        parsed_keys = self.bounded('parsed_outputs', ('snapshot_id', 'device_name', 'command'),
                                   predicate='TRUE' if requested == 'latest' else 'snapshot_id=?',
                                   params=[] if requested == 'latest' else [requested])
        facts = {(r['snapshot_id'], r['device_name'], r['command']) for r in parsed_keys}
        source_keys = {(r['snapshot_id'], r['device_name'], r['command']) for r in sources}
        for r in sources:
            present = (r['snapshot_id'], r['device_name'], r['command']) in facts
            if (r['parse_status'] == 'parsed') != present:
                raise DomainError('SOURCE_UNAVAILABLE', 'parsed_source_consistency')
        times = {sid: aggregate_time([r for r in sources if r['snapshot_id'] == sid]) for sid in ids}
        gaps = {sid: sum(k[0] == sid for k in facts - source_keys) for sid in ids}
        for candidate in ids:
            if gaps[candidate] and any(r['snapshot_id'] == candidate for r in sources):
                times[candidate] = {**UNKNOWN, 'status': 'partial', 'source': 'missing_persisted_source'}
        observed = {sid: {'commands_count': sum(r['snapshot_id'] == sid for r in sources),
                          'hosts_count': len({r['device_name'] for r in sources if r['snapshot_id'] == sid})} for sid in ids}
        bundle_coverage = self.bundle_source_coverage(ids, observed, requested)
        for candidate in ids:
            if bundle_coverage[candidate]['status'] != 'complete' and any(r['snapshot_id'] == candidate for r in sources):
                times[candidate] = {**UNKNOWN, 'status': 'conflict' if bundle_coverage[candidate]['status'] == 'conflict' else 'partial', 'source': 'bundle_source_coverage'}
        sid = select_latest(times) if requested == 'latest' else requested
        self.sources.update(('netops.parsed_outputs', 'netops.bundle_ingests'))
        stamp = times[sid]
        family = {'timestamp': 'aware', 'timestamp_without_timezone': 'naive', 'date': 'date'}.get(stamp['precision'])
        records = [r for r in sources if r['snapshot_id'] == sid]
        return sid, {'snapshot_id': sid, 'collected_at': stamp, 'time_family': family,
                     'ordering_basis': {'aware': 'utc_instant', 'naive': 'recorded_naive_value', 'date': 'recorded_date_value'}.get(family),
                     'evidence_basis': 'persisted_ingest_sources', 'ingested_at': dict(UNKNOWN), 'record_updated_at': dict(UNKNOWN), 'event_time': dict(UNKNOWN),
                     'record_evidence_count': len(records), 'record_evidence': [self.public_source(r) for r in records[:3]], 'record_evidence_limit': 3, 'sample_truncated': len(records) > 3,
                     'coverage': {'scope': 'snapshot', 'provenance_status': 'partial' if gaps[sid] and records or records and bundle_coverage[sid]['status'] != 'complete' else 'present' if records else 'missing', 'source_gap_records': gaps[sid], 'bundle_source_counts': bundle_coverage[sid], 'naive_date_limitation': 'recorded value order; no inferred UTC instant'}}

    @staticmethod
    def public_source(r):
        return {k: r[k] for k in ('device_name', 'command', 'collected_at', 'parser', 'parsed_at', 'ingested_at', 'parse_status', 'parser_fingerprint', 'parser_versions')}

    def parsed(self, sid):
        key = ('parsed', sid)
        if key in self.cache:
            return self.cache[key]
        rows = self.bounded('parsed_outputs', TABLES['parsed_outputs'], predicate='snapshot_id=?', params=[sid])
        records = self.source_records(sid)
        source = {(r['device_name'], r['command']): r for r in records}
        count = 0
        seen = set()
        for r in rows:
            try:
                if not isinstance(r['device_name'], str) or not r['device_name']:
                    raise ValueError()
                identity = (r['device_name'], r['command'])
                if identity in seen:
                    raise ValueError()
                seen.add(identity)
                entries = json.loads(r['parsed_data']) if isinstance(r['parsed_data'], str) else r['parsed_data']
                if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
                    raise ValueError()
                r['entries'] = [{str(k).lower(): v for k, v in entry.items()} for entry in entries]
                count += len(entries)
                if count > 1000:
                    raise DomainError('RESULT_BUDGET_EXCEEDED', 'expanded_entries')
                s = source.get((r['device_name'], r['command']))
                if s and s['parse_status'] != 'parsed':
                    raise ValueError()
            except DomainError:
                raise
            except (ValueError, TypeError):
                raise DomainError('SOURCE_UNAVAILABLE', 'parsed_shape') from None
        actual = {(r['device_name'], r['command']) for r in rows}
        if any(r['parse_status'] == 'parsed' and (r['device_name'], r['command']) not in actual for r in records):
            raise DomainError('SOURCE_UNAVAILABLE', 'parsed_source_without_fact')
        rows.sort(key=lambda r: ((r['device_name'] or '').encode(), (r['command'] or '').encode()))
        self.cache[key] = rows
        return rows

    def inventory(self):
        if 'inventory' not in self.cache:
            self.cache['inventory'] = self.bounded('devices', TABLES['devices'])
            if any(not isinstance(r['hostname'], str) or not r['hostname'] for r in self.cache['inventory']):
                raise DomainError('SOURCE_UNAVAILABLE', 'inventory_identity')
        return self.cache['inventory']

    # ---- OW144 bounded audit / read primitives (opt-in, legacy untouched) ----

    def _audit_bad(self, sql, params, *, code, reason):
        row = self.run(sql, list(params), cap=1)
        if row and row[0][0]:
            raise DomainError(code, reason)
        return row[0][0] if row else 0

    def _clock_column_type(self, col):
        return (self.columns.get('ingest_sources', {}).get(col) or 'VARCHAR').upper()

    @staticmethod
    def _clock_fragments(col):
        shape = (f"system.main.regexp_matches({col}, "
                 f"'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}[T ][0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}"
                 f"([.][0-9]{{1,6}})?(Z|[+-][0-9]{{2}}:[0-9]{{2}})?$')")
        aware = f"system.main.regexp_matches({col}, '(Z|[+-][0-9]{{2}}:[0-9]{{2}})$')"
        off_h = f"system.main.regexp_extract({col}, '[+-]([0-9]{{2}}):([0-9]{{2}})$', 1)"
        off_m = f"system.main.regexp_extract({col}, '[+-]([0-9]{{2}}):([0-9]{{2}})$', 2)"
        naive = f"try_cast({col} AS TIMESTAMP)"
        instant = f"try_cast({col} AS TIMESTAMPTZ)"
        day = f"try_cast(system.main.substr({col},1,10) AS DATE)"
        micros = f"CASE WHEN {aware} THEN system.main.epoch_us({instant}) ELSE system.main.epoch_us({naive}) END"
        return dict(shape=shape, aware=aware, off_h=off_h, off_m=off_m, naive=naive, instant=instant, day=day, micros=micros)

    @staticmethod
    def _clock_fast(c, col):
        return (f"{c['shape']} AND system.main.substr({col},1,4) != '0000' "
                f"AND system.main.substr({col},12,2) < '24' AND system.main.substr({col},15,2) < '60' "
                f"AND system.main.substr({col},18,2) < '60' "
                f"AND ({c['off_h']} = '' OR ({c['off_h']} < '24' AND {c['off_m']} < '60')) "
                f"AND {c['day']} IS NOT NULL AND {c['micros']} IS NOT NULL "
                f"AND {c['micros']} BETWEEN ? AND ?")

    def _typed_clock_range(self, sid, col, col_type):
        """Typed TIMESTAMP/TIMESTAMPTZ columns must honor the frozen clock() year 1..9999 range.

        DuckDB accepts year 10000 / BC in TIMESTAMP and the driver fetches them as a
        string the frozen clock() then rejects (SOURCE_UNAVAILABLE/clock_value);
        'type valid => Python clock valid' is false. Typed ±infinity maps to
        datetime.max/min (epoch_us NULL but col IS NOT NULL), which clock() accepts.
        TIMESTAMPTZ also checks its local CAST so a UTC-in-range instant that
        overflows the driver timezone conversion is caught.
        """
        S = 'reader.netops.ingest_sources'
        def fin_out(expr):
            return f'({expr} IS NOT NULL AND ({expr} < {_CLOCK_MIN_US} OR {expr} > {_CLOCK_MAX_US}))'
        conds = fin_out(f'system.main.epoch_us({col})')
        if col_type in ('TIMESTAMPTZ', 'TIMESTAMP WITH TIME ZONE'):
            conds += f" OR {fin_out(f'system.main.epoch_us(CAST({col} AS TIMESTAMP))')}"
        self._audit_bad(
            f"SELECT count(*) FROM {S} WHERE snapshot_id=? AND {col} IS NOT NULL AND ({conds})",
            [sid], code='SOURCE_UNAVAILABLE', reason='clock_value')

    def _clock_fallback(self, sid, col, source_label):
        col_type = self._clock_column_type(col)
        if col_type in ('TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMPTZ'):
            self._typed_clock_range(sid, col, col_type)
            return
        c = self._clock_fragments(col)
        fast = self._clock_fast(c, col)
        params = [sid, _CLOCK_MIN_US, _CLOCK_MAX_US]
        bad = self.run(
            f"SELECT count(*) FROM reader.netops.ingest_sources WHERE snapshot_id=? AND {col} IS NOT NULL AND NOT ({fast})",
            params, cap=1)[0][0]
        if not bad:
            return
        cell = f"system.main.octet_length(system.main.encode(COALESCE(CAST({col} AS VARCHAR),'')))"
        pre = self.run(
            f"SELECT count(DISTINCT {col}), COALESCE(sum({cell}),0), COALESCE(max({cell}),0) "
            f"FROM reader.netops.ingest_sources WHERE snapshot_id=? AND {col} IS NOT NULL AND NOT ({fast})",
            params, cap=1)[0]
        if pre[0] > 1000 or pre[1] > 1_000_000 or pre[2] > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'clock_metadata')
        vals = self.run(
            f"SELECT DISTINCT {col} FROM reader.netops.ingest_sources WHERE snapshot_id=? AND {col} IS NOT NULL AND NOT ({fast}) LIMIT 1001",
            params, cap=1000)
        for (v,) in vals:
            clock(v, source_label)

    def _source_scalar_audit(self, sid):
        """Single multi-path projection for all fixed source_json scalar fields.

        r1 OOM'd because the scalar-provenance OR re-parsed the full source_json
        column once per json_type/json_extract_string predicate. This extracts
        the fixed 12 top-level paths ONCE into a MATERIALIZED narrow CTE (bound
        ?::VARCHAR[] path list), then validates only the short JSON slots via
        system.main.list_extract (1-based ordinal). Top-level duplicate keys keep
        first semantics (json_extract with a path list returns the first match,
        same as per-path json_extract); raw only contributes its SQL sha256.
        """
        S = 'reader.netops.ingest_sources'
        def sl(n):
            return f'system.main.list_extract(p,{n})'
        def typ(n):
            return f'system.main.json_type({sl(n)})'
        def val(n):
            return f"system.main.json_extract_string({sl(n)},'$')"
        sql = f"""
WITH proj AS MATERIALIZED (
  SELECT system.main.row_number() OVER (ORDER BY snapshot_id, device_name, command) AS rid,
         snapshot_id, device_name, command,
         system.main.sha256(raw_output) AS rawhash,
         system.main.json_extract(source_json, ?::VARCHAR[]) AS p
  FROM {S} WHERE snapshot_id=?
)
SELECT count(*) FROM proj WHERE (
  snapshot_id IS NULL OR snapshot_id='' OR system.main.length(snapshot_id)>256
  OR device_name IS NULL OR device_name='' OR system.main.length(device_name)>256
  OR command IS NULL OR command='' OR system.main.length(command)>256
  OR {typ(1)} IS DISTINCT FROM 'VARCHAR'
  OR {val(1)} IS DISTINCT FROM device_name
  OR {typ(2)} IS DISTINCT FROM 'VARCHAR'
  OR {val(2)} IS DISTINCT FROM command
  OR ({typ(3)} IS NOT NULL AND {typ(3)} NOT IN ('NULL','VARCHAR'))
  OR ({typ(3)}='VARCHAR' AND system.main.length({val(3)})>256)
  OR (rawhash IS NOT NULL AND ({typ(6)} IS DISTINCT FROM 'VARCHAR' OR {val(6)} IS DISTINCT FROM rawhash))
  OR (rawhash IS NULL AND {typ(6)} IS NOT NULL AND {typ(6)} IS DISTINCT FROM 'NULL')
  OR {typ(7)} IS DISTINCT FROM 'VARCHAR'
  OR NOT system.main.regexp_matches({val(7)},'^[0-9a-fA-F]{{64}}$')
  OR {typ(9)} IS DISTINCT FROM 'VARCHAR'
  OR NOT system.main.regexp_matches({val(9)},'^[0-9a-fA-F]{{64}}$')
  OR {typ(10)} IS DISTINCT FROM 'VARCHAR'
  OR {val(10)} IS NULL
  OR {val(10)}=''
  OR system.main.length({val(10)})>256
  OR {typ(11)} IS DISTINCT FROM 'VARCHAR'
  OR NOT system.main.regexp_matches({val(11)},'^[0-9a-fA-F]{{64}}$')
  OR {typ(8)} IS DISTINCT FROM 'VARCHAR'
  OR {val(8)} IS NULL
  OR {val(8)}=''
  OR system.main.length({val(8)})>256
  OR {val(8)} LIKE '/%'
  OR {val(8)} LIKE '%://%'
  OR {val(8)}='..'
  OR {val(8)} LIKE '../%'
  OR {val(8)} LIKE '%/../%'
  OR {val(8)} LIKE '%/..'
  OR {typ(12)} IS DISTINCT FROM 'OBJECT'
)
"""
        self._audit_bad(sql, [list(_SOURCE_PATHS), sid], code='SOURCE_UNAVAILABLE', reason='source_identity')

    def source_audit(self, sid):
        key = ('source_audit', sid)
        if key in self.cache:
            return
        self.require('ingest_sources', internal=True)
        self.require('parsed_outputs', ('snapshot_id', 'device_name', 'command'))
        self.sources.add('netops.ingest_sources')
        self.sources.add('netops.parsed_outputs')
        S = 'reader.netops.ingest_sources'
        # 1. staged JSON validity (before any json_each / json_extract)
        self._audit_bad(
            f"SELECT count(*) FROM {S} WHERE snapshot_id=? AND (NOT system.main.json_valid(source_json) OR (parser_json IS NOT NULL AND NOT system.main.json_valid(parser_json)))",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_json')
        # 2+3. scalar identity + provenance via single multi-path projection
        self._source_scalar_audit(sid)
        # 4. task / token presence and per-SID consistency
        self._audit_bad(
            f"SELECT count(*) FROM {S} WHERE snapshot_id=? AND (task_id IS NULL OR task_id='' OR commit_token IS NULL OR commit_token='')",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_identity')
        task_groups = self.run(f"SELECT count(*) FROM (SELECT task_id, commit_token FROM {S} WHERE snapshot_id=? GROUP BY task_id, commit_token) d", [sid], cap=1)[0][0]
        if task_groups > 1:
            raise DomainError('SOURCE_UNAVAILABLE', 'source_identity')
        # 5. raw_artifact exact object via json_each last-wins
        self._audit_bad(
            """WITH base AS MATERIALIZED (
  SELECT system.main.row_number() OVER (ORDER BY snapshot_id, device_name, command) AS rid,
         snapshot_id, device_name, command,
         system.main.json_extract(source_json, '$.raw_artifact') AS ra
  FROM reader.netops.ingest_sources WHERE snapshot_id=?
), parts AS (
  SELECT b.rid, j.key, j.id, j.value, j.type
  FROM base b, LATERAL system.main.json_each(b.ra) j
), last_ids AS (
  SELECT rid, key, max(id) AS id FROM parts GROUP BY rid, key
), last_parts AS (
  SELECT p.* FROM parts p JOIN last_ids l ON p.rid=l.rid AND p.key=l.key AND p.id=l.id
)
SELECT count(*) FROM base b WHERE
  system.main.json_type(b.ra) IS DISTINCT FROM 'OBJECT'
  OR (SELECT count(*) FROM last_parts p WHERE p.rid=b.rid) IS DISTINCT FROM 4
  OR EXISTS (
    SELECT 1 FROM last_parts p WHERE p.rid=b.rid AND (
      p.key NOT IN ('table','snapshot_id','device_name','command')
      OR p.type IS DISTINCT FROM 'VARCHAR'
      OR system.main.json_extract_string(p.value,'$') IS DISTINCT FROM
         CASE p.key WHEN 'table' THEN 'netops.ingest_sources'
                    WHEN 'snapshot_id' THEN b.snapshot_id
                    WHEN 'device_name' THEN b.device_name
                    WHEN 'command' THEN b.command END
    )
  )""",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_identity')
        # 6. parser must be dict (None allowed when not parsed) + 3 required last values
        parsed_empty = "parsed_at IS NULL OR parsed_at=''" if self._clock_column_type('parsed_at') == 'VARCHAR' else "parsed_at IS NULL"
        self._audit_bad(
            f"SELECT count(*) FROM {S} WHERE snapshot_id=? AND ("
            f"(parser_json IS NOT NULL AND system.main.json_type(parser_json) IS DISTINCT FROM 'NULL' AND system.main.json_type(parser_json) IS DISTINCT FROM 'OBJECT')"
            f" OR (system.main.json_extract_string(source_json,'$.parse_status')='parsed' AND ("
            f"system.main.json_type(parser_json) IS DISTINCT FROM 'OBJECT' OR {parsed_empty}))"
            f")",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_identity')
        self._audit_bad(
            """WITH base AS (
  SELECT system.main.row_number() OVER (ORDER BY snapshot_id, device_name, command) AS rid, parser_json
  FROM reader.netops.ingest_sources WHERE snapshot_id=?
), parts AS (
  SELECT b.rid, j.key, j.id, j.value, j.type FROM base b, LATERAL system.main.json_each(b.parser_json) j
), last_ids AS (
  SELECT rid, key, max(id) AS id FROM parts GROUP BY rid, key
), last_parts AS (
  SELECT p.* FROM parts p JOIN last_ids l ON p.rid=l.rid AND p.key=l.key AND p.id=l.id
)
SELECT count(*) FROM base b WHERE system.main.json_type(b.parser_json)='OBJECT' AND (
  (SELECT count(DISTINCT p.key) FROM last_parts p WHERE p.rid=b.rid AND p.key IN ('tier','template_id','template_sha256')) IS DISTINCT FROM 3
  OR EXISTS (SELECT 1 FROM last_parts p WHERE p.rid=b.rid AND p.key IN ('tier','template_id','template_sha256') AND (
    p.type IS DISTINCT FROM 'VARCHAR'
    OR system.main.length(system.main.json_extract_string(p.value,'$'))>256
    OR (p.key='template_sha256' AND NOT system.main.regexp_matches(system.main.json_extract_string(p.value,'$'),'^[0-9a-fA-F]{64}$'))
    OR (p.key='template_id' AND (
      system.main.json_extract_string(p.value,'$') IS NULL OR system.main.json_extract_string(p.value,'$')=''
      OR system.main.json_extract_string(p.value,'$') LIKE '/%'
      OR system.main.json_extract_string(p.value,'$') LIKE '%://%'
      OR system.main.json_extract_string(p.value,'$')='..'
      OR system.main.json_extract_string(p.value,'$') LIKE '../%'
      OR system.main.json_extract_string(p.value,'$') LIKE '%/../%'
      OR system.main.json_extract_string(p.value,'$') LIKE '%/..'
    ))
  ))
)""",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_identity')
        # 7. parser_versions known keys (object already enforced in step 3)
        self._audit_bad(
            """WITH base AS MATERIALIZED (
  SELECT system.main.row_number() OVER (ORDER BY snapshot_id, device_name, command) AS rid,
         system.main.json_extract(source_json, '$.parser_versions') AS pv
  FROM reader.netops.ingest_sources WHERE snapshot_id=?
), parts AS (
  SELECT b.rid, j.key, j.id, j.value, j.type
  FROM base b, LATERAL system.main.json_each(b.pv) j
), last_ids AS (
  SELECT rid, key, max(id) AS id FROM parts GROUP BY rid, key
), last_parts AS (
  SELECT p.* FROM parts p JOIN last_ids l ON p.rid=l.rid AND p.key=l.key AND p.id=l.id
)
SELECT count(*) FROM base b WHERE EXISTS (
  SELECT 1 FROM last_parts p WHERE p.rid=b.rid AND p.key IN ('ntc-templates','textfsm','netutils','netconan') AND (
    p.type IS DISTINCT FROM 'VARCHAR' OR system.main.length(system.main.json_extract_string(p.value,'$'))>256
  )
)""",
            [sid], code='SOURCE_UNAVAILABLE', reason='source_identity')
        # 8. clocks validity (fastpath stays in SQL; non-fast non-null → bounded Python clock())
        self._clock_fallback(sid, 'parsed_at', 'ingest_sources.parsed_at')
        self._clock_fallback(sid, 'ingested_at', 'ingest_sources.ingested_at')
        # 9. same-key duplicate → full legacy validation via source_records_for_keys
        dup = self.run(
            f"SELECT snapshot_id, device_name, command FROM {S} WHERE snapshot_id=? GROUP BY snapshot_id, device_name, command HAVING count(*)>1 LIMIT 1001",
            [sid], cap=1000)
        if dup:
            self.source_records_for_keys([(r[0], r[1], r[2]) for r in dup])
        # 10. parsed bidirectional consistency
        self._audit_bad(
            f"SELECT count(*) FROM {S} i WHERE i.snapshot_id=? AND system.main.json_extract_string(i.source_json,'$.parse_status')='parsed' "
            f"AND NOT EXISTS (SELECT 1 FROM reader.netops.parsed_outputs p WHERE p.snapshot_id=i.snapshot_id AND p.device_name=i.device_name AND p.command=i.command)",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_source_without_fact')
        self._audit_bad(
            f"SELECT count(*) FROM reader.netops.parsed_outputs p WHERE p.snapshot_id=? AND EXISTS ("
            f"SELECT 1 FROM {S} i WHERE i.snapshot_id=p.snapshot_id AND i.device_name=p.device_name AND i.command=p.command "
            f"AND system.main.json_extract_string(i.source_json,'$.parse_status') IS DISTINCT FROM 'parsed')",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_shape')
        self.cache[key] = True

    def parsed_audit(self, sid):
        key = ('parsed_audit', sid)
        if key in self.cache:
            return
        self.require('parsed_outputs', ('snapshot_id', 'device_name', 'command', 'parsed_data'))
        self.sources.add('netops.parsed_outputs')
        P = 'reader.netops.parsed_outputs'
        self._audit_bad(
            f"SELECT count(*) FROM {P} WHERE snapshot_id=? AND (NOT system.main.json_valid(parsed_data) OR system.main.json_type(parsed_data) IS DISTINCT FROM 'ARRAY')",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_shape')
        self._audit_bad(
            f"SELECT count(*) FROM {P} WHERE snapshot_id=? AND (device_name IS NULL OR device_name='')",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_shape')
        self._audit_bad(
            f"SELECT count(*) FROM (SELECT device_name, command FROM {P} WHERE snapshot_id=? GROUP BY device_name, command HAVING count(*)>1) d",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_shape')
        self._audit_bad(
            f"SELECT count(*) FROM {P} p, LATERAL system.main.json_each(p.parsed_data) e WHERE p.snapshot_id=? AND system.main.json_type(e.value) IS DISTINCT FROM 'OBJECT'",
            [sid], code='SOURCE_UNAVAILABLE', reason='parsed_shape')
        self.cache[key] = True

    def _members(self, table, sid, *, null_reason):
        S = f'reader.netops.{table}'
        self.sources.add('netops.' + table)
        self._audit_bad(
            f"SELECT count(*) FROM {S} WHERE snapshot_id=? AND (device_name IS NULL OR device_name='')",
            [sid], code='SOURCE_UNAVAILABLE', reason=null_reason)
        pre = self.run(
            f"SELECT count(DISTINCT device_name), COALESCE(max(system.main.octet_length(system.main.encode(COALESCE(CAST(device_name AS VARCHAR),'')))),0) FROM {S} WHERE snapshot_id=?",
            [sid], cap=1)[0]
        if pre[0] > 1000 or pre[1] > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'membership_metadata')
        return [r[0] for r in self.run(f"SELECT DISTINCT device_name FROM {S} WHERE snapshot_id=? ORDER BY device_name LIMIT 1001", [sid], cap=1000)]

    def parsed_membership(self, sid):
        key = ('parsed_membership', sid)
        if key not in self.cache:
            self.require('parsed_outputs', ('snapshot_id', 'device_name'))
            self.cache[key] = self._members('parsed_outputs', sid, null_reason='parsed_shape')
        return self.cache[key]

    def source_membership(self, sid):
        key = ('source_membership', sid)
        if key not in self.cache:
            self.require('ingest_sources', internal=True)
            self.cache[key] = self._members('ingest_sources', sid, null_reason='source_identity')
        return self.cache[key]

    def protocol_header(self, sid, commands):
        self.require('parsed_outputs', ('snapshot_id', 'device_name', 'command', 'parsed_data'))
        self.sources.add('netops.parsed_outputs')
        P = 'reader.netops.parsed_outputs'
        placeholders = ','.join('?' for _ in commands)
        row = self.run(
            f"SELECT count(*), count(DISTINCT device_name), COALESCE(sum(system.main.json_array_length(parsed_data)),0), "
            f"COALESCE(sum(CASE WHEN system.main.json_array_length(parsed_data)=0 THEN 1 ELSE 0 END),0) "
            f"FROM {P} WHERE snapshot_id=? AND command IN ({placeholders})",
            [sid, *commands], cap=1)[0]
        rows, hosts, total_entries, empty = int(row[0]), int(row[1]), int(row[2]), int(row[3])
        if rows == 0:
            return {'present': False, 'observed_devices': [], 'total_entries': 0, 'empty_arrays': 0, 'command_not_parsed': True}
        devices = [r[0] for r in self.run(
            f"SELECT DISTINCT device_name FROM {P} WHERE snapshot_id=? AND command IN ({placeholders}) ORDER BY device_name LIMIT 1001",
            [sid, *commands], cap=1000)]
        return {'present': True, 'observed_devices': devices, 'total_entries': total_entries, 'empty_arrays': empty, 'command_not_parsed': False}

    def protocol_entries(self, sid, commands, devices=None):
        self.require('parsed_outputs', ('snapshot_id', 'device_name', 'command', 'parsed_data'))
        self.sources.add('netops.parsed_outputs')
        P = 'reader.netops.parsed_outputs'
        clauses = ['snapshot_id=?', 'command IN (' + ','.join('?' for _ in commands) + ')']
        params = [sid, *commands]
        if devices:
            clauses.append('device_name IN (' + ','.join('?' for _ in devices) + ')')
            params.extend(devices)
        pred = ' AND '.join(clauses)
        val = "system.main.octet_length(system.main.encode(COALESCE(CAST(e.value AS VARCHAR),'')))"
        idc = ("system.main.octet_length(system.main.encode(COALESCE(CAST(p.device_name AS VARCHAR),'')))"
               "+system.main.octet_length(system.main.encode(COALESCE(CAST(p.command AS VARCHAR),'')))"
               "+system.main.octet_length(system.main.encode(COALESCE(CAST(e.key AS VARCHAR),'')))")
        pre = self.run(
            f"SELECT count(*), COALESCE(sum({idc}+{val}),0), COALESCE(max({idc}),0), COALESCE(max({val}),0) "
            f"FROM {P} p, LATERAL system.main.json_each(p.parsed_data) e WHERE {pred}",
            params, cap=1)[0]
        if pre[0] > 1000 or pre[1] > 1_000_000 or pre[2] > 65536 or pre[3] > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'entry_metadata')
        rows = self.run(
            f"SELECT p.device_name, p.command, e.key, e.value FROM {P} p, LATERAL system.main.json_each(p.parsed_data) e "
            f"WHERE {pred} ORDER BY p.device_name ASC NULLS LAST, p.command ASC NULLS LAST, CAST(e.key AS BIGINT) LIMIT 1001",
            params, cap=1000)
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def source_platforms(self, sid, devices):
        if not devices:
            return {}
        self.sources.add('netops.ingest_sources')
        placeholders = ','.join('?' for _ in devices)
        rows = self.run(
            f"SELECT device_name, system.main.json_extract_string(source_json,'$.platform') FROM reader.netops.ingest_sources "
            f"WHERE snapshot_id=? AND device_name IN ({placeholders}) "
            f"QUALIFY system.main.row_number() OVER (PARTITION BY device_name ORDER BY command DESC NULLS LAST)=1",
            [sid, *devices], cap=1000)
        return {r[0]: r[1] for r in rows}


@contextmanager
def session(required=(), *, timeout=5000):
    if type(timeout) is not int or timeout <= 0:
        raise DomainError('INVALID_REQUEST')
    start = time.monotonic()
    policy = load_query_policy()
    for t in required:
        if ('netops', t) not in policy.relations:
            raise DomainError('OPERATION_DENIED')
    with open_read_connection() as read:
        if time.monotonic() - start >= min(timeout, 5000) / 1000:
            raise DomainError('EXECUTION_TIMEOUT')
        q = Session(read, policy, timeout=min(timeout, 5000))
        for t in required:
            q.require(t)
        yield q


def execute(action, *, required=(), timeout=5000, byte_cap=65536, char_cap=None):
    try:
        if type(timeout) is not int or timeout <= 0:
            raise DomainError('INVALID_REQUEST')
        with session(required, timeout=timeout) as q:
            result = action(q)
            result.setdefault('status', 'success')
            result['reader'] = {'backend': 'ducklake', 'read_version': q.version, 'policy_version': q.policy.version, 'transport': 'bounded_domain'}
            result['limits'] = {'response_bytes': byte_cap, 'timeout_ms': min(timeout, 5000), 'threads': 1, 'memory': '256MB', 'timeout_scope': 'post_connect_execution; setup_checked_after_return', 'physical_sources': sorted(q.sources)}
            if 'operation' in result:
                result['limits'].update(rows=50, cell_chars=256)
            return finalize(result, byte_cap=byte_cap, char_cap=char_cap)
    except DomainError as exc:
        return failure(exc.code, exc.reason)
    except Exception as exc:
        return failure(getattr(exc, 'code', 'WAREHOUSE_UNAVAILABLE'))


def pick(entry, *names):
    for name in names:
        v = entry.get(name)
        if isinstance(v, list):
            v = v[0] if v else None
        if v is not None and v != '':
            if isinstance(v, (dict, list, bool)) or not isinstance(v, (str, int, float)):
                raise DomainError('SOURCE_UNAVAILABLE', 'parsed_field_type')
            return v
    return None


def historical(q, sid, devices=None):
    rows = q.parsed(sid)
    members = sorted({r['device_name'] for r in rows} | {r['device_name'] for r in q.source_records(sid)})
    selected = [d for d in members if not devices or d in devices]
    facts = {d: {'platform': None, 'model': None, 'os_version': None, 'record_source_platform': None} for d in selected}
    for source in q.source_records(sid):
        if source['device_name'] in facts:
            facts[source['device_name']]['record_source_platform'] = source.get('platform')
    for r in rows:
        if r['device_name'] not in facts or r['command'] != 'show version':
            continue
        if r['entries']:
            e = r['entries'][0]
            facts[r['device_name']].update(platform=pick(e, 'platform'), model=pick(e, 'model', 'hardware'), os_version=pick(e, 'os_version', 'junos_version', 'version'))
    return members, facts


def inspect_devices(devices, *, snapshot_id=None):
    def action(q):
        ds = strings(devices)
        if snapshot_id is None:
            inventory = q.inventory()
            target = sorted(ds or [r['hostname'] for r in inventory])
            found = {r['hostname']: {k: v for k, v in r.items() if k != 'hostname' and v is not None} for r in inventory if r['hostname'] in target}
            for facts in found.values():
                if facts.get('ip_address') is not None:
                    facts['mgmt_ip'] = facts['ip_address']
            unknown = {d: [f for f in ('platform', 'loopback', 'local_as', 'mgmt_ip', 'role') if found[d].get(f) is None] for d in sorted(found)[:50]}
            return {'found': dict(list(sorted(found.items()))[:50]), 'current_inventory': dict(list(sorted(found.items()))[:50]), 'unknown_facts': unknown, 'unknown_devices': sorted(set(ds) - set(found)), 'scope': 'current_inventory', 'total_rows': len(found), 'sample_truncated': len(found) > 50}
        sid, temporal = q.snapshot(snapshot_id)
        members, facts = historical_bounded(q, sid, ds)
        keys = sorted(facts)[:50]
        return {'found': {}, 'current_inventory': None, 'unknown_facts': {}, 'snapshot_id': sid, 'snapshot_members': keys, 'historical_facts': {k: facts[k] for k in keys}, 'historical_unknown_facts': {k: [f for f, v in facts[k].items() if v is None] for k in keys}, 'unknown_devices': sorted(set(ds) - set(members)), 'scope': 'snapshot_membership', 'temporal': temporal, 'total_rows': len(facts), 'sample_truncated': len(facts) > 50}
    return execute(action, required=('devices',) if snapshot_id is None else tuple(TIME_RELATIONS))


def protocol(q, sid, kind, *, devices=None):
    from olav_skills.netops.core.topology_engine import _canonicalise_interface, _make_link_id
    rows = q.parsed(sid)
    commands = {'interface': ('show ip interface brief', 'show interfaces terse', 'show interfaces'), 'bgp': ('show ip bgp summary', 'show bgp summary'), 'l2': ('show lldp neighbors', 'show lldp neighbors detail', 'show cdp neighbors', 'show cdp neighbors detail'), 'ospf': ('show ip ospf neighbor', 'show ospf neighbor')}
    chosen = [r for r in rows if r['command'] in commands[kind]]
    if not chosen:
        return [], {'status': 'missing', 'count': None, 'reason': 'command_not_parsed'}
    observed_devices = {r['device_name'] for r in chosen}
    if devices is not None:
        chosen = [r for r in chosen if r['device_name'] in devices]
    result, missing = [], sorted(set(devices) - observed_devices) if devices is not None else []
    # Use the existing hostname resolver against a bounded snapshot registry.
    from olav_skills.netops.core.hostname_registry import canonicalize, refresh
    class Registry:
        def execute(self, sql):
            members = {r['device_name'] for r in rows} | {r['device_name'] for r in q.source_records(sid)}
            self.values = [(d,) for d in sorted(members, key=lambda d: (-len(d), d))]
            return self
        def fetchall(self):
            return self.values
    registry = Registry()
    refresh(registry)
    for r in chosen:
        for e in r['entries']:
            device = r['device_name']
            if kind == 'interface':
                name = pick(e, 'interface', 'interface_name')
                status, proto = pick(e, 'status', 'link_state', 'admin_state'), pick(e, 'proto', 'protocol')
                if name is None:
                    missing.append(device)
                    continue
                if status is None or proto is None:
                    missing.append(device)
                intf, valid = _canonicalise_interface(str(name))
                if not valid:
                    missing.append(device)
                    continue
                result.append({'device_name': device, 'interface': intf, 'ip_address': pick(e, 'ip_address', 'address'), 'status': status, 'proto': proto})
            elif kind == 'bgp':
                neighbor, state = pick(e, 'bgp_neighbor', 'neighbor', 'neighbor_ip'), pick(e, 'state_or_prefixes_received', 'state_pfxrcd', 'state')
                if neighbor is None or state is None:
                    missing.append(device)
                    continue
                state = str(state)
                remote_as = pick(e, 'neighbor_as', 'remote_as', 'as')
                local_as = pick(e, 'local_as')
                for label, value in (('remote', remote_as), ('local', local_as)):
                    if value is not None and not str(value).isdigit():
                        raise DomainError('SOURCE_UNAVAILABLE', 'bgp_as_type')
                if remote_as is None:
                    missing.append(device)
                result.append({'device': device, 'device_name': device, 'neighbor_ip': neighbor, 'bgp_neighbor': neighbor, 'neighbor_as': int(remote_as) if remote_as is not None else None, 'local_as': int(local_as) if local_as is not None else None, 'router_id': pick(e, 'router_id'), 'state': 'Established' if state.isdigit() else state, 'state_or_prefixes_received': state, 'uptime': pick(e, 'up_down', 'uptime')})
            elif kind == 'l2':
                local = pick(e, 'local_interface', 'local_port', 'local_intf', 'interface')
                neighbor = pick(e, 'neighbor', 'neighbor_name', 'neighbor_device', 'destination_host', 'device_id', 'system_name')
                remote = pick(e, 'neighbor_interface', 'remote_interface', 'neighbor_port_id', 'port_id', 'remote_port', 'port')
                if not local or not neighbor or not remote:
                    missing.append(device)
                    continue
                a, av = _canonicalise_interface(str(local)); b, bv = _canonicalise_interface(str(remote))
                neighbor = canonicalize(str(neighbor), registry)
                device = canonicalize(device, registry)
                if not av or not bv or neighbor == device:
                    missing.append(device)
                    continue
                (device, a), (neighbor, b) = sorted(((device, a), (neighbor, b)), key=lambda x: (x[0].lower(), x[1].lower()))
                result.append({'link_id': _make_link_id(device, a, neighbor, b), 'source_device': device, 'source_interface': a, 'destination_device': neighbor, 'destination_interface': b, 'discovery_protocol': 'CDP' if 'cdp' in r['command'] else 'LLDP', 'link_status': 'up'})
            else:
                missing.append(device)
    if kind == 'l2':
        unique = {}
        for r in sorted(result, key=dumps):
            unique.setdefault(r['link_id'], r)
        result = list(unique.values())
    result.sort(key=dumps)
    return result, {'status': 'missing_fields' if missing else 'observed', 'count': None if missing else len(result), 'missing_devices': sorted(set(missing)), 'basis': 'historical_parsed'}


def historical_bounded(q, sid, devices=None):
    """OW144 opt-in bounded historical facts (legacy historical untouched)."""
    q.source_audit(sid)
    q.parsed_audit(sid)
    members = sorted(set(q.parsed_membership(sid)) | set(q.source_membership(sid)))
    selected = [d for d in members if not devices or d in devices]
    facts = {d: {'platform': None, 'model': None, 'os_version': None, 'record_source_platform': None} for d in selected}
    for d, p in q.source_platforms(sid, selected).items():
        facts[d]['record_source_platform'] = p
    for device, command, idx, raw in q.protocol_entries(sid, ('show version',), devices=(devices or None)):
        if command != 'show version' or int(idx) != 0:
            continue
        entry = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(entry, dict):
            raise DomainError('SOURCE_UNAVAILABLE', 'parsed_shape')
        entry = {str(k).lower(): v for k, v in entry.items()}
        if device in facts:
            facts[device].update(platform=pick(entry, 'platform'), model=pick(entry, 'model', 'hardware'),
                                 os_version=pick(entry, 'os_version', 'junos_version', 'version'))
    return members, facts


def protocol_bounded(q, sid, kind, devices=None):
    """OW144 opt-in bounded protocol read (legacy protocol untouched, except new OSPF branch)."""
    from olav_skills.netops.core.topology_engine import _canonicalise_interface, _make_link_id
    q.source_audit(sid)
    q.parsed_audit(sid)
    commands = {'interface': ('show ip interface brief', 'show interfaces terse', 'show interfaces'),
                'bgp': ('show ip bgp summary', 'show bgp summary'),
                'l2': ('show lldp neighbors', 'show lldp neighbors detail', 'show cdp neighbors', 'show cdp neighbors detail'),
                'ospf': ('show ip ospf neighbor', 'show ospf neighbor')}[kind]
    header = q.protocol_header(sid, commands)
    if not header['present']:
        return [], {'status': 'missing', 'count': None, 'reason': 'command_not_parsed'}
    observed_devices = set(header['observed_devices'])
    if devices is not None:
        devices = set(devices)
    rows = q.protocol_entries(sid, commands, devices=sorted(devices) if devices else None)
    result, missing = [], sorted(devices - observed_devices) if devices is not None else []
    registry = None
    if kind == 'l2':
        from olav_skills.netops.core.hostname_registry import canonicalize, refresh
        members = set(q.parsed_membership(sid)) | set(q.source_membership(sid))
        class Registry:
            def execute(self, sql):
                self.values = [(d,) for d in sorted(members, key=lambda d: (-len(d), d))]
                return self
            def fetchall(self):
                return self.values
        registry = Registry()
        refresh(registry)
    for device, command, _idx, raw in rows:
        entry = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(entry, dict):
            raise DomainError('SOURCE_UNAVAILABLE', 'parsed_shape')
        entry = {str(k).lower(): v for k, v in entry.items()}
        if kind == 'interface':
            name = pick(entry, 'interface', 'interface_name')
            status, proto = pick(entry, 'status', 'link_state', 'admin_state'), pick(entry, 'proto', 'protocol')
            if name is None:
                missing.append(device)
                continue
            if status is None or proto is None:
                missing.append(device)
            intf, valid = _canonicalise_interface(str(name))
            if not valid:
                missing.append(device)
                continue
            result.append({'device_name': device, 'interface': intf, 'ip_address': pick(entry, 'ip_address', 'address'), 'status': status, 'proto': proto})
        elif kind == 'bgp':
            neighbor, state = pick(entry, 'bgp_neighbor', 'neighbor', 'neighbor_ip'), pick(entry, 'state_or_prefixes_received', 'state_pfxrcd', 'state')
            if neighbor is None or state is None:
                missing.append(device)
                continue
            state = str(state)
            remote_as = pick(entry, 'neighbor_as', 'remote_as', 'as')
            local_as = pick(entry, 'local_as')
            for _label, value in (('remote', remote_as), ('local', local_as)):
                if value is not None and not str(value).isdigit():
                    raise DomainError('SOURCE_UNAVAILABLE', 'bgp_as_type')
            if remote_as is None:
                missing.append(device)
            result.append({'device': device, 'device_name': device, 'neighbor_ip': neighbor, 'bgp_neighbor': neighbor,
                           'neighbor_as': int(remote_as) if remote_as is not None else None,
                           'local_as': int(local_as) if local_as is not None else None,
                           'router_id': pick(entry, 'router_id'), 'state': 'Established' if state.isdigit() else state,
                           'state_or_prefixes_received': state, 'uptime': pick(entry, 'up_down', 'uptime')})
        elif kind == 'l2':
            local = pick(entry, 'local_interface', 'local_port', 'local_intf', 'interface')
            neighbor = pick(entry, 'neighbor', 'neighbor_name', 'neighbor_device', 'destination_host', 'device_id', 'system_name')
            remote = pick(entry, 'neighbor_interface', 'remote_interface', 'neighbor_port_id', 'port_id', 'remote_port', 'port')
            if not local or not neighbor or not remote:
                missing.append(device)
                continue
            a, av = _canonicalise_interface(str(local)); b, bv = _canonicalise_interface(str(remote))
            neighbor = canonicalize(str(neighbor), registry)
            device = canonicalize(device, registry)
            if not av or not bv or neighbor == device:
                missing.append(device)
                continue
            (device, a), (neighbor, b) = sorted(((device, a), (neighbor, b)), key=lambda x: (x[0].lower(), x[1].lower()))
            result.append({'link_id': _make_link_id(device, a, neighbor, b), 'source_device': device, 'source_interface': a,
                           'destination_device': neighbor, 'destination_interface': b,
                           'discovery_protocol': 'CDP' if 'cdp' in command else 'LLDP', 'link_status': 'up'})
        elif kind == 'ospf':
            neighbor_id = pick(entry, 'neighbor_id', 'router_id')
            neighbor_ip = pick(entry, 'ip_address', 'neighbor_ip', 'address')
            interface = pick(entry, 'interface', 'interface_name')
            state_raw = pick(entry, 'state')
            dead_time = pick(entry, 'dead_time', 'dead_timer')
            area = pick(entry, 'area')
            if neighbor_id is None or interface is None or state_raw is None:
                missing.append(device)
                continue
            state = _ospf_state(state_raw)
            n_ip = _ospf_neighbor_ip(neighbor_ip)
            result.append({'device': device, 'device_name': device,
                           'neighbor_id': str(neighbor_id), 'neighbor_ip': n_ip,
                           'interface': str(interface),
                           'area': None if area is None else str(area),
                           'state': state, 'dead_time': None if dead_time is None else str(dead_time)})
        else:
            missing.append(device)
    if kind == 'l2':
        unique = {}
        for r in sorted(result, key=dumps):
            unique.setdefault(r['link_id'], r)
        result = list(unique.values())
    result.sort(key=dumps)
    return result, {'status': 'missing_fields' if missing else 'observed', 'count': None if missing else len(result), 'missing_devices': sorted(set(missing)), 'basis': 'historical_parsed'}


def inspect_interfaces(devices, include_unassigned=False, *, snapshot_id='latest'):
    def action(q):
        ds = strings(devices)
        if type(include_unassigned) is not bool:
            raise DomainError('INVALID_REQUEST')
        sid, temporal = q.snapshot(snapshot_id)
        rows, coverage = protocol_bounded(q, sid, 'interface', devices=set(ds) if ds else None)
        rows = [r for r in rows if (not ds or r['device_name'] in ds) and (include_unassigned or str(r['ip_address'] or '').lower() not in ('', 'unassigned', 'none'))]
        found = {}
        for r in rows[:50]:
            found.setdefault(r['device_name'], []).append({k: v for k, v in r.items() if k != 'device_name'})
        return {'found': found, 'snapshot_id': sid, 'snapshot_ids': {d: sid for d in found}, 'unknown_devices': sorted(set(ds) - set(found)), 'absent_sources': [] if coverage['status'] == 'observed' else [{'command': 'interface', 'reason': coverage['status']}], 'missing_layers': [] if coverage['status'] == 'observed' else ['interface'], 'coverage': coverage, 'temporal': temporal, 'total_rows': len(rows), 'sample_truncated': len(rows) > 50}
    return execute(action, required=tuple(TIME_RELATIONS))


def l2_topology_links(q, sid):
    from olav_skills.netops.core.hostname_registry import canonicalize
    from olav_skills.netops.core.topology_engine import _make_link_id
    q.require('topology_links', ('snapshot_id', 'source_device', 'source_interface', 'destination_device', 'destination_interface', 'discovery_protocol', 'link_status'))
    hostnames = []
    if ('netops', 'devices') in q.policy.relations:
        q.require('devices', ('hostname', 'platform', 'vendor', 'model'))
        hostnames = [r[0] for r in q.run('SELECT hostname FROM reader.netops.devices WHERE hostname IS NOT NULL', cap=10000)]
        q.sources.add('netops.devices')
    class Registry:
        def execute(self, sql):
            return self
        def fetchall(self):
            return [(h,) for h in hostnames]
    registry = Registry()
    rows = q.run('SELECT source_device,source_interface,destination_device,destination_interface,discovery_protocol,link_status FROM reader.netops.topology_links WHERE snapshot_id=?', [sid], cap=10000)
    q.sources.add('netops.topology_links')
    result, seen = [], set()
    for source, source_interface, destination, destination_interface, proto, status in rows:
        source = canonicalize(source or '', registry)
        destination = canonicalize(destination or '', registry)
        if not source or not destination or source == destination:
            continue
        (a_dev, a_intf), (b_dev, b_intf) = sorted(((source, source_interface), (destination, destination_interface)), key=lambda x: (x[0].lower(), x[1].lower()))
        link_id = _make_link_id(a_dev, a_intf, b_dev, b_intf)
        if link_id in seen:
            continue
        seen.add(link_id)
        result.append({'source_device': a_dev, 'source_interface': a_intf, 'destination_device': b_dev, 'destination_interface': b_intf, 'discovery_protocol': proto, 'link_status': status})
    result.sort(key=dumps)
    coverage = {'status': 'observed' if result else 'missing', 'count': len(result) if result else None, 'missing_devices': [], 'basis': 'topology_links'}
    return result, coverage


def query_topology(concept='all', snapshot_id=None):
    def action(q):
        if concept not in ('all', 'bgp', 'l2', 'ospf'):
            raise DomainError('INVALID_REQUEST')
        sid, temporal = q.snapshot(snapshot_id or 'latest')
        result = {'snapshot_id': sid, 'temporal': temporal, 'bgp_sessions': [], 'ospf_adjacencies': [], 'l2_links': [], 'coverage': {}, 'missing_layers': [], 'absent_layers': []}
        remaining = 50
        for kind, name in (('bgp', 'bgp_sessions'), ('ospf', 'ospf_adjacencies'), ('l2', 'l2_links')):
            if concept not in ('all', kind):
                continue
            if kind == 'l2' and ('netops', 'topology_links') in q.policy.relations:
                rows, coverage = l2_topology_links(q, sid)
            elif kind == 'l2' and concept == 'l2':
                rows, coverage = protocol(q, sid, kind)
            else:
                rows, coverage = protocol_bounded(q, sid, kind)
            public_fields = {'bgp': ('device', 'neighbor_ip', 'neighbor_as', 'local_as', 'router_id', 'state', 'uptime'), 'l2': ('source_device', 'source_interface', 'destination_device', 'destination_interface', 'discovery_protocol', 'link_status'), 'ospf': ('device', 'neighbor_id', 'neighbor_ip', 'interface', 'area', 'state', 'dead_time')}[kind]
            result[name] = [{k: row.get(k) for k in public_fields} for row in rows[:remaining]]
            remaining -= len(result[name])
            result['coverage'][kind] = {**coverage, 'returned': len(result[name]), 'sample_truncated': len(rows) > len(result[name])}
            if coverage['status'] != 'observed':
                result['missing_layers'].append(kind)
                result['absent_layers'].append(kind)
        return result
    return execute(action, required=tuple(TIME_RELATIONS))


def diff_table(q, table, source, target, device=None, sample_limit=50, cell_cap=2000):
    keys = KEYS[table]
    needed = ('snapshot_id', *keys, *(() if device is None else (('source_device',) if table == 'topology_links' else ('device_name',))))
    q.require(table, needed)
    fields = ','.join('"' + c + '"' for c in keys)
    filt = '' if device is None else ' AND ' + ('source_device' if table == 'topology_links' else 'device_name') + '=?'
    result = {'status': 'ok', 'coverage': 'last_writer_labels' if table in ('raw_output_store', 'topology_links') else 'snapshot_identity_keys', 'sample_truncated': {}}
    for direction, first, second in (('added', target, source), ('removed', source, target)):
        sql = f'SELECT {fields} FROM reader.netops.{table} WHERE snapshot_id=?{filt} EXCEPT SELECT {fields} FROM reader.netops.{table} WHERE snapshot_id=?{filt}'
        params = [first, *([] if device is None else [device]), second, *([] if device is None else [device])]
        n = q.run('SELECT count(*) FROM (' + sql + ') d', params, cap=1)[0][0]
        lengths = ','.join(f'COALESCE(max(system.main.length(CAST("{k}" AS VARCHAR))),0)' for k in keys)
        # Refuse oversized keys before fetching, not after clipping them.
        selected = '(' + sql + ')'
        oversize = max(q.run('SELECT ' + lengths + ' FROM ' + selected + ' d', params, cap=1)[0])
        if oversize > cell_cap:
            raise DomainError('RESULT_ROW_TOO_LARGE', 'complete_identity_key')
        order = ','.join('"' + c + '" ASC NULLS LAST' for c in keys)
        rows = q.run('SELECT * FROM ' + selected + ' d ORDER BY ' + order + ' LIMIT ?', params + [sample_limit], cap=sample_limit)
        result[direction + '_count'] = int(n)
        result[direction + '_sample'] = [dict(zip(keys, row)) for row in rows]
        result[direction] = result[direction + '_sample']
        result[direction + '_has_more'] = n > len(rows)
        result[direction + '_truncated'] = n > len(rows)
        result['sample_truncated'][direction] = n > len(rows)
    q.sources.add('netops.' + table)
    return result


def diff_snapshots(snapshot_id_1, snapshot_id_2, table_name=None, device=None, *, sample_limit=50, timeout_ms=5000):
    try:
        text(snapshot_id_1); text(snapshot_id_2); text(device, optional=True)
        if table_name is not None and (not isinstance(table_name, str) or table_name not in KEYS):
            raise DomainError('INVALID_REQUEST')
        integer(sample_limit, 1, 50)
    except DomainError as exc:
        return failure(exc.code, exc.reason)
    def action(q):
        sid1, t1 = q.snapshot(snapshot_id_1, deep=False)
        sid2, t2 = q.snapshot(snapshot_id_2, deep=False)
        tables = {}
        for t in ([table_name] if table_name else KEYS):
            if ('netops', t) not in q.policy.relations:
                if table_name is not None:
                    raise DomainError('OPERATION_DENIED')
                continue
            if not q._present(t):
                if table_name is not None:
                    q.require(t)
                tables[t] = {'status': 'absent', 'added_count': 0, 'removed_count': 0, 'added': [], 'removed': []}
                continue
            tables[t] = diff_table(q, t, sid1, sid2, device, sample_limit)
        return {'snapshot_id_1': sid1, 'snapshot_id_2': sid2, 'device': device, 'tables': tables, 'total_added': sum(t['added_count'] for t in tables.values()), 'total_removed': sum(t['removed_count'] for t in tables.values()), 'temporal': {'source': t1, 'target': t2}, 'semantics': 'identity_keys; added=target-source; removed=source-target'}
    return execute(action, required=tuple(TIME_RELATIONS), timeout=5000 if timeout_ms is None else timeout_ms)


def ordered(rows, columns, first, direction='asc'):
    order = [first] + [c for c in columns if c != first]
    def compare(a, b):
        for i, key in enumerate(order):
            x, y = a.get(key), b.get(key)
            if x is None or y is None:
                cmp = (x is None) - (y is None)
            else:
                if isinstance(x, str): x = x.encode()
                if isinstance(y, str): y = y.encode()
                cmp = (x > y) - (x < y)
                if i == 0 and direction == 'desc': cmp = -cmp
            if cmp: return cmp
        return 0
    return sorted(rows, key=cmp_to_key(compare))


def _summary_counts(q, sid, devices, commands):
    if not devices:
        return {'observed_device_count': 0, 'command_count': 0, 'record_count': 0}
    clauses, params = ['snapshot_id=?'], [sid]
    clauses.append('device_name IN (' + ','.join('?' for _ in devices) + ')')
    params.extend(devices)
    if commands:
        clauses.append('command IN (' + ','.join('?' for _ in commands) + ')')
        params.extend(commands)
    row = q.run('SELECT count(DISTINCT device_name),count(DISTINCT command),count(*) FROM reader.netops.parsed_outputs WHERE ' + ' AND '.join(clauses), params, cap=1)[0]
    return {'observed_device_count': int(row[0]), 'command_count': int(row[1]), 'record_count': int(row[2])}


def batch(arguments, *, temporal_sources=None, host_max_rows=None, host_max_bytes=None, host_timeout_ms=None):
    from .batch import _validate, FIELDS, DIFF_KINDS, OPERATIONS
    try:
        a = _validate(arguments)
        if temporal_sources is not None and (not isinstance(temporal_sources, tuple) or len(set(temporal_sources)) != len(temporal_sources) or not set(temporal_sources) <= {'netops.parsed_outputs', 'netops.bundle_ingests', 'netops.ingest_sources'}):
            raise DomainError('INVALID_REQUEST')
        for v in (host_max_rows, host_max_bytes, host_timeout_ms):
            if v is not None and (type(v) is not int or v < 1): raise DomainError('INVALID_REQUEST')
        maxrows, maxbytes, timeout = min(50, host_max_rows or 50), min(12000, host_max_bytes or 12000), min(5000, host_timeout_ms or 5000)
        limit = min(a['limit'], maxrows)
    except (ValueError, TypeError, LookupError):
        return failure('INVALID_REQUEST')
    def action(q):
        op = a['operation']
        requested = arguments.get('limit')
        result = {'operation': op, 'limit_metadata': {'requested': requested, 'default': 10, 'effective': limit, 'host': maxrows, 'cell_chars': 256, 'response_bytes': maxbytes}}
        if op == 'context':
            caps = []
            for table, columns in TABLES.items():
                if table == 'ingest_sources' or ('netops', table) not in q.policy.relations: continue
                capability_columns = columns + (('commands_count', 'hosts_count') if table == 'bundle_ingests' else ())
                caps.append({'source': 'netops.' + table, 'status': 'present' if ('reader', 'netops', table) in q.tables and ('reader', 'netops', table) not in q.views else 'absent', 'columns': [c for c in capability_columns if c in q.columns.get(table, {})], 'required_columns_present': set(columns) <= set(q.columns.get(table, {}))})
            return {**result, 'sources': caps, 'operations': list(OPERATIONS), 'scope': 'authorized_base_table_capabilities_only'}
        if op == 'diff_summary':
            table = DIFF_KINDS[a.get('diff_kind', 'parsed_keys')]
            q.require(table)
            sid1, t1 = q.snapshot(a['snapshot_id_1'], temporal_sources)
            sid2, t2 = q.snapshot(a['snapshot_id_2'], temporal_sources)
            n = min(limit, 25)
            return {**result, 'snapshot_id_1': sid1, 'snapshot_id_2': sid2, 'diff_kind': a.get('diff_kind', 'parsed_keys'), 'diff': diff_table(q, table, sid1, sid2, a['devices'][0] if a['devices'] else None, n, 256), 'temporal': {'source': t1, 'target': t2}, 'limit_metadata': {**result['limit_metadata'], 'effective': n, 'fixed_direction_cap': 25}}
        inventory = q.inventory()
        d = [r for r in inventory if (not a['devices'] or r['hostname'] in a['devices']) and (not a['vendors'] or r['vendor'] in a['vendors'])]
        names = {r['hostname'] for r in d}
        if op == 'devices' and 'snapshot_id' not in a:
            sid, temporal, p = None, {'collected_at': dict(UNKNOWN), 'scope': 'current_inventory'}, []
        elif op == 'snapshot_summary':
            sid, temporal = q.snapshot(a.get('snapshot_id', 'latest'), temporal_sources)
            p = []
        else:
            sid, temporal = q.snapshot(a.get('snapshot_id', 'latest'), temporal_sources)
            p = [r for r in q.parsed(sid) if r['device_name'] in names and (not a['commands'] or r['command'] in a['commands'])]
        result.update(snapshot_id=sid, temporal=temporal, scope={'devices': a['devices'], 'vendors': a['vendors'], 'commands': a['commands'], 'inventory_basis': 'current_inventory', 'membership_basis': 'selected_snapshot'})
        if op == 'snapshot_summary':
            return {**result, 'counts': {'inventory_device_count': len(names), **_summary_counts(q, sid, sorted(names), a['commands'])}}
        if op in ('interface_health', 'bgp_health'):
            kind = 'interface' if op == 'interface_health' else 'bgp'
            h, cov = protocol(q, sid, kind, devices=names)
            h = [r for r in h if r.get('device_name', r.get('device')) in names]
            def bad(r):
                return str(r['status']).lower() != 'up' or str(r['proto']).lower() != 'up' if kind == 'interface' else r['state'].lower() != 'established'
            fields = ['device_name', 'interface', 'ip_address', 'status', 'proto'] if kind == 'interface' else ['device_name', 'bgp_neighbor', 'state_or_prefixes_received']
            unhealthy = [r for r in h if bad(r)]
            hs = ordered(unhealthy, fields, fields[0])[:limit]
            return {**result, 'rows': [{k: r.get(k) for k in fields} for r in hs], 'total_rows': len(unhealthy) if cov['status'] == 'observed' else None, 'has_more': len(unhealthy) > len(hs), 'counts': {'record_count': len(h) if cov['status'] == 'observed' else None, 'observed_device_count': len({r['device_name'] for r in h}) if cov['status'] == 'observed' else None, 'unhealthy_count': len(unhealthy) if cov['status'] == 'observed' else None, 'healthy_count': len(h)-len(unhealthy) if cov['status'] == 'observed' else None}, 'coverage': cov, 'missing_layers': [] if cov['status'] == 'observed' else [kind]}
        if op == 'devices':
            rows = d
            if sid:
                members, facts = historical(q, sid)
                rows = [r for r in d if r['hostname'] in members]
                result.update(current_inventory={'basis': 'current_inventory', 'filtered_device_count': len(d)}, snapshot_membership={'basis': 'parsed_and_persisted_source', 'observed_device_count': len(members), 'snapshot_id': sid}, historical_facts={k: facts[k] for k in sorted(facts) if k in names})
        elif op in ('multi_device_summary', 'device_coverage'):
            summary = [{'device_name': r['hostname'], 'vendor': r['vendor'], 'record_count': sum(x['device_name'] == r['hostname'] for x in p), 'command_count': len({x['command'] for x in p if x['device_name'] == r['hostname'] and x['command'] is not None}), 'collection_status': 'present' if any(x['device_name'] == r['hostname'] for x in p) else 'no_matching_records'} for r in d]
            rows = summary if op == 'multi_device_summary' else [{'vendor': v, 'device_count': sum(r['vendor'] == v for r in summary), 'observed_device_count': sum(r['vendor'] == v and r['record_count'] > 0 for r in summary)} for v in {r['vendor'] for r in summary}]
        elif op in ('commands', 'command_coverage'):
            groups = {(r['device_name'], r['command']) if op == 'commands' else (r['command'],) for r in p}
            rows = []
            for group in groups:
                subset = [r for r in p if ((r['device_name'], r['command']) if op == 'commands' else (r['command'],)) == group]
                rows.append({'device_name': group[0], 'command': group[1], 'record_count': len(subset)} if op == 'commands' else {'command': group[0], 'device_count': len({r['device_name'] for r in subset}), 'record_count': len(subset)})
        else:
            vendor = {r['hostname']: r['vendor'] for r in d}
            def key(r): return r['device_name'] if a['group_by'] == 'device' else r['command'] if a['group_by'] == 'command' else vendor[r['device_name']]
            rows = []
            for group in {key(r) for r in p}:
                subset = [r for r in p if key(r) == group]
                rows.append({'key': group, 'device_count': len({r['device_name'] for r in subset}), 'command_count': len({r['command'] for r in subset if r['command'] is not None}), 'record_count': len(subset)})
        total = len(rows)
        rows = ordered(rows, FIELDS[op], a['order_by'], a['order'])[:limit]
        projected = [{f: r.get(f)[:256] if isinstance(r.get(f), str) else r.get(f) for f in a['fields']} for r in rows]
        missing = [{'device_name': x, 'status': 'outside_inventory_filter' if x not in names else 'no_matching_records'} for x in sorted(a['devices']) if not any(r['device_name'] == x for r in p)] if sid else []
        return {**result, 'rows': projected, 'total_rows': total, 'has_more': total > len(rows), 'cell_values_may_be_clipped': True, 'coverage': {'requested_devices_without_matches': missing, 'inventory_is_snapshot_membership': False}}
    return execute(action, timeout=timeout, byte_cap=maxbytes)


def raw_identity(scope, sid, device, command):
    return ('current_' if scope == 'current_labels' else 'source_') + hashlib.sha256(dumps([sid, device, command]).encode()).hexdigest()


def _canonical_identity_sql(scope):
    """Return (SQL expr, params) equal to raw_identity(scope, snapshot_id, device_name, command).

    Builds the same JSON array string Python ``dumps`` produces
    (ensure_ascii=False, separators=(',',':')) with fixed, parameter-bound
    ``system.main.replace`` escapes: backslash, quote, then all 32 C0 controls
    (08/09/0a/0c/0d named, the rest lowercase ``\\u00xx``).  No new builtin
    name is introduced and no control byte is embedded in SQL text.
    """
    columns = ('snapshot_id', 'device_name', 'command')
    elements, params = [], []
    for col in columns:
        expr = col
        named = {8: r'\b', 9: r'\t', 10: r'\n', 12: r'\f', 13: r'\r'}
        pairs = [('\\', '\\\\'), ('"', '\\"')] + [(chr(i), named.get(i, '\\u%04x' % i)) for i in range(32)]
        for a, b in pairs:
            expr = f'system.main.replace({expr}, ?, ?)'
            params += [a, b]
        elements.append(f"CASE WHEN {col} IS NULL THEN 'null' ELSE '\"' || {expr} || '\"' END")
    json_expr = "'[' || " + " || ',' || ".join(elements) + " || ']'"
    prefix = 'current_' if scope == 'current_labels' else 'source_'
    return f"('{prefix}' || system.main.sha256({json_expr}))", params


#: All legal Unicode scalars (no lone surrogates; DuckDB UTF-8 never holds them).
_UNICODE_CORPUS = ''.join(chr(i) for i in range(0x110000) if not 0xD800 <= i <= 0xDFFF)


@lru_cache(maxsize=1024)
def _literal_class(char):
    """RE2 character class equal to Python re.search(re.escape(char), x, re.I).

    A lone surrogate has no member in the legal-scalar corpus, so Python
    ``like`` never matches it against legal DB strings; the SQL equivalent is a
    never-matching class (an empty ``[]`` would be an invalid RE2 regex).
    """
    equivalent = re.findall(re.escape(char), _UNICODE_CORPUS, re.I)
    if not equivalent:
        return '[^\\s\\S]'
    return '[' + ''.join(re.escape(c) for c in equivalent) + ']'


def _like_sql_regex(pattern):
    """RE2 regex equal to Python re.search(like_pattern(pattern), x, re.I | re.S).

    Literal characters expand to their interpreter-derived case classes, so the
    SQL engine's own case folding (which diverges from Python re.I for e.g.
    İ/ı) is never used: only the 's' flag is enabled.
    """
    return ''.join('.*' if c == '%' else '.' if c == '_' else _literal_class(c) for c in pattern)


def like_pattern(pattern):
    """The exact regex string ``like()`` searches with (Python re, not SQL)."""
    return ''.join('.*' if c == '%' else '.' if c == '_' else re.escape(c) for c in pattern)


def like(value, pattern):
    return re.search(like_pattern(pattern), value or '', re.I | re.S) is not None


def query_evidence(source, pattern, device=None, time_range=None, snapshot=None, *, commands=None, ids=None, date_from=None, date_to=None, page=1, offset=None, limit=None, deadline_ms=5000, raw_scope='current_labels'):
    try:
        if source not in ('command_output', 'config', 'syslog'):
            raise DomainError('INVALID_REQUEST')
        if source == 'syslog':
            raise DomainError('SOURCE_UNAVAILABLE', 'syslog_not_supported')
        if raw_scope not in ('current_labels', 'immutable_source') or time_range is not None:
            raise DomainError('INVALID_REQUEST')
        text(pattern)
        if not pattern.strip(): raise DomainError('INVALID_REQUEST')
        text(device, optional=True)
        cmds, identifiers = strings([] if commands is None else commands), strings([] if ids is None else ids)
        expected_prefix = 'current_' if raw_scope == 'current_labels' else 'source_'
        if any(not v.startswith(expected_prefix) for v in identifiers): raise DomainError('INVALID_REQUEST')
        page_value = integer(page, 1, 1_000_000)
        n = integer(50 if limit is None else limit, 1, 50)
        start = integer(offset, 0, 50_000_000) if offset is not None else (page_value - 1) * n
        dates = []
        for value in (date_from, date_to):
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise DomainError('INVALID_REQUEST')
                try: date.fromisoformat(value)
                except ValueError: raise DomainError('INVALID_REQUEST') from None
            dates.append(value)
        if all(dates) and dates[0] > dates[1]: raise DomainError('INVALID_REQUEST')
        if deadline_ms is not None and (type(deadline_ms) is not int or deadline_ms <= 0):
            raise DomainError('INVALID_REQUEST')
    except DomainError as exc:
        return failure(exc.code, exc.reason)
    def action(q):
        temporal = None
        if snapshot is not None:
            snapshot_value, temporal = q.snapshot(text(snapshot))
        else:
            snapshot_value = None
        table = 'raw_output_store' if raw_scope == 'current_labels' else 'ingest_sources'
        q.require(table, internal=table == 'ingest_sources')
        relation = 'reader.netops.raw_output_store' if raw_scope == 'current_labels' else '(SELECT DISTINCT snapshot_id,device_name,command,raw_output FROM reader.netops.ingest_sources)'
        # Push all four filters (snapshot, device, commands, ids) into SQL so
        # the budget gate measures the narrowed candidate set. device uses
        # _like_sql_regex() (Python-re.I-derived character classes, RE2 's'
        # only) so Unicode case folding stays exact; ids use the canonical
        # identity serializer, parameter-bound, matching raw_identity.
        clauses, params = [], []
        if snapshot_value:
            clauses.append('snapshot_id=?')
            params.append(snapshot_value)
        if device:
            clauses.append("system.main.regexp_matches(COALESCE(device_name,''), ?, 's')")
            params.append(_like_sql_regex(device))
        if cmds:
            clauses.append('command IN (' + ','.join('?' for _ in cmds) + ')')
            params.extend(cmds)
        if identifiers:
            ident_expr, ident_params = _canonical_identity_sql(raw_scope)
            clauses.append(ident_expr + ' IN (' + ','.join('?' for _ in identifiers) + ')')
            params.extend(ident_params)
            params.extend(identifiers)
        pred = ' AND '.join(clauses) if clauses else 'TRUE'
        # Inspect bounded scalar metadata in SQL before any complete key, hash
        # or body-match projection is fetched into Python. Body stays in SQL.
        cells = [f"system.main.octet_length(system.main.encode(COALESCE(CAST(\"{c}\" AS VARCHAR),'')))" for c in ('snapshot_id', 'device_name', 'command')]
        metadata_bytes = '+'.join(cells) + "+64+system.main.octet_length(system.main.encode(COALESCE(CAST(system.main.length(raw_output) AS VARCHAR),'')))"
        maxima = ','.join(f'COALESCE(max({cell}),0)' for cell in cells)
        pre = q.run(f'SELECT count(*),COALESCE(sum({metadata_bytes}),0),{maxima} FROM reader.netops.{table} WHERE {pred}', params, cap=1)[0]
        if pre[0] > 1000 or pre[1] > 1_000_000 or max(pre[2:]) > 65536:
            raise DomainError('RESULT_BUDGET_EXCEEDED', 'raw_metadata')
        rows = q.run(f'SELECT snapshot_id,device_name,command,system.main.sha256(raw_output),system.main.length(raw_output) FROM {relation} raw_source WHERE {pred} ORDER BY snapshot_id ASC NULLS LAST,device_name ASC NULLS LAST,command ASC NULLS LAST LIMIT 1001', params)
        # config filter stays in Python (exact substring semantics). Collect
        # pre-date candidate keys first, then fetch source records for only
        # those keys (never the whole snapshot's source_json).
        pre_date, exclusions, seen = [], {'unknown': 0, 'invalid': 0, 'conflict': 0, 'partial': 0, 'derived': 0}, set()
        for sid, dev, command, digest, chars in rows:
            key = (sid, dev, command)
            if key in seen: raise DomainError('SOURCE_UNAVAILABLE', 'duplicate_raw_key')
            seen.add(key)
            if source == 'config' and not any(x in str(command).lower() for x in ('running-config', 'startup-config')):
                continue
            ident = raw_identity(raw_scope, *key)
            pre_date.append((key, ident, digest, chars))
        by_key = q.source_records_for_keys([k for k, *_ in pre_date])
        before_date = len(pre_date)
        candidates = []
        for key, ident, digest, chars in pre_date:
            evidence = by_key.get(key)
            stamp = normalize_time(evidence['collected_at'] if evidence else None)
            if any(dates):
                if stamp['status'] != 'known':
                    exclusions[stamp['status'] if stamp['status'] in exclusions else 'unknown'] += 1
                    continue
                day = stamp['value'][:10]
                if dates[0] and day < dates[0] or dates[1] and day > dates[1]: continue
            candidates.append((key, ident, digest, chars, stamp, evidence))
        q.sources.add('netops.' + table)
        matches = []
        if candidates:
            clauses, values = [], []
            for key, *_ in candidates:
                clauses.append('(snapshot_id IS NOT DISTINCT FROM ? AND device_name IS NOT DISTINCT FROM ? AND command IS NOT DISTINCT FROM ?)')
                values.extend(key)
            where = '(' + ' OR '.join(clauses) + ") AND raw_output ILIKE ('%'||?||'%')"
            values.append(pattern)
            total = q.run(f'SELECT count(*) FROM {relation} raw_source WHERE {where}', values, cap=1)[0][0]
            # First literal match is exact only for patterns without LIKE wildcards.
            position = "system.main.strpos(system.main.lower(raw_output),system.main.lower(?))"
            inner = f'SELECT snapshot_id,device_name,command,raw_output,{position} AS pos FROM {relation} raw_source WHERE {where}'
            context = f'SELECT *,CASE WHEN pos>120 THEN pos-120 ELSE 1 END AS begin_char FROM ({inner}) located'
            lines = lambda expression: f"system.main.length({expression})-system.main.length(system.main.replace({expression},'\n',''))+1"
            prefix = 'system.main.substr(raw_output,1,pos-1)'
            start_prefix = 'system.main.substr(raw_output,1,begin_char-1)'
            end_prefix = 'system.main.substr(raw_output,1,begin_char+1199)'
            projection = f'snapshot_id,device_name,command,system.main.substr(raw_output,begin_char,1200),{lines(prefix)},{lines(start_prefix)},{lines(end_prefix)}'
            page_rows = q.run(f'SELECT {projection} FROM ({context}) excerpted ORDER BY snapshot_id ASC NULLS LAST,device_name ASC NULLS LAST,command ASC NULLS LAST LIMIT ? OFFSET ?', [pattern, *values, n, start], cap=n)
            meta = {key: (ident, digest, chars, stamp, evidence) for key, ident, digest, chars, stamp, evidence in candidates}
            for sid, dev, command, excerpt, match_line, line_start, line_end in page_rows:
                ident, digest, chars, stamp, evidence = meta[(sid, dev, command)]
                literal = '%' not in pattern and '_' not in pattern
                matches.append({'source_id': ident, 'content_hash': digest, 'device': dev, 'device_name': dev, 'command': command, 'snapshot_id': sid, 'excerpt': excerpt, 'body_chars': chars, 'excerpt_truncated': len(excerpt) < chars, 'match_line': match_line if literal else None, 'excerpt_line_start': line_start if literal else None, 'excerpt_line_end': line_end if literal else None, 'line_position_status': 'known' if literal else 'unknown_wildcard_pattern', 'body_scope': 'bounded_excerpt', 'temporal': {'collected_at': stamp, 'parsed_at': evidence['parsed_at'] if evidence else dict(UNKNOWN), 'ingested_at': evidence['ingested_at'] if evidence else dict(UNKNOWN), 'record_updated_at': dict(UNKNOWN), 'event_time': dict(UNKNOWN)}, **({'input_artifact': evidence['input_artifact'], 'input_sha256': evidence['input_sha256']} if evidence and raw_scope == 'immutable_source' else {})})
        else:
            total = 0
        unknown = sum(exclusions.values())
        store_empty = not rows and q.run(f'SELECT count(*) FROM reader.netops.{table}', cap=1)[0][0] == 0
        zero_reason = None if total else 'store_empty' if store_empty else 'no_candidates' if before_date == 0 else 'no_dated_candidates' if any(dates) and not candidates else 'no_body_match'
        return {'source': source, 'matches': matches, 'total': total, 'candidate_total': len(candidates), 'returned': len(matches), 'offset': start, 'page': start // n + 1, 'page_size': n, 'has_more': start + len(matches) < total, 'next_offset': start + len(matches) if start + len(matches) < total else None, 'truncated': False, 'total_scope': 'filtered_scope', 'total_is_complete': True, 'identity_scope': 'record_key', 'body_scope': 'bounded_excerpt', 'coverage': 'last_writer_labels' if raw_scope == 'current_labels' else 'immutable_source', 'raw_scope': raw_scope, 'temporal': temporal, 'date_unknown_candidates': unknown if any(dates) else None, 'date_exclusions': exclusions if any(dates) else None, 'date_scope': {'status': ('partial_unknown' if candidates else 'unknown_only') if unknown else ('known_outside_range' if any(dates) and before_date and not candidates else 'known_only' if any(dates) else 'not_applicable'), 'excluded_candidates_total': None, 'known_dated_candidates': len(candidates) if any(dates) else None}, 'zero_reason': zero_reason, **({'hint': 'offset is past the filtered match total; this is not an empty dataset'} if total and start >= total else {}), 'budget': {'rows_omitted_by_char_budget': 0, 'page_size': n}, 'excerpt_truncated_rows': sum(r['excerpt_truncated'] for r in matches)}
    return execute(action, required=('raw_output_store',), timeout=5000 if deadline_ms is None else deadline_ms, char_cap=12000)


def candidate_main(function, *, allowed=None, byte_cap=65536, output_bytes=65536):
    import sys
    try:
        args = stdin_args(sys.stdin, byte_cap=byte_cap, allowed=allowed)
        result = function(**args)
    except DomainError as exc:
        result = failure(exc.code, exc.reason)
    except (ValueError, TypeError):
        result = failure('INVALID_REQUEST')
    except Exception as exc:
        result = failure(getattr(exc, 'code', 'WAREHOUSE_UNAVAILABLE'))
    final = finalize(result, byte_cap=output_bytes)
    print(dumps(final))
    raise SystemExit(1 if final.get('status') == 'error' else 0)
