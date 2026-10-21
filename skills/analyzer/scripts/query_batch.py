#!/usr/bin/env python3
"""Bounded parameterized netops queries. Host must bind OLAV_HOME."""
import json
import sys
from olav_netops.core.query.batch import query_batch, serialize, error

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
