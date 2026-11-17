#!/usr/bin/env python3
"""Bounded parameterized netops queries. Host must bind OLAV_HOME."""
import json
import sys
from olav_skills.netops.core.query.batch import query_batch, serialize, error

from olav_skills.netops.core.query import warehouse as _candidate_warehouse

if __name__ == '__main__' and _candidate_warehouse.configured():
    _candidate_warehouse.candidate_main(query_batch, byte_cap=32768, output_bytes=12000, allowed=('operation', 'snapshot_id', 'snapshot_id_1', 'snapshot_id_2', 'devices', 'vendors', 'commands', 'fields', 'order_by', 'order', 'limit', 'group_by', 'metric', 'diff_kind'))

if __name__ == '__main__':
    try:
        raw = sys.stdin.read(32769)
        if len(raw.encode('utf-8')) > 32768:
            raise ValueError('stdin exceeds 32768 bytes')
        args = json.loads(raw or '{}')
        if not isinstance(args, dict):
            raise ValueError('stdin must be a JSON object')
        result = query_batch(**args)
    except (ValueError, TypeError) as exc:
        result = error('validation_error', str(exc))
    wire = serialize(result)
    print(wire)
    sys.exit(1 if json.loads(wire).get('status') == 'error' else 0)
