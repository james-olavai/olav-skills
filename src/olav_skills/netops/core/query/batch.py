"""Fixed, bounded netops query templates; model input never supplies SQL or paths.

Native templates count in DuckDB over full predicates. The configured candidate
dispatches to the bounded domain service: complete prechecked sets are aggregated
under explicit source/expansion caps; exceeding a cap fails the whole request.
OLAV_HOME is a host configuration, not a tool argument or a tenant ACL. A caller
must bind that environment to its authorized project before invoking this module.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
from .result_contract import adapt_batch, adapt_diff, diff_limits
from .temporal import unknown, time_value, snapshot_time, record_temporal, interval
from olav_skills.netops.core.diff.snapshot_summary import (
    TABLES, SnapshotDiffError, _classify_db_error, _resolve_connect_config,
    _run_with_timeout, diff_one_table,
)

OPERATIONS = ('context', 'snapshot_summary', 'device_coverage', 'command_coverage',
              'devices', 'commands', 'multi_device_summary', 'top_n',
              'diff_summary', 'interface_health', 'bgp_health')
MAX_ROWS = 50
MAX_BYTES = 12_000
MAX_CELL = 256
TIMEOUT_MS = 15_000
# Fixed export surface. Other catalog entries are never exposed in context.
SOURCES = {
    'devices': ('hostname', 'ip_address', 'platform', 'vendor', 'model', 'role'),
    'parsed_outputs': ('device_name', 'command', 'snapshot_id'),
    'raw_output_store': ('device_name', 'command', 'snapshot_id'),
    'v_show_ip_interface_brief_auto': ('device_name', 'snapshot_id', 'interface', 'ip_address', 'status', 'proto'),
    'v_show_interfaces_terse_auto': ('device_name', 'snapshot_id', 'interface', 'ip_address', 'link_state', 'proto'),
    'v_show_ip_bgp_summary_auto': ('device_name', 'snapshot_id', 'bgp_neighbor', 'state_or_prefixes_received'),
    'v_show_bgp_summary_auto': ('device_name', 'snapshot_id', 'bgp_neighbor', 'state_or_prefixes_received'),
}
FIELDS = {
    'devices': SOURCES['devices'],
    'commands': ('device_name', 'command', 'record_count'),
    'multi_device_summary': ('device_name', 'vendor', 'record_count', 'command_count', 'collection_status'),
    'device_coverage': ('vendor', 'device_count', 'observed_device_count'),
    'command_coverage': ('command', 'device_count', 'record_count'),
    'top_n': ('key', 'device_count', 'command_count', 'record_count'),
}
DIFF_KINDS = {'parsed_keys': 'parsed_outputs', 'command_keys': 'raw_output_store',
              'topology_keys': 'topology_links', 'oc_keys': 'oc_outputs'}


def error(kind: str, message: str) -> dict:
    return {'status': 'error', 'error_type': kind, 'error': message[:800]}


def serialize(result: dict, max_bytes: int = MAX_BYTES) -> str:
    wire = json.dumps(result, ensure_ascii=False, separators=(',', ':'), default=str)
    if len(wire.encode()) > max_bytes:
        wire = json.dumps(error('budget_exceeded', f'response metadata exceeded {max_bytes} bytes; narrow the request'), separators=(',', ':'))
    return wire


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f'{label} must be a nonempty string of at most 256 characters')
    return value


def _list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError(f'{label} must be a list of at most 50 strings; [] means discovery')
    return list(dict.fromkeys(_text(x, label) for x in value))


def _validate(args: dict) -> dict:
    if not isinstance(args, dict):
        raise ValueError('stdin must be a JSON object')
    known = {'operation', 'snapshot_id', 'snapshot_id_1', 'snapshot_id_2', 'devices', 'vendors',
             'commands', 'fields', 'order_by', 'order', 'limit', 'group_by', 'metric', 'diff_kind'}
    if set(args) - known:
        raise ValueError('unknown arguments: ' + ', '.join(sorted(set(args) - known))[:300])
    a = dict(args)
    op = a.get('operation')
    if op not in OPERATIONS:
        raise LookupError('unsupported operation; choose ' + ', '.join(OPERATIONS))
    if op == 'context' and set(args) != {'operation'}:
        raise ValueError('context accepts only operation')
    if op not in FIELDS and 'order' in args:
        raise ValueError('order unsupported for this operation')
    for key in ('devices', 'vendors', 'commands'):
        a[key] = _list(a.get(key, []), key)
    for key in ('snapshot_id', 'snapshot_id_1', 'snapshot_id_2'):
        if key in a:
            _text(a[key], key)
    n = a.get('limit', 10)
    if type(n) is not int or not 1 <= n <= MAX_ROWS:
        raise ValueError('limit must be an integer from 1 to 50')
    a['limit'] = n
    a['order'] = a.get('order', 'asc')
    if a['order'] not in ('asc', 'desc'):
        raise ValueError('order must be asc or desc')
    if op in FIELDS:
        fields = _list(a.get('fields', []), 'fields') or list(FIELDS[op])
        if not set(fields) <= set(FIELDS[op]):
            raise ValueError('fields not in operation whitelist')
        a['fields'] = fields
        order_by = a.get('order_by', 'record_count' if op == 'top_n' else FIELDS[op][0])
        if order_by not in FIELDS[op]:
            raise ValueError('order_by not in operation whitelist')
        a['order_by'] = order_by
    elif any(k in a for k in ('fields', 'order_by')):
        raise ValueError('fields/order_by unsupported for this operation')
    if op == 'top_n':
        a['group_by'] = a.get('group_by', 'device')
        a['metric'] = a.get('metric', 'record_count')
        if a['group_by'] not in ('device', 'command', 'vendor') or a['metric'] not in ('device_count', 'command_count', 'record_count'):
            raise ValueError('group_by/metric not in whitelist')
        if 'order_by' in args and args['order_by'] != a['metric']:
            raise ValueError('top_n order_by must match metric')
        a['order_by'] = a['metric']
        a['order'] = a.get('order', 'desc') if 'order' in args else 'desc'
    elif any(k in a for k in ('group_by', 'metric')):
        raise ValueError('group_by/metric only supported by top_n')
    if op == 'diff_summary':
        if not a.get('snapshot_id_1') or not a.get('snapshot_id_2'):
            raise ValueError('diff_summary requires snapshot_id_1 and snapshot_id_2')
        if len(a['devices']) > 1 or a['vendors'] or a['commands']:
            raise ValueError('diff_summary supports at most one device; vendor/command filters unsupported')
        if a.get('diff_kind', 'parsed_keys') not in DIFF_KINDS:
            raise ValueError('diff_kind not in whitelist')
        if 'snapshot_id' in a:
            raise ValueError('use snapshot_id_1/2 for diff_summary')
    elif any(k in a for k in ('snapshot_id_1', 'snapshot_id_2', 'diff_kind')):
        raise ValueError('snapshot_id_1/2 and diff_kind only supported by diff_summary')
    if op in ('context', 'devices', 'device_coverage', 'interface_health', 'bgp_health') and a['commands']:
        raise ValueError('commands filter unsupported for this operation')
    if op == 'context' and any(k in args for k in ('snapshot_id', 'devices', 'vendors', 'commands', 'order')):
        raise ValueError('context accepts no answer-data filters; it returns schema capabilities only')
    return a


class Query:
    def __init__(self, conn: Any, temporal_sources=None):
        self.conn = conn
        self.deadline = time.monotonic() + TIMEOUT_MS / 1000
        self.fetched_rows = 0
        self.temporal_sources = temporal_sources
        # Physical sources this request actually read; provenanced only on success.
        self.read_sources = set()
        self._times = {}
        self._loaded_all_times = False

    def run(self, sql: str, params: list | None = None, cap: int = 1) -> list:
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise SnapshotDiffError('timeout', 'query request exceeded 15000 ms')
        def action():
            rows = self.conn.execute(sql, params or []).fetchmany(cap)
            self.fetched_rows += len(rows)
            return rows
        return _run_with_timeout(self.conn, action, timeout_ms=remaining, stage='query_batch')

    def columns(self, source: str) -> list[str]:
        allowed = SOURCES.get(source, ())
        if not allowed:
            return []
        marks = ','.join('?' for _ in allowed)
        return [x[0] for x in self.run(f'SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? AND column_name IN ({marks}) ORDER BY ordinal_position LIMIT 16', ['netops', source, *allowed], 16)]

    def require(self, source: str, needed: tuple | list):
        cols = self.columns(source)
        if not cols:
            raise SnapshotDiffError('source_missing', f'netops.{source} is absent')
        if not set(needed) <= set(cols):
            raise SnapshotDiffError('source_unsupported', f'netops.{source} lacks required columns: {sorted(set(needed)-set(cols))}')

    def _load_times(self, requested=None):
        if self._loaded_all_times or requested in self._times:
            return
        bundle_allowed = self.temporal_sources is None or 'netops.bundle_ingests' in self.temporal_sources
        table_names = ['parsed_outputs','raw_output_store'] + (['bundle_ingests'] if bundle_allowed else [])
        marks = ','.join('?' for _ in table_names)
        # Metadata remains inside the same connection/deadline. No device loop.
        catalog = self.run(f"SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='netops' AND table_name IN ({marks}) AND column_name IN ('snapshot_id','collected_at','captured_at','snapshot_date','ingested_at','updated_at','collector_name') LIMIT 40", table_names, cap=40)
        columns = {}
        for table, col in catalog:
            columns.setdefault(table, set()).add(col)
        sources = [t for t in ('parsed_outputs','raw_output_store') if 'snapshot_id' in columns.get(t, ())]
        if not sources:
            raise SnapshotDiffError('source_missing', 'no snapshot-bearing source is available')
        union = ' UNION '.join(f'SELECT snapshot_id FROM netops.{t}' for t in sources)
        predicate = ' AND snapshot_id=?' if requested else ''
        bind = [requested] if requested else []
        ids = self.run(f'SELECT snapshot_id FROM ({union}) s WHERE snapshot_id IS NOT NULL{predicate} LIMIT 513', bind, cap=513)
        if len(ids) > 512 or any(len(str(r[0])) > 256 for r in ids):
            raise SnapshotDiffError('source_budget', 'snapshot temporal metadata exceeds 512 bounded snapshots')
        self._times.update({r[0]: record_temporal({'snapshot_id': r[0]}, scope='snapshot') for r in ids})
        if requested is None:
            self._loaded_all_times = True
        # Bundle metadata is optional and is never read when a restricted caller
        # omits it from its trusted capabilities. stdin cannot set capabilities.
        tables = list(sources)
        if bundle_allowed:
            if 'snapshot_id' in columns.get('bundle_ingests', ()):
                tables.append('bundle_ingests')
        for table in tables:
            fields = [c for c in ('collected_at','captured_at','snapshot_date','ingested_at','updated_at') if c in columns[table]]
            if not fields:
                continue
            self.read_sources.add(table)
            parts = []
            for c in fields:
                text = f'CAST({c} AS VARCHAR)'
                clock = f"CASE WHEN regexp_full_match({text}, '[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}') THEN 'date' WHEN regexp_matches({text}, '(Z|[+-][0-9]{{2}}(:[0-9]{{2}})?)$') THEN 'aware' ELSE 'naive' END"
                parts.append(f'arg_min({text},TRY_CAST({c} AS TIMESTAMPTZ)),arg_max({text},TRY_CAST({c} AS TIMESTAMPTZ)),count({c}),count(*) FILTER (WHERE {c} IS NOT NULL AND TRY_CAST({c} AS TIMESTAMPTZ) IS NULL),count(DISTINCT {clock}) FILTER (WHERE {c} IS NOT NULL)')
            projection = ','.join(parts)
            # collector-dump stores date-only provenance as midnight timestamp.
            collector = ",bool_or(collector_name='collector-dump')" if 'collector_name' in columns[table] else ',false'
            rows = self.run(f'SELECT snapshot_id,count(*),{projection}{collector} FROM netops.{table} WHERE TRUE{predicate} GROUP BY snapshot_id LIMIT 513', bind, cap=513)
            if len(rows) > 512:
                raise SnapshotDiffError('source_budget', 'snapshot temporal metadata exceeds bounded source budget')
            for row in rows:
                sid, count = row[:2]
                if sid not in self._times:
                    continue
                target = self._times[sid]
                for i, field in enumerate(fields):
                    lo, hi, present, invalid, clocks = row[2+i*5:7+i*5]
                    key = 'record_updated_at' if field == 'updated_at' else ('collected_at' if field in ('captured_at','snapshot_date') else field)
                    source = table + '.' + field
                    if not present:
                        continue
                    stamp = time_value(lo, source, date_only=field == 'snapshot_date' or (key == 'collected_at' and row[-1]))
                    if lo != hi:
                        end = time_value(hi, source, date_only=field == 'snapshot_date' or (key == 'collected_at' and row[-1]))
                        if stamp['precision'] == end['precision'] and stamp['value'] and end['value']:
                            stamp.update(range_start=stamp['value'], range_end=end['value'])
                        else:
                            stamp = unknown(source, 'conflict')
                    if invalid or clocks > 1:
                        stamp = unknown(source, 'invalid' if invalid else 'conflict')
                    elif present != count:
                        stamp['status'] = 'partial'
                    old = target[key]
                    # bundle wins over row metadata; row metadata wins over ID
                    # labels. Conflicts within a priority level remain unknown.
                    priority = lambda x: 3 if str(x.get('source','')).startswith('bundle_ingests.') else (1 if str(x.get('source','')).startswith('snapshot_id') else 2)
                    if old['source'] is None or priority(stamp) > priority(old) or old['status'] == 'unknown':
                        target[key] = stamp
                    elif priority(stamp) == priority(old) and any(old.get(k) != stamp.get(k) for k in ('value','precision','status','range_start','range_end')):
                        target[key] = unknown(source, 'conflict')
                target['coverage']['status'] = target['collected_at']['status']

    def temporal(self, sid):
        self._load_times(sid)
        return self._times[sid]

    def snapshot(self, requested: str = 'latest') -> str:
        self._load_times(None if requested == 'latest' else requested)
        if requested != 'latest':
            if requested not in self._times:
                raise SnapshotDiffError('source_missing', 'requested snapshot does not exist in authorized snapshot sources')
            return requested
        if not self._times:
            raise SnapshotDiffError('source_missing', 'no bounded snapshot is available')
        if len(self._times) == 1:
            return next(iter(self._times))
        intervals = {sid: interval(t['collected_at']) for sid,t in self._times.items()}
        if all(intervals.values()):
            for sid, candidate in intervals.items():
                # A date interval must be strictly after every other interval.
                # Do not guess an ordering for mixed timezone provenance.
                others = [v for k,v in intervals.items() if k != sid]
                compatible = all(candidate[2] == v[2] for v in others)
                if compatible and all((candidate[0] >= v[1] if candidate[2] == 'date' else candidate[0] > v[1]) for v in others):
                    return sid
        raise SnapshotDiffError('ambiguous_snapshot_time', 'latest cannot be determined: unknown, conflicting, overlapping or incomparable collection times; select an explicit snapshot_id')


def _in(col: str, values: list[str], params: list) -> str:
    if not values:
        return ''
    params.extend(values)
    return f' AND {col} IN ({",".join("?" for _ in values)})'


def _scope(a: dict, sid: str) -> tuple[str, list]:
    params: list = []
    dw = _in('hostname', a['devices'], params) + _in('vendor', a['vendors'], params)
    params.append(sid)
    pw = _in('p.command', a['commands'], params)
    return (f'WITH d AS (SELECT * FROM netops.devices WHERE TRUE{dw}), '
            f'p AS (SELECT p.device_name,p.command FROM netops.parsed_outputs p '
            f'WHERE p.snapshot_id=? AND EXISTS (SELECT 1 FROM d WHERE d.hostname=p.device_name){pw}) ', params)


def _sample(q: Query, base: str, params: list, a: dict, fields: list[str], order: str) -> tuple[list, int, bool]:
    total = int(q.run('SELECT count(*) FROM (' + base + ') b', params)[0][0])
    # Clip in SQL before the bounded fetch, after aggregation/order. Exact counts
    # and ordering continue to use original full values. NULL remains NULL.
    projection = ','.join(f'CASE WHEN typeof("{f}")=\'VARCHAR\' AND length(CAST("{f}" AS VARCHAR))>{MAX_CELL} THEN substr(CAST("{f}" AS VARCHAR),1,{MAX_CELL}) ELSE CAST("{f}" AS VARCHAR) END AS "{f}"' if f not in ('record_count','command_count','device_count','observed_device_count') else f'"{f}"' for f in fields)
    rows = q.run(f'SELECT {projection} FROM (SELECT * FROM ({base}) b ORDER BY {order} LIMIT ?) sampled ORDER BY {','.join('sampled.' + x.strip() for x in order.split(','))}', params + [a['limit']], a['limit'])
    data = [dict(zip(fields, row)) for row in rows]
    return data, total, total > len(data)


def _context(q: Query) -> dict:
    entries = []
    for name, allowed in SOURCES.items():
        cols = q.columns(name)
        entries.append({'source': 'netops.' + name, 'status': 'present' if cols else 'absent',
                        'columns': [c for c in allowed if c in cols],
                        'required_columns_present': set(allowed) <= set(cols)})
    return {'status':'success', 'operation':'context', 'contract_version':1,
            'sources':entries, 'operations':list(OPERATIONS),
            'scope':'host-bound OLAV_HOME/.olav/databases/main.duckdb; source/column capability only; no answer rows or counts',
            'coverage_note':'Missing canonical view does not imply absence of underlying parsed data. Inventory is not snapshot membership.',
            'limits':{'rows':MAX_ROWS,'response_bytes':MAX_BYTES,'cell_chars':MAX_CELL,'timeout_ms':TIMEOUT_MS}}


def _health(q: Query, a: dict, sid: str, cte: str, params: list) -> dict:
    interface = a['operation'] == 'interface_health'
    names = ('v_show_ip_interface_brief_auto','v_show_interfaces_terse_auto') if interface else ('v_show_ip_bgp_summary_auto','v_show_bgp_summary_auto')
    parts, sources = [], []
    for name in names:
        cols = q.columns(name)
        required = SOURCES[name]
        is_view = bool(q.run("SELECT 1 FROM information_schema.tables WHERE table_schema='netops' AND table_name=? AND table_type='VIEW' LIMIT 1",[name]))
        supported = is_view and set(required) <= set(cols)
        sources.append({'source':'netops.'+name,'status': 'present' if supported else ('unsupported_columns_or_not_view' if cols else 'absent')})
        if not supported:
            continue
        if interface:
            state = 'status' if name == names[0] else 'link_state'
            part = f"SELECT s.device_name,s.interface,s.ip_address,s.{state} AS status,s.proto, (lower(coalesce(s.{state},''))<>'up' OR lower(coalesce(s.proto,''))<>'up') AS unhealthy FROM netops.{name} s WHERE s.snapshot_id=? AND EXISTS (SELECT 1 FROM d WHERE d.hostname=s.device_name)"
        else:
            part = f"SELECT s.device_name,s.bgp_neighbor,s.state_or_prefixes_received, NOT (lower(trim(coalesce(CAST(s.state_or_prefixes_received AS VARCHAR),'')))='established' OR regexp_full_match(trim(coalesce(CAST(s.state_or_prefixes_received AS VARCHAR),'')), '[0-9]+')) AS unhealthy FROM netops.{name} s WHERE s.snapshot_id=? AND EXISTS (SELECT 1 FROM d WHERE d.hostname=s.device_name)"
        parts.append(part)
    if not parts:
        return {**error('source_missing', 'no supported canonical protocol view; no health verdict available'), 'sources':sources}
    # Only d is needed; p remains a lazy CTE. All unions use identical d scope.
    hp = params + [sid] * len(parts)
    base = cte + ', h AS (' + ' UNION ALL '.join(parts) + ') '
    totals = q.run(base + 'SELECT count(*),count(DISTINCT device_name),count(*) FILTER (WHERE unhealthy) FROM h', hp)[0]
    fields = ['device_name','interface','ip_address','status','proto'] if interface else ['device_name','bgp_neighbor','state_or_prefixes_received']
    order = ','.join(f'"{x}" ASC NULLS LAST' for x in fields)
    rows, n, more = _sample(q, base + 'SELECT '+','.join(fields)+' FROM h WHERE unhealthy', hp, a, fields, order)
    covered = q.run(base + 'SELECT count(*) FROM d WHERE NOT EXISTS (SELECT 1 FROM h WHERE h.device_name=d.hostname)', hp)[0][0]
    missing = []
    if a['devices']:
        placeholders = ','.join('(?)' for _ in a['devices'])
        missing = [{'device_name':r[0], 'status':r[1]} for r in q.run(base+f", req(device_name) AS (VALUES {placeholders}) SELECT req.device_name,CASE WHEN NOT EXISTS (SELECT 1 FROM d WHERE d.hostname=req.device_name) THEN 'outside_inventory_filter' ELSE 'no_canonical_records' END FROM req WHERE NOT EXISTS (SELECT 1 FROM h WHERE h.device_name=req.device_name) ORDER BY req.device_name",hp+a['devices'],50)]
    return {'status':'success','sources':sources,'rows':rows,'total_rows':n,'has_more':more,
            'counts':{'record_count':totals[0],'observed_device_count':totals[1],'unhealthy_count':totals[2], 'healthy_count':totals[0]-totals[2]},
            'coverage':{'inventory_devices_without_canonical_records':covered,'complete':covered==0,'requested_devices_without_matches':missing,
                        'note':'Canonical views only. A missing view or zero canonical records does not prove underlying parsed/raw data is absent.'}}


def _execute(q: Query, a: dict, *, diff_limit_block: dict | None = None) -> dict:
    op = a['operation']
    if op == 'context':
        return _context(q)
    sid = q.snapshot(a.get('snapshot_id', 'latest')) if op not in ('diff_summary','devices') else None
    if op == 'diff_summary':
        sid1, sid2 = q.snapshot(a['snapshot_id_1']), q.snapshot(a['snapshot_id_2'])
        kind = DIFF_KINDS[a.get('diff_kind','parsed_keys')]
        meta = TABLES[kind]
        remaining = max(1, int((q.deadline-time.monotonic())*1000))
        sample_limit = int(diff_limit_block['effective']['limit']) if diff_limit_block else min(a['limit'], 25)
        result = diff_one_table(q.conn, kind, meta, sid1, sid2, a['devices'][0] if a['devices'] else None, sample_limit=sample_limit, timeout_ms=remaining)
        if result['status'] == 'missing':
            return error('source_missing', f'{kind} is absent')
        return {'status':'success','operation':op,'snapshot_id_1':sid1,'snapshot_id_2':sid2,
                'diff_kind':a.get('diff_kind','parsed_keys'),'diff':result,
                'temporal':{'source':q.temporal(sid1),'target':q.temporal(sid2)},
                'semantics':'identity-key set EXCEPT; added=target minus source; NULLS LAST; not content changes'}
    if op == 'devices':
        q.require('devices', FIELDS['devices'])
        params = []
        predicate = _in('hostname',a['devices'],params) + _in('vendor',a['vendors'],params)
        if 'snapshot_id' in a:
            sid = q.snapshot(a['snapshot_id'])
            predicate += ' AND EXISTS (SELECT 1 FROM netops.parsed_outputs p WHERE p.device_name=hostname AND p.snapshot_id=?)'
            params.append(sid)
        base = 'SELECT '+','.join(FIELDS['devices'])+' FROM netops.devices WHERE TRUE'+predicate
        order = ','.join(f'"{x}" {a["order"].upper() if i==0 else "ASC"} NULLS LAST' for i,x in enumerate([a['order_by']]+[x for x in FIELDS['devices'] if x != a['order_by']]))
        rows,total,more = _sample(q,base,params,a,a['fields'],order)
        return {'status':'success','operation':op,'snapshot_id':sid,'rows':rows,'total_rows':total,'has_more':more,'temporal':q.temporal(sid) if sid else record_temporal({},scope='inventory'), 'scope':'inventory; explicit snapshot restricts to observed members'}
    q.require('devices', ('hostname','vendor'))
    q.require('parsed_outputs', ('snapshot_id','device_name','command'))
    cte, params = _scope(a, sid)
    response = {'status':'success','operation':op,'snapshot_id':sid,'temporal':q.temporal(sid),
                'scope':{'devices':a['devices'],'vendors':a['vendors'],'commands':a['commands']},
                'sources':['netops.devices','netops.parsed_outputs']}
    if op in ('interface_health','bgp_health'):
        return {**response, **_health(q,a,sid,cte,params)}
    if op == 'snapshot_summary':
        counts = q.run(cte+'SELECT (SELECT count(DISTINCT hostname) FROM d), count(DISTINCT device_name), count(DISTINCT command),count(*) FROM p',params)[0]
        return {**response, 'counts':dict(zip(('inventory_device_count','observed_device_count','command_count','record_count'),counts)),
                'coverage':{'inventory_is_snapshot_membership':False,'command_count_excludes_null':True}}
    if op == 'devices':
        q.require('devices', FIELDS['devices'])
        base = cte+'SELECT '+','.join(FIELDS['devices'])+' FROM d'
    elif op in ('multi_device_summary','device_coverage'):
        summary = "SELECT d.hostname AS device_name,d.vendor,count(p.device_name) AS record_count,count(DISTINCT p.command) AS command_count,CASE WHEN count(p.device_name)=0 THEN 'no_matching_records' ELSE 'present' END AS collection_status FROM d LEFT JOIN p ON p.device_name=d.hostname GROUP BY d.hostname,d.vendor"
        base = cte+summary if op == 'multi_device_summary' else cte+', s AS ('+summary+') SELECT vendor,count(*) AS device_count,count(*) FILTER (WHERE record_count>0) AS observed_device_count FROM s GROUP BY vendor'
    elif op in ('commands','command_coverage'):
        base = cte+ ('SELECT device_name,command,count(*) AS record_count FROM p GROUP BY device_name,command' if op == 'commands' else 'SELECT command,count(DISTINCT device_name) AS device_count,count(*) AS record_count FROM p GROUP BY command')
    elif op == 'top_n':
        key = {'device':'p.device_name','command':'p.command','vendor':'d.vendor'}[a['group_by']]
        base = cte+f'SELECT {key} AS key,count(DISTINCT p.device_name) AS device_count,count(DISTINCT p.command) AS command_count,count(*) AS record_count FROM p JOIN d ON p.device_name=d.hostname GROUP BY {key}'
    else:
        raise LookupError('unsupported operation')
    order_cols = [a['order_by']] + [x for x in FIELDS[op] if x != a['order_by']]
    order = ','.join(f'"{x}" {a["order"].upper() if i==0 else "ASC"} NULLS LAST' for i,x in enumerate(order_cols))
    rows, total, more = _sample(q,base,params,a,a['fields'],order)
    # One bounded SQL query explains every explicitly requested missing device;
    # a LEFT JOIN distinguishes unknown inventory from zero matching collection.
    missing = []
    if a['devices']:
        placeholders = ','.join('(?)' for _ in a['devices'])
        missing = [{'device_name':r[0],'status':r[1]} for r in q.run(cte+f", req(device_name) AS (VALUES {placeholders}) SELECT req.device_name,CASE WHEN NOT EXISTS (SELECT 1 FROM d WHERE d.hostname=req.device_name) THEN 'outside_inventory_filter' ELSE 'no_matching_records' END FROM req WHERE NOT EXISTS (SELECT 1 FROM p WHERE p.device_name=req.device_name) ORDER BY req.device_name",params+a['devices'],50)]
    return {**response,'rows':rows,'total_rows':total,'has_more':more,'coverage':{'requested_devices_without_matches':missing,'inventory_is_snapshot_membership':False}}


def _merge_sources(*groups) -> list[str]:
    """Stable union of source groups: first occurrence wins, order preserved."""
    merged: list[str] = []
    for group in groups:
        for source in group:
            if source not in merged:
                merged.append(source)
    return merged


def _physical_sources(a: dict, result: dict, read_sources) -> list[str]:
    """Sources this operation actually read; never a capability inventory.

    One stable merge is shared by every branch: answer source(s) first, then
    the actual successful temporal reads (for example raw_output_store). A
    temporal read of the same table as the answer is one occurrence, not two;
    capability discovery and failed reads never enter ``read_sources``.
    """
    op = a.get('operation')
    if op == 'context':
        return []
    temporal = sorted('netops.' + table for table in read_sources)
    if op == 'devices':
        answer = ['netops.devices']
        if 'snapshot_id' in a:
            answer.append('netops.parsed_outputs')
        # An explicit snapshot resolves temporal metadata through the authorized
        # time source before rows are read. The global inventory path reads no
        # time source, so it stays free of temporal extras.
        return _merge_sources(answer, temporal)
    if op == 'diff_summary':
        kind = DIFF_KINDS.get(a.get('diff_kind', 'parsed_keys'), 'parsed_outputs')
        return _merge_sources(['netops.' + kind], temporal)
    answer = ['netops.devices', 'netops.parsed_outputs']
    if op in ('interface_health', 'bgp_health'):
        answer += [s['source'] for s in result.get('sources') or []
                   if s.get('status') == 'present']
    return _merge_sources(answer, temporal)


def _attach_contract(a: dict, result: dict, read_sources, *, requested_limit,
                     max_rows: int, max_bytes: int,
                     diff_limit_block: dict | None = None) -> dict | None:
    """One metadata block per successful result; none for a failed operation."""
    if result.get('status') != 'success':
        return None
    sources = _physical_sources(a, result, read_sources)
    if a.get('operation') == 'diff_summary':
        diff = result.get('diff') or {}
        if diff.get('status') != 'ok':
            return None
        if diff_limit_block is None:
            diff_limit_block, _ = diff_limits(requested_limit=requested_limit,
                                              host_cap=max_rows)
        return adapt_diff(diff, limit_block=diff_limit_block,
                          physical_sources=sources, cell_cap=MAX_CELL)
    contract_args = dict(a)
    if requested_limit is None:
        # _validate() injects the default into its working copy. The contract
        # must not report that injected value as a model request.
        contract_args.pop('limit', None)
    else:
        contract_args['limit'] = requested_limit
    return adapt_batch(contract_args, result, max_rows=max_rows, default_limit=10,
                       cell_cap=MAX_CELL, byte_cap=max_bytes,
                       physical_sources=sources)


def query_batch(*, temporal_sources: tuple[str, ...] | None = None,
                host_max_rows: int | None = None,
                host_max_bytes: int | None = None,
                host_timeout_ms: int | None = None,
                **arguments: Any) -> dict:
    """Run one fixed operation in the server-bound project database.

    ``host_*`` are trusted deployment caps, separate from the model arguments
    and from the canonical validation domain (``MAX_ROWS``). A host cap lowers
    the page/effective limits but never rewrites an in-range model request and
    never makes the canonical 1..50 validation accept 51.
    """
    from . import warehouse as _warehouse
    if _warehouse.configured():
        return _warehouse.batch(arguments, temporal_sources=temporal_sources, host_max_rows=host_max_rows, host_max_bytes=host_max_bytes, host_timeout_ms=host_timeout_ms)
    requested_limit = arguments.get('limit')
    try:
        a = _validate(arguments)
    except LookupError as exc:
        return error('unsupported_operation', str(exc))
    except (ValueError,TypeError) as exc:
        return error('validation_error', str(exc))
    # Do not fall back to cwd or another project if the host forgot its binding.
    home = os.environ.get('OLAV_HOME')
    if not home:
        return error('scope_error', 'host must bind OLAV_HOME to an authorized project root')
    path = Path(home) / '.olav' / 'databases' / 'main.duckdb'
    if not path.is_file():
        return error('source_missing','authorized main.duckdb is absent')
    read_sources: set = set()
    q = None
    effective_max_rows = MAX_ROWS if host_max_rows is None else max(1, min(MAX_ROWS, int(host_max_rows)))
    effective_max_bytes = MAX_BYTES if host_max_bytes is None else max(1, min(MAX_BYTES, int(host_max_bytes)))
    effective_timeout_ms = TIMEOUT_MS if host_timeout_ms is None else max(1, min(TIMEOUT_MS, int(host_timeout_ms)))
    # One canonical typed-diff limit construction binds the executor and the
    # metadata adapter to the same effective sample size and deciding layer.
    diff_limit_block = None
    if a.get('operation') == 'diff_summary':
        diff_limit_block, _ = diff_limits(requested_limit=requested_limit,
                                          host_cap=effective_max_rows)
    # The validated request stays in ``requested_limit``. The execution copy
    # carries only the effective page size, so a host clamp reduces rows without
    # changing the canonical domain or the recorded request/default facts.
    a['limit'] = min(a['limit'], effective_max_rows)
    try:
        with duckdb.connect(str(path),read_only=True,config=_resolve_connect_config()) as conn:
            q = Query(conn, temporal_sources=temporal_sources)
            q.deadline = time.monotonic() + effective_timeout_ms / 1000
            read_sources = q.read_sources
            result = _execute(q,a,diff_limit_block=diff_limit_block)
            result['limits'] = {'rows':MAX_ROWS,'response_bytes':MAX_BYTES,'cell_chars':MAX_CELL,
                                'timeout_ms':TIMEOUT_MS,'timeout_scope':'request except diff per-statement watchdog',
                                'fetched_rows':q.fetched_rows}
    except SnapshotDiffError as exc:
        result = exc.as_dict()
    except Exception as exc:
        result = error(_classify_db_error(exc),str(exc))
    # Samples are already SQL-limited and cell-bounded; byte trimming removes
    # only a stable prefix tail, retaining exact aggregate totals and metadata.
    read_sources = getattr(q, 'read_sources', read_sources)
    if 'rows' in result:
        result['cell_values_may_be_clipped'] = True
    # The contract is additive metadata and counts toward MAX_BYTES, so it is
    # rebuilt after each row drop: a bounded page must not report a stale
    # returned count.
    for _ in range(len(result.get('rows') or []) + 1):
        block = _attach_contract(a, result, read_sources,
                                 requested_limit=requested_limit,
                                 max_rows=effective_max_rows,
                                 max_bytes=effective_max_bytes,
                                 diff_limit_block=diff_limit_block)
        if block is not None:
            result['result_contract'] = block
        if len(json.dumps(result,ensure_ascii=False,default=str,separators=(',',':')).encode()) <= effective_max_bytes:
            break
        if not result.get('rows'):
            break
        result['rows'].pop()
        result['has_more'] = True
        result['byte_truncated'] = True
    return json.loads(serialize(result, effective_max_bytes))
