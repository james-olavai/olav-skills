"""Compare cumulative Cisco IOS interface counters in fixed native history."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import groupby
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from olav_skills.native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from olav_skills.native_snapshot import NativeSnapshotError, close_native_reader, open_native_snapshot
from olav_skills.netops.core.platform_canonical import canonicalize_platform
from olav_skills.netops.core.query.temporal import time_value


SCHEMA = "olav.counter-diff/v1"
READER_MODULE = "olav_skills.counter_deltas"
INTERFACES = "show interfaces"
VERSION = "show version"
PARSED = "parsed_outputs"
LEDGER = "ingest_sources"
IOS = "cisco_ios"
SID_FIELDS = ("snapshot_id_1", "snapshot_id_2")
SID_ALIASES = {"latest", "newest", "current"}
IDENTITY = {"snapshot_id": "VARCHAR", "device_name": "VARCHAR", "command": "VARCHAR"}
COUNTERS = {"input_errors": "errors", "crc": "errors", "output_errors": "errors",
            "queue_drops": "packets", "queue_output_drops": "packets"}
CLOCK_FIELDS = ("collected_at", "captured_at")
CLOCK_TYPES = {"VARCHAR", "TIMESTAMP WITH TIME ZONE"}
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_SAMPLE_BYTES = 48 * 1024
MAX_DB_BYTES = 1024 * 1024 * 1024
MAX_RECORD_BYTES = 512 * 1024
MAX_SELECTED_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_LEAF_ROWS = 8192
MAX_OBSERVATIONS = 250_000
MAX_PARENT_ROWS = 16_384
TIMEOUT_SECONDS = 10
ADDRESS_SPACE_BYTES = 768 * 1024 * 1024
DIGITS = re.compile(r"[0-9]{1,38}")
INTERFACE_HEADER = re.compile(r"^(\S+)\s+is\s+.+?,\s+line\s+protocol(?:\s|$)")
CLEARING = re.compile(r'^\s+Last clearing of "show interface" counters (\S.*)$')
DURATION = re.compile(r"([0-9]{2}):([0-5][0-9]):([0-5][0-9])")
IOS_TIMESTAMP = re.compile(
    r"([0-9]{1,2}):([0-5][0-9]):([0-5][0-9]) (?:UTC|GMT) "
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +"
    r"([0-9]{1,2}) ([0-9]{4})"
)
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
WEEKDAYS = "Mon Tue Wed Thu Fri Sat Sun".split()


class CounterDiffError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _number(value):
    return int(value) if DIGITS.fullmatch(value) else None


def _load(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_int=_number, parse_constant=lambda _: None)


def _label(value, limit=256):
    if (not isinstance(value, str) or not value or len(value) > limit or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def _request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise CounterDiffError("request_budget_exceeded", "Counter comparison request exceeds 16 KiB.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if not isinstance(request, dict) or request.keys() - {*SID_FIELDS, "device", "interface", "limit"}:
        raise CounterDiffError("invalid_request", "Expected explicit snapshot IDs and optional device, interface and limit.")
    sids = [request.get(key) for key in SID_FIELDS]
    if any(not _label(sid) or sid.casefold() in SID_ALIASES for sid in sids) or sids[0] == sids[1]:
        raise CounterDiffError("invalid_snapshot_id", "Supply two distinct, exact historical snapshot IDs.")
    for key in ("device", "interface"):
        value = request.setdefault(key, None)
        if value is not None and not _label(value):
            raise CounterDiffError("invalid_request", f"Supply an exact nonempty {key} of at most 256 bytes.")
    limit = request.setdefault("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CounterDiffError("invalid_request", "Sample limit must be an integer from 1 to 50.")
    return request


def _failure(code, message):
    return {"schema": SCHEMA, "status": "error", "comparison_status": "unknown",
            "error_type": code, "message": message, "counts": None, "samples": []}


def compare_counters(request):
    """Return a bounded comparison of recorded counters in explicit OLAV_HOME."""
    try:
        raw = _wire(request)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return _failure("invalid_request", "Request must contain valid UTF-8 JSON.")
    return run_counter_diff(raw)


def run_counter_diff(raw):
    """Run the same bounded operation for the library and stdin entry point."""
    try:
        _request(raw)
        if sys.platform != "linux":
            raise CounterDiffError("unsupported_platform", "Counter comparison requires native Linux.")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with tempfile.TemporaryDirectory(prefix="olav-counter-diff-") as scratch:
            result = subprocess.run(
                [sys.executable, "-B", "-m", READER_MODULE, scratch, repr(deadline)],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=max(0, deadline - time.monotonic()), check=False, env={**os.environ, "TZ": "UTC"},
            )
        if result.returncode or not result.stdout or len(result.stdout) > MAX_RESPONSE_BYTES + 1:
            return _failure("worker_failed", "Counter worker did not return a bounded result.")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return _failure("deadline_exceeded", "Counter comparison exceeded its deadline.")
    except CounterDiffError as exc:
        return _failure(exc.code, str(exc))
    except (ValueError, UnicodeError, RecursionError):
        return _failure("invalid_request", "Request must contain one unambiguous UTF-8 object.")
    except OSError:
        return _failure("worker_failed", "Counter worker could not start.")


def _project():
    home = os.environ.get("OLAV_HOME")
    if not _label(home, 2048):
        raise CounterDiffError("invalid_project", "Set OLAV_HOME to an explicit absolute native project directory.")
    project = Path(home)
    if not project.is_absolute() or project.anchor != "/" or project == Path("/") or str(project) != home or ".." in project.parts:
        raise CounterDiffError("invalid_project", "Set OLAV_HOME to an explicit absolute native project directory.")
    return project


def _encoded(value):
    return f"system.main.encode({value})"


def _octets(value):
    return f"system.main.octet_length({_encoded(value)})"


def _columns(conn, table, required, *, optional=False):
    scope = ("system.main.encode(database_name)=system.main.encode(system.main.current_database()) "
             "AND system.main.encode(schema_name)=system.main.encode('netops') "
             "AND system.main.encode(table_name)=system.main.encode(?)")
    found = conn.execute(
        f"SELECT 1 FROM system.main.duckdb_tables() WHERE {scope} AND NOT internal AND NOT temporary LIMIT 1",
        [table],
    ).fetchone()
    if not found:
        view_scope = scope.replace("table_name", "view_name")
        view = conn.execute(f"SELECT 1 FROM system.main.duckdb_views() WHERE {view_scope} LIMIT 1", [table]).fetchone()
        if view or not optional:
            raise CounterDiffError("unsupported_source_schema", f"Expected a native base table for netops.{table}.")
        return {}
    columns = dict(conn.execute(
        f"SELECT column_name, data_type FROM system.main.duckdb_columns() WHERE {scope} LIMIT 65", [table]
    ).fetchmany(65))
    if len(columns) > 64 or any(columns.get(name) not in types for name, types in required.items()):
        raise CounterDiffError("unsupported_source_schema", f"Unexpected native columns for netops.{table}.")
    return columns


def _identity_matches():
    return " AND ".join(f"{_encoded(name)}={_encoded('?')}" for name in IDENTITY)


def _when_bounded(value, limit):
    return f"CASE WHEN {_octets(value)} <= {limit} THEN {value} END"


def _clock_columns(columns):
    return tuple(key for key in CLOCK_FIELDS if columns.get(key) in CLOCK_TYPES)


def _select_clocks(columns):
    projections = [f"CASE WHEN {name} IS NOT NULL THEN coalesce("
                   f"{_when_bounded(f'CAST({name} AS VARCHAR)', 128)}, '') END"
                   for name in _clock_columns(columns)]
    return "".join(", " + item for item in projections)


def _time(value, source):
    stamp = time_value(value, source)
    if stamp["status"] != "known" or stamp["precision"] != "timestamp":
        return None
    return datetime.fromisoformat(stamp["value"])


def _parsed_clock(names, values):
    times = [(name, _time(value, name)) for name, value in zip(names, values) if value is not None]
    if not times:
        return None, None, "unknown"
    if any(not stamp or stamp != times[0][1] for _, stamp in times):
        return None, None, "invalid_or_conflicting_capture_time"
    return times[0][1], "netops.parsed_outputs." + times[0][0], "known"


def _clock_json(stamp):
    instant, source, status = stamp
    return {"value": instant.isoformat() if instant else None, "source": source, "status": status}


def _firm_ledger(conn, sid, device, command):
    """Validate the exact command source and its raw payload before using evidence."""
    raw, meta = "raw_output", "source_json"
    rows = conn.execute(
        f"SELECT {_when_bounded(raw, MAX_RECORD_BYTES)}, {_when_bounded(meta, MAX_METADATA_BYTES)} "
        f"FROM netops.{LEDGER} WHERE {_identity_matches()} LIMIT 2",
        [sid, device, command],
    ).fetchmany(2)
    if not rows:
        return None, (None, None, "unknown")
    invalid = (None, None, "invalid_capture_source")
    if len(rows) != 1 or any(not value for value in rows[0]):
        return None, invalid
    body, raw_meta = rows[0]
    try:
        metadata = _load(raw_meta)
    except (ValueError, RecursionError):
        return None, invalid
    if (not isinstance(metadata, dict)
            or metadata.get("raw_artifact") != {"table": "netops.ingest_sources", "snapshot_id": sid,
                                               "device_name": device, "command": command}
            or metadata.get("device_name") != device or metadata.get("command") != command
            or metadata.get("effective_platform") != IOS or metadata.get("parse_status") != "parsed"
            or metadata.get("actual_raw_sha256") != hashlib.sha256(body.encode()).hexdigest()):
        return None, invalid
    clock = metadata.get("collected_at")
    if not isinstance(clock, dict) or clock.get("status") in ("invalid", "partial", "conflict"):
        return body, invalid
    if clock.get("status") != "known" or clock.get("source") != "command_header":
        return body, (None, None, "unknown")
    evidence = metadata.get("capture_evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        return body, invalid
    header = [item for item in evidence if item.get("source") == "command_header"]
    if len(header) != 1 or header[0] != clock or any(
        item.get("status") == "invalid"
        or (item.get("status") == "known" and
            (item.get("value"), item.get("precision")) != (clock.get("value"), clock.get("precision")))
        for item in evidence
    ):
        return body, invalid
    source = "netops.ingest_sources.command_header"
    stamp = _time(clock.get("value"), source) if clock.get("precision") == "timestamp" else None
    return body, ((stamp, source, "known") if stamp else (None, None, "invalid_or_conflicting_capture_time"))


def _clock(conn, names, values, ledger, sid, device, command):
    parsed = _parsed_clock(names, values)
    if not ledger:
        return parsed, None
    body, recorded = _firm_ledger(conn, sid, device, command)
    if recorded[2] == "unknown":
        return parsed, body
    if parsed[2] == "unknown":
        return recorded, body
    if parsed[0] and parsed[0] == recorded[0]:
        return recorded, body
    return (None, None, "invalid_or_conflicting_capture_time"), body


def _observed_int(value):
    if type(value) is int and value >= 0:
        return value
    return _number(value) if isinstance(value, str) else None


def _decode_interfaces(payload, selected):
    if payload is None:
        return {}
    try:
        rows = _load(payload)
    except (ValueError, RecursionError) as exc:
        raise CounterDiffError("unsupported_source_schema", "Invalid or ambiguous parsed interface JSON.") from exc
    if not isinstance(rows, list):
        raise CounterDiffError("unsupported_source_schema", "Parsed interfaces must be a JSON array.")
    if len(rows) > MAX_LEAF_ROWS:
        raise CounterDiffError("row_budget_exceeded", "A parsed command exceeds the interface row budget.")
    interfaces = {}
    for row in rows:
        if not isinstance(row, dict) or not _label(row.get("interface")):
            raise CounterDiffError("unknown_interface_identity", "A selected parsed interface has no bounded identity.")
        identity = row["interface"]
        if selected is not None and identity != selected:
            continue
        if identity in interfaces:
            raise CounterDiffError("ambiguous_interface_identity", "Duplicate exact interface within a selected command.")
        interfaces[identity] = {field: _observed_int(row.get(field)) for field in COUNTERS}
    return interfaces


def _ios_time(value):
    if not _label(value, 128):
        return None
    iso = _time(value, "ios_clock")
    if iso:
        return iso
    match = IOS_TIMESTAMP.fullmatch(value)
    if not match:
        return None
    hour, minute, second, weekday, month, day, year = match.groups()
    try:
        stamp = datetime(int(year), MONTHS.index(month) + 1, int(day), int(hour), int(minute), int(second),
                         tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp if WEEKDAYS[stamp.weekday()] == weekday else None


def _reboot(conn, columns, ledger, device, sid, before, after):
    """Consume only an exact IOS show-version observation with its own time."""
    parsed = "CAST(parsed_data AS VARCHAR)"
    result = conn.execute(
        f"SELECT {_when_bounded('platform', 256)}, {_when_bounded(parsed, MAX_METADATA_BYTES)}"
        f"{_select_clocks(columns)} "
        f"FROM netops.{PARSED} WHERE {_identity_matches()} LIMIT 2", [sid, device, VERSION]
    ).fetchmany(2)
    if len(result) != 1:
        return None
    platform, payload, *clock_values = result[0]
    if canonicalize_platform(platform) != IOS or not payload:
        return None
    try:
        rows = _load(payload)
    except (ValueError, RecursionError):
        return None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    restarted = _ios_time(rows[0].get("restarted"))
    if restarted is None or not before < restarted < after:
        return None
    clock, _ = _clock(conn, _clock_columns(columns), clock_values, ledger, sid, device, VERSION)
    if not clock[0] or clock[0] < restarted:
        return None
    return {"kind": "device_restart", "source": "netops.parsed_outputs.show version.restarted",
            "value": restarted.isoformat(), "observed_at": _clock_json(clock)}


def _clearing_statements(body):
    if not body:
        return {}
    from olav_skills.netops.tools.field_normalizer import normalize_fields

    matches = {}
    interface = None
    for line in body.splitlines():
        header = INTERFACE_HEADER.match(line)
        if header:
            interface = normalize_fields([{"interface": header[1]}])[0]["interface"]
            if interface in matches:
                matches[interface].append(None)
            else:
                matches[interface] = []
        elif line and not line[0].isspace():
            interface = None
        elif interface:
            clearing = CLEARING.fullmatch(line)
            if clearing:
                matches[interface].append(clearing[1].strip())
    return {interface: statements[0] for interface, statements in matches.items()
            if len(statements) == 1}


def _cleared(statement, before, after):
    absolute = _ios_time(statement)
    source = 'netops.ingest_sources.show interfaces.Last clearing of "show interface" counters'
    if absolute and before < absolute < after:
        return {"kind": "interface_counter_clear", "source": source, "value": absolute.isoformat()}
    duration = DURATION.fullmatch(statement or "")
    if duration:
        hours, minutes, seconds = map(int, duration.groups())
        try:
            latest = after - timedelta(hours=hours, minutes=minutes, seconds=seconds)
            earliest = latest - timedelta(seconds=1)
        except OverflowError:
            return None
        if before < earliest <= latest < after:
            return {"kind": "interface_counter_clear", "source": source,
                    "earliest": earliest.isoformat(), "latest": latest.isoformat()}
    return None


def _counter(before, after, unit, discontinuities, elapsed):
    result = {"before": before, "after": after, "unit": unit, "delta": None,
              "delta_status": "unknown", "rate": None, "rate_unit": unit + "/second", "rate_status": "unknown"}
    if before is None or after is None:
        return result
    if discontinuities:
        result["delta_status"] = "confirmed_discontinuity"
    elif after < before:
        result["delta_status"] = "possible_reset_or_wrap"
    else:
        delta = after - before
        result.update(delta=delta, delta_status="known")
        if elapsed is not None:
            result.update(rate=delta / elapsed, rate_status="known")
    return result


def _sample(device, interface, before, after, clocks, reboot, clearing):
    start, end = (item[0] for item in clocks)
    elapsed, time_status = None, "unknown"
    if start is not None and end is not None:
        if end > start:
            elapsed, time_status = (end - start).total_seconds(), "known"
        else:
            time_status = "nonpositive_interval"
    evidence = []
    if before is not None and after is not None and elapsed is not None:
        cleared = _cleared(clearing, start, end)
        evidence = [item for item in (reboot, cleared) if item is not None]
    coverage = "matched" if before is not None and after is not None else "before_only" if after is None else "after_only"
    counters = {}
    for name, unit in COUNTERS.items():
        counters[name] = _counter((before or {}).get(name), (after or {}).get(name), unit, evidence, elapsed)
    return {"device": device, "interface": interface, "observation_status": coverage,
            "collection_time": {"before": _clock_json(clocks[0]), "after": _clock_json(clocks[1])},
            "elapsed_seconds": elapsed, "time_status": time_status, "discontinuities": evidence,
            "counters": counters}


def _scope(request):
    params = [*(request[sid] for sid in SID_FIELDS), INTERFACES]
    scope = (f"{_encoded('snapshot_id')} IN ({_encoded('?')}, {_encoded('?')}) "
             f"AND {_encoded('command')}={_encoded('?')}")
    if request["device"] is not None:
        scope += f" AND {_encoded('device_name')}={_encoded('?')}"
        params.append(request["device"])
    return scope, params


def _compare(conn, version, request, deadline):
    required = {name: {kind} for name, kind in (IDENTITY | {"platform": "VARCHAR"}).items()}
    columns = _columns(conn, PARSED, required | {"parsed_data": {"VARCHAR", "JSON"}})
    ledger_requirements = {name: {kind} for name, kind in
                           (IDENTITY | {"source_json": "VARCHAR", "raw_output": "VARCHAR"}).items()}
    ledger = bool(_columns(conn, LEDGER, ledger_requirements, optional=True))
    scope, params = _scope(request)
    parsed = "CAST(parsed_data AS VARCHAR)"
    payload_size = _octets(parsed)
    count, max_record, all_records, bad_identity = conn.execute(
        f"SELECT count(*), coalesce(max({payload_size}), 0), coalesce(sum({payload_size}), 0), "
        f"count(*) FILTER (WHERE device_name IS NULL OR {_octets('device_name')} > 256 "
        f"OR {_octets('platform')} > 256) FROM netops.{PARSED} WHERE {scope}", params
    ).fetchone()
    if count > MAX_PARENT_ROWS or max_record > MAX_RECORD_BYTES or all_records > MAX_SELECTED_BYTES:
        raise CounterDiffError("source_budget_exceeded", "Selected historical command payloads exceed the fixed comparison budget.")
    if bad_identity:
        raise CounterDiffError("unknown_device_identity", "A selected command has no bounded device or platform identity.")
    counts = {key: 0 for key in ("before_source_rows", "after_source_rows", "before_unsupported_source_rows",
                                  "after_unsupported_source_rows", "before_observed_interfaces",
                                  "after_observed_interfaces", "matched_interfaces", "before_only_interfaces",
                                  "after_only_interfaces")}
    counter_counts = {name: Counter(known_deltas=0, unknown_deltas=0, possible_reset_or_wrap=0,
                                    confirmed_discontinuity=0, known_rates=0, unknown_rates=0)
                      for name in COUNTERS}
    samples, sample_bytes, byte_truncated, observations = [], 2, False, 0
    sides = {request[key]: side for key, side in zip(SID_FIELDS, ("before", "after"))}
    # A separate cursor preserves the streaming result while fetching optional
    # per-device evidence. Only the current device's two payloads enter Python.
    with conn.cursor() as stream:
        stream.execute(
            f"SELECT device_name, snapshot_id, platform, {parsed}{_select_clocks(columns)} "
            f"FROM netops.{PARSED} WHERE {scope} ORDER BY {_encoded('device_name')}, {_encoded('snapshot_id')}",
            params,
        )
        rows = iter(stream.fetchone, None)
        for device, group in groupby(rows, key=lambda row: row[0]):
            if time.monotonic() >= deadline:
                raise CounterDiffError("deadline_exceeded", "Counter comparison exceeded its deadline.")
            if not _label(device):
                raise CounterDiffError("unknown_device_identity", "A selected command has no bounded device identity.")
            states, clocks, bodies = {}, {}, {}
            for _, sid, platform, payload, *clock_values in group:
                side = sides[sid]
                count_key = f"{side}_source_rows"
                if side in states:
                    raise CounterDiffError("ambiguous_source", "Duplicate exact device, command and snapshot.")
                counts[count_key] += 1
                states[side] = {}
                if canonicalize_platform(platform) != IOS:
                    counts[f"{side}_unsupported_source_rows"] += 1
                    continue
                states[side] = _decode_interfaces(payload, request["interface"])
                if states[side]:
                    clocks[side], bodies[side] = _clock(conn, _clock_columns(columns), clock_values, ledger,
                                                       sid, device, INTERFACES)
            old, new = states.get("before", {}), states.get("after", {})
            matched = old.keys() & new.keys()
            counts["before_observed_interfaces"] += len(old)
            counts["after_observed_interfaces"] += len(new)
            counts["matched_interfaces"] += len(matched)
            counts["before_only_interfaces"] += len(old) - len(matched)
            counts["after_only_interfaces"] += len(new) - len(matched)
            interface_ids = old.keys() | new.keys()
            observations += len(interface_ids)
            if observations > MAX_OBSERVATIONS:
                raise CounterDiffError("row_budget_exceeded", "Selected interface observations exceed the fixed comparison budget.")
            cleared, reboot = {}, None
            if matched and "before" in clocks and "after" in clocks:
                start, end = clocks["before"][0], clocks["after"][0]
                if start and end and start < end:
                    reboot = _reboot(conn, columns, ledger, device, request[SID_FIELDS[1]], start, end)
                    cleared = _clearing_statements(bodies.get("after"))
            for interface in sorted(interface_ids):
                pair = tuple(clocks[side] if interface in states.get(side, {}) else (None, None, "unknown")
                             for side in ("before", "after"))
                item = _sample(device, interface, old.get(interface), new.get(interface), pair,
                               reboot, cleared.get(interface))
                for field, evidence in item["counters"].items():
                    counts_for_field = counter_counts[field]
                    counter_state = evidence["delta_status"]
                    if counter_state == "known":
                        counts_for_field["known_deltas"] += 1
                    else:
                        counts_for_field["unknown_deltas"] += 1
                        if counter_state != "unknown":
                            counts_for_field[counter_state] += 1
                    counts_for_field[evidence["rate_status"] + "_rates"] += 1
                if len(samples) < request["limit"] and not byte_truncated:
                    next_size = len(_wire(item)) + bool(samples)
                    if sample_bytes + next_size <= MAX_SAMPLE_BYTES:
                        samples.append(item)
                        sample_bytes += next_size
                    else:
                        byte_truncated = True
    status = "unknown"
    if counts["matched_interfaces"]:
        status = "observed" if all(c["known_deltas"] == observations for c in counter_counts.values()) else "partial"
    return {"schema": SCHEMA, "status": "success", "comparison_status": status,
            "scope": {key: request[key] for key in (*SID_FIELDS, "device", "interface")},
            "source": {"table": f"netops.{PARSED}", "command": INTERFACES, "platform": IOS},
            "source_version": version, "collection_coverage": "unknown",
            "counts": {**counts, "counters": counter_counts}, "samples": samples,
            "sample_count": len(samples), "has_more": observations > len(samples), "sample_byte_truncated": byte_truncated}


def _worker(scratch, deadline):
    import resource

    try:
        validate_native_reader_identity(READER_MODULE)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if soft == resource.RLIM_INFINITY or soft > ADDRESS_SPACE_BYTES:
            resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, hard))
        duckdb = bootstrap_native_reader(READER_MODULE)
    except NativeReaderBootstrapError as exc:
        return _failure(str(exc), "Counter comparison reader initialization was rejected.")

    try:
        request = _request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
        with open_native_snapshot(_project(), scratch, max_bytes=MAX_DB_BYTES, deadline=deadline) as (conn, version):
            result = _compare(conn, version, request, deadline)
        payload = _wire(result)
        if len(payload) > MAX_RESPONSE_BYTES:
            return _failure("response_budget_exceeded", "Counter comparison result exceeds 64 KiB.")
        return result
    except (CounterDiffError, NativeSnapshotError) as exc:
        return _failure(exc.code, str(exc))
    except (MemoryError, duckdb.OutOfMemoryException):
        return _failure("memory_budget_exceeded", "Counter comparison exceeded its memory budget.")
    except duckdb.Error:
        return _failure("source_query_failed", "Could not read the selected native historical evidence.")


def main():
    result = run_counter_diff(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    sys.stdout.buffer.write(_wire(result) + b"\n")
    return int(result["status"] != "success")


if __name__ == "__main__":
    result = _worker(Path(sys.argv[1]), float(sys.argv[2]))
    if owns_native_reader(READER_MODULE):
        close_native_reader()
    sys.stdout.buffer.write(_wire(result) + b"\n")
