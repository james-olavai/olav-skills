"""Pure bounded historical IOS CDP/LLDP extraction for dedicated native readers."""
from __future__ import annotations
import json
import re
from . import bounded_export as storage
from .netops.core.platform_canonical import canonicalize_platform
MAX_ROWS = 4096
MAX_INPUT_BYTES = 1024 * 1024
MAX_CELL_BYTES = 256
PROFILES = {'show cdp neighbors detail': 'CDP', 'show lldp neighbors detail': 'LLDP'}
class NeighborError(Exception):
    pass

def _label(value, cap=MAX_CELL_BYTES):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and value.isprintable() and len(value.encode()) <= cap)


def _no_constants(value):
    raise ValueError('non JSON number')


def _interface(value):
    # The shared engine catches arbitrary normalization exceptions. Here failures
    # remain explicit unknown instead, and descriptions/ifIndex/MACs are rejected.
    if not _label(value) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_./:-]*[0-9][A-Za-z0-9_./:-]*', value):
        return None
    if re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', value):
        return None
    try:
        from netutils.interface import canonical_interface_name
        return canonical_interface_name(value)
    except (ValueError, TypeError):
        return None


def read_history(conn, snapshot_ids):
    """Extract one/two exact SIDs; counterpart alignment belongs to diff caller."""
    if not isinstance(snapshot_ids, (tuple, list)) or not 1 <= len(snapshot_ids) <= 2 or any(not _label(sid) for sid in snapshot_ids) or len(set(snapshot_ids)) != len(snapshot_ids):
        raise NeighborError("validation_error")
    catalog = "database_name=system.main.current_database() AND schema_name='netops' AND table_name='parsed_outputs'"
    if conn.execute(f'SELECT 1 FROM system.main.duckdb_tables() WHERE {catalog} AND NOT internal AND NOT temporary LIMIT 1').fetchone() is None:
        raise NeighborError('source_table_absent')
    types = {'snapshot_id': 'VARCHAR', 'device_name': 'VARCHAR', 'command': 'VARCHAR', 'platform': 'VARCHAR', 'parsed_data': 'JSON'}
    marks = ','.join('?' for _ in types)
    actual = dict(conn.execute(f'SELECT column_name,data_type FROM system.main.duckdb_columns() WHERE {catalog} AND column_name IN ({marks}) LIMIT ?', [*types, len(types)]).fetchall())
    if actual != types:
        raise NeighborError('unsupported_schema')
    size = 'system.main.octet_length(system.main.encode(CAST(parsed_data AS VARCHAR)))'
    query = f"""SELECT snapshot_id,
       CASE WHEN system.main.octet_length(system.main.encode(device_name))<=256 THEN device_name END,
       command, CASE WHEN system.main.octet_length(system.main.encode(platform))<=256 THEN platform END,
       {size}, CASE WHEN {size}<=262144 THEN CAST(parsed_data AS VARCHAR) END
       FROM netops.parsed_outputs
       WHERE system.main.encode(snapshot_id) IN ({','.join('system.main.encode(?)' for _ in snapshot_ids)})
       AND system.main.encode(command) IN (system.main.encode(?),system.main.encode(?)) LIMIT ?"""
    cursor = conn.execute(query, [*snapshot_ids, *PROFILES, MAX_ROWS+1])
    sides = {sid: {'links': {}, 'reasons': set(), 'row_count': 0, 'parents': set()} for sid in snapshot_ids}
    consumed = records = 0
    for index in range(MAX_ROWS+1):
        parent = cursor.fetchone()
        if parent is None:
            break
        sid, device, command, platform, size, payload = parent
        consumed += size or 0
        if index == MAX_ROWS or (size is not None and size > 262144) or consumed > MAX_INPUT_BYTES:
            raise NeighborError('input_budget_exceeded')
        side = sides[sid]
        identity = (device, command)
        if identity in side['parents']:
            side['reasons'].add('duplicate_parent')
        side['parents'].add(identity)
        if not _label(device) or device.casefold() in {'unknown','none','null','n/a','-'} or canonicalize_platform(platform) != 'cisco_ios':
            side['reasons'].add('unknown_device_or_unsupported_platform')
            continue
        try:
            rows = json.loads(payload, object_pairs_hook=storage._unambiguous_object, parse_constant=_no_constants)
        except (ValueError, TypeError, RecursionError, storage.ExportError):
            rows = None
        if not isinstance(rows, list) or not rows:
            side['reasons'].add('empty_or_invalid_parsed_source')
            continue
        records += len(rows)
        if records > MAX_ROWS:
            raise NeighborError('input_budget_exceeded')
        seen = set()
        for row in rows:
            side['row_count'] += 1
            if not isinstance(row, dict):
                side['reasons'].add('invalid_parsed_record')
                continue
            neighbor = row.get('neighbor_name')
            local_port = _interface(row.get('local_interface'))
            # LLDP port description is arbitrary text; its port-id is the endpoint.
            remote_port = _interface(row.get('neighbor_interface' if PROFILES[command]=='CDP' else 'neighbor_port_id'))
            if not _label(neighbor) or neighbor.casefold() in {'unknown','none','null','n/a','-'} or local_port is None or remote_port is None:
                side['reasons'].add('unknown_endpoint_identity')
                continue
            a, b = (device,local_port), (neighbor,remote_port)
            if a == b:
                side['reasons'].add('self_endpoint')
                continue
            key = tuple(sorted((a,b)))
            if key in seen:
                side['reasons'].add('duplicate_endpoint_in_parent')
            seen.add(key)
            # Opposite-side and CDP/LLDP observations aggregate as protocol sets.
            # These stock templates carry no link-status, type or speed evidence.
            old = side['links'].get(key, ((),None,None,None))
            protocols = tuple(sorted(set(old[0]) | {PROFILES[command]}))
            side['links'][key] = (protocols,None,None,None)
    for side in sides.values():
        if not side['parents']:
            side['reasons'].add('snapshot_source_missing')
        if side['row_count'] == 0:
            side['reasons'].add('empty_or_uncollected_snapshot_source')
    return sides
