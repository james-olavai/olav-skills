"""Time provenance for recorded snapshots. Never use ingest/update as capture time.

Functions inspecting result rows do no I/O and grant no additional table access.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


def unknown(source: str | None = None, status: str = 'unknown') -> dict:
    return {'value': None, 'precision': 'unknown', 'source': source, 'status': status}


def time_value(value: Any, source: str, *, date_only: bool = False) -> dict:
    if value is None:
        return unknown(source)
    text = str(value).strip()
    try:
        if date_only or re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
                # Only the documented midnight placeholder may lose time.
                if not (date_only and re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]00:00:00(?:Z|[+]00:00)?', text)):
                    return unknown(source, 'invalid')
            parsed = date.fromisoformat(text[:10])
            return {'value': parsed.isoformat(), 'precision': 'date', 'source': source, 'status': 'known'}
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.]\d{1,6})?(?:Z|[+-]\d{2}(?::\d{2})?)?', text):
            return unknown(source, 'invalid')
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        # A naive database TIMESTAMP has lost timezone provenance. Preserve that
        # limitation; latest must not compare it with an offset-aware instant.
        aware = parsed.tzinfo is not None
        if aware:
            parsed = parsed.astimezone(timezone.utc)
        return {'value': parsed.isoformat(), 'precision': 'timestamp' if aware else 'timestamp_without_timezone',
                'source': source, 'status': 'known'}
    except (ValueError, TypeError, OverflowError):
        return unknown(source, 'invalid')


def snapshot_time(snapshot_id: Any) -> dict:
    text = str(snapshot_id or '')
    # Date labels are inference, never proof of device capture. A timestamp in
    # an auto-generated ingest ID is deliberately not treated as capture time.
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return unknown('snapshot_id')
    result = time_value(text, 'snapshot_id.date_label', date_only=True)
    if result['value']:
        result['status'] = 'derived'
    return result


def record_temporal(row: dict, *, scope: str = 'returned_record') -> dict:
    sid = row.get('snapshot_id')
    stamps = [time_value(row[key], 'raw_output_store.' + key, date_only=key == 'snapshot_date')
              for key in ('collected_at', 'captured_at', 'snapshot_date') if row.get(key) is not None]
    collected = stamps[0] if stamps else snapshot_time(sid)
    if any((stamp['value'], stamp['precision'], stamp['status']) !=
           (collected['value'], collected['precision'], collected['status']) for stamp in stamps[1:]):
        collected = unknown('raw_output_store.collection_fields', 'conflict')
    return {'snapshot_id': sid, 'collected_at': collected,
            'ingested_at': time_value(row.get('ingested_at'), 'raw_output_store.ingested_at'),
            'record_updated_at': time_value(row.get('updated_at'), 'raw_output_store.updated_at'),
            'event_time': unknown(),
            'coverage': {'scope': scope, 'status': collected['status'],
                         'note': 'Recorded data only; date labels are inferred, not verified capture. Event time is not inferred from CLI text.'}}


def sql_result_temporal(result: Any) -> dict:
    """Arbitrary SQL projections/aggregates do not establish dataset time scope."""
    result = record_temporal({}, scope='sql_result')
    result['coverage'].update(status='unknown', reason='sql_time_scope_not_inferred', note='Explicit projected row timestamps retain their own semantics; dataset/snapshot coverage is not inferred from arbitrary SQL.')
    return result


def interval(value: dict):
    """Comparable closed/open date interval or instant; unknown has no interval."""
    if not value.get('value'):
        return None
    if value.get('status') not in ('known', 'derived'):
        return None
    start_text = value.get('range_start', value['value'])
    end_text = value.get('range_end', value['value'])
    if value['precision'] == 'date':
        start = datetime.fromisoformat(start_text)
        end = datetime.fromisoformat(end_text) + timedelta(days=1)
        return start, end, 'date'
    parsed = datetime.fromisoformat(start_text)
    end = datetime.fromisoformat(end_text)
    return parsed, end, 'naive' if parsed.tzinfo is None else 'aware'
