"""Bounded comparison of two recorded raw configuration sources."""
from __future__ import annotations

from collections import defaultdict
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, NamedTuple

from olav_skills.native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from olav_skills.native_snapshot import NativeSnapshotError, close_native_reader, open_native_snapshot


SCHEMA = "olav.config-diff/v1"
READER_MODULE = "olav_skills.netops.core.diff.configs"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_DB_BYTES = 1024 * 1024 * 1024
MAX_RAW_BYTES = 256 * 1024
MAX_RAW_LINES = 4000
MAX_METADATA_BYTES = 64 * 1024
MAX_DIFF_BYTES = 24 * 1024
MAX_GROUP_BYTES = 24 * 1024
MAX_GROUPS = 100
DEADLINE_SECONDS = 10
ADDRESS_SPACE_BYTES = 768 * 1024 * 1024
LEDGER = "ingest_sources"
RAW_STORE = "raw_output_store"
IDENTITY = ("snapshot_id", "device_name", "command")
CLOCK_FIELDS = ("collected_at", "captured_at", "snapshot_date")
RESERVED_SIDS = {"latest", "current", "newest"}
REQUIRED = {"device", "command", "snapshot_id_1", "snapshot_id_2"}
OPTIONAL = {"context_lines", "sections", "full"}
GLOBAL = ("global", "global")
BANNER = re.compile(r"banner[ \t]+\S+[ \t]+(.+)", re.IGNORECASE)
NOISE = re.compile(
    r"(?:Building configuration\.\.\.|Current configuration[ \t]*:[ \t]*[0-9]+ bytes"
    r"|![ \t]+(?:Last configuration change|NVRAM config last updated) at "
    r"[0-9]{1,2}:[0-9]{2}:[0-9]{2} [A-Za-z][A-Za-z0-9+\-]{0,9} "
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +[0-9]{1,2} [0-9]{4}"
    r"(?: by \S+)?)?[ \t]*"
)


class ConfigDiffError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.side = None
        self.table = None


class ConfigLine(NamedTuple):
    block: tuple[str, str]
    stanza: str
    text: str


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON member")
        obj[key] = value
    return obj


def _label(value, max_bytes):
    return (isinstance(value, str) and 0 < len(value) <= max_bytes
            and value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and len(value.encode("utf-8")) <= max_bytes)


def _request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise ConfigDiffError("request_budget_exceeded", "Request exceeds 16 KiB.")
    obj = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(obj, dict) or obj.keys() - REQUIRED - OPTIONAL:
        raise ConfigDiffError("invalid_request", "Use one object with documented configuration diff fields.")
    if any(not _label(obj.get(key), limit) for key, limit in (("device", 512), ("command", 256))):
        raise ConfigDiffError("invalid_request", "Supply an exact device and command.")
    sids = (obj.get("snapshot_id_1"), obj.get("snapshot_id_2"))
    if any(not _label(sid, 256) or sid.casefold() in RESERVED_SIDS for sid in sids) or sids[0] == sids[1]:
        raise ConfigDiffError("invalid_snapshot_id", "Supply two distinct explicit snapshot IDs.")
    obj.setdefault("context_lines", 3)
    obj.setdefault("full", False)
    if type(obj["context_lines"]) is not int or not 0 <= obj["context_lines"] <= 10:
        raise ConfigDiffError("invalid_request", "context_lines must be an integer from 0 to 10.")
    if type(obj["full"]) is not bool:
        raise ConfigDiffError("invalid_request", "full must be a boolean.")
    sections = obj.get("sections")
    if sections is not None and (
        not isinstance(sections, list) or len(sections) > 16
        or any(not _label(section, 256) for section in sections)
    ):
        raise ConfigDiffError("invalid_request", "sections must contain up to 16 bounded labels.")
    obj["sections"] = sections or []
    return obj


def _failure(error, request=None):
    result = {"schema": SCHEMA, "status": "error", "diff_status": "unknown",
              "error_type": error.code, "message": str(error)}
    if request:
        result.update({key: request[key] for key in REQUIRED})
    if error.side:
        result["side"] = error.side
    if error.table:
        result["source_table"] = "netops." + error.table
    return result


def diff_configs(
    device: str,
    command: str,
    snapshot_id_1: str | None = None,
    snapshot_id_2: str | None = None,
    sections: list[str] | None = None,
    context_lines: int = 3,
    full: bool = False,
) -> dict[str, Any]:
    """Compare exact recorded source labels in the authorized native project."""
    try:
        raw = _json(dict(device=device, command=command, snapshot_id_1=snapshot_id_1,
                         snapshot_id_2=snapshot_id_2, sections=sections,
                         context_lines=context_lines, full=full))
    except (TypeError, ValueError, RecursionError):
        return _failure(ConfigDiffError("invalid_request", "Request must contain JSON values."))
    return run_config_diff(raw)


def run_config_diff(raw: bytes) -> dict[str, Any]:
    """Run a bounded operation regardless of whether the caller is the script or library."""
    request = None
    try:
        request = _request(raw)
        if sys.platform != "linux":
            raise ConfigDiffError("unsupported_platform", "Configuration diff requires a native Linux database.")
        with tempfile.TemporaryDirectory(prefix="olav-config-diff-") as scratch:
            result = subprocess.run(
                [sys.executable, "-B", "-m", READER_MODULE, scratch],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=DEADLINE_SECONDS, check=False, env={**os.environ, "TZ": "UTC"},
            )
        if result.returncode or len(result.stdout) > MAX_RESPONSE_BYTES + 1:
            raise ConfigDiffError("worker_failed", "Configuration diff worker did not return a bounded result.")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        error = ConfigDiffError("deadline_exceeded", "Configuration diff exceeded its 10 second deadline.")
    except ConfigDiffError as exc:
        error = exc
    except (ValueError, UnicodeError, RecursionError):
        error = ConfigDiffError("invalid_request", "Request must contain an unambiguous UTF-8 JSON object.")
    except OSError:
        error = ConfigDiffError("worker_failed", "Configuration diff worker could not start.")
    return _failure(error, request)


def _project():
    home = os.environ.get("OLAV_HOME", "")
    project = Path(home)
    if (not _label(home, 2048) or not project.is_absolute() or project == Path("/")
            or str(project) != home or ".." in project.parts or home.startswith("//")):
        raise ConfigDiffError("invalid_project", "Set OLAV_HOME to an explicit absolute native project directory.")
    return project


def _source_columns(conn, table):
    present = conn.execute("""
        SELECT table_name FROM system.main.duckdb_tables()
        WHERE system.main.encode(database_name)=system.main.encode(system.main.current_database())
          AND system.main.encode(schema_name)=system.main.encode('netops')
          AND system.main.encode(table_name)=system.main.encode(?)
          AND NOT internal AND NOT temporary LIMIT 2
    """, [table]).fetchall()
    if not present:
        view = conn.execute("""
            SELECT 1 FROM system.main.duckdb_views()
            WHERE system.main.encode(database_name)=system.main.encode(system.main.current_database())
              AND system.main.encode(schema_name)=system.main.encode('netops')
              AND system.main.encode(view_name)=system.main.encode(?) LIMIT 1
        """, [table]).fetchone()
        if view:
            raise ConfigDiffError("unsupported_source_schema", "Raw source must be a native base table.")
        return None
    columns = dict(conn.execute("""
        SELECT column_name, data_type FROM system.main.duckdb_columns()
        WHERE system.main.encode(database_name)=system.main.encode(system.main.current_database())
          AND system.main.encode(schema_name)=system.main.encode('netops')
          AND system.main.encode(table_name)=system.main.encode(?)
          LIMIT 65
    """, [table]).fetchall())
    required = (*IDENTITY, "raw_output", *(["source_json"] if table == LEDGER else []))
    if len(present) != 1 or len(columns) > 64 or any(columns.get(key) != "VARCHAR" for key in required):
        raise ConfigDiffError("unsupported_source_schema", "Raw source must have native VARCHAR identity and body columns.")
    return columns


def _exact(key):
    return f'system.main.encode("{key}") = system.main.encode(?)'


def _bytes_limit(key):
    return f'system.main.octet_length(system.main.encode("{key}")) <= ?'


def _ledger_time(value):
    from olav_skills.netops.core.query.temporal import time_value, unknown

    source = "netops.ingest_sources.source_json.collected_at"
    if value is None:
        return unknown()
    if not isinstance(value, dict):
        return unknown(source, "invalid")
    status = value.get("status")
    reported_source = value.get("source")
    if status in ("known", "partial") and _label(reported_source, 128) and _label(value.get("value"), 128):
        parsed = time_value(value["value"], source)
        if parsed["status"] == "known" and value.get("precision") == parsed["precision"]:
            return {**parsed, "status": status, "recorded_source": reported_source}
    elif status in ("unknown", "conflict", "invalid") and value.get("value") is None:
        return unknown(source, status)
    return unknown(source, "invalid")


def _read_clock(fields, values):
    from olav_skills.netops.core.query.temporal import time_value, unknown

    evidence = [time_value(value, f"netops.raw_output_store.{key}", date_only=kind == "DATE")
                for (key, kind), value in zip(fields, values) if value is not None]
    if not evidence:
        return unknown()
    if any((stamp["value"], stamp["precision"], stamp["status"]) !=
           (evidence[0]["value"], evidence[0]["precision"], evidence[0]["status"])
           for stamp in evidence[1:]):
        return unknown("netops.raw_output_store.collection_fields", "conflict")
    return evidence[0]


def _source_for_sid(conn, sid):
    if _source_columns(conn, LEDGER) is not None and conn.execute(
        f"SELECT 1 FROM netops.{LEDGER} WHERE {_exact('snapshot_id')} LIMIT 1", [sid],
    ).fetchone():
        return LEDGER, []
    columns = _source_columns(conn, RAW_STORE)
    if columns is None:
        raise ConfigDiffError("source_unavailable", "No supported raw source is available for this snapshot.")
    fields = [(key, columns[key]) for key in CLOCK_FIELDS
              if columns.get(key, "").startswith(("DATE", "TIMESTAMP"))]
    return RAW_STORE, fields


def _read_side(conn, sid, device, command, side):
    table = None
    try:
        table, clocks = _source_for_sid(conn, sid)
        params = [MAX_RAW_BYTES]
        extra = [f'CAST("{key}" AS VARCHAR)' for key, _ in clocks]
        if table == LEDGER:
            extra = [f'CASE WHEN {_bytes_limit("source_json")} THEN source_json END',
                     "source_json IS NULL"]
            params.append(MAX_METADATA_BYTES)
        rows = conn.execute(
            "SELECT snapshot_id, device_name, command, "
            f"CASE WHEN {_bytes_limit('raw_output')} THEN raw_output END, raw_output IS NULL"
            + (", " + ", ".join(extra) if extra else "")
            + f" FROM netops.{table} WHERE "
            + " AND ".join(_exact(key) for key in IDENTITY)
            + " LIMIT 2",
            [*params, sid, device, command],
        ).fetchall()
        if not rows:
            raise ConfigDiffError("raw_not_found", "No raw record for the exact snapshot, device and command.")
        if len(rows) != 1:
            raise ConfigDiffError("ambiguous_record", "Multiple raw records share the exact source identity.")
        actual_sid, actual_device, actual_command, body, null_body, *values = rows[0]
        if (sid, device, command) != (actual_sid, actual_device, actual_command):
            raise ConfigDiffError("source_integrity_error", "Selected raw identity differs from the request.")
        if null_body or (body is not None and not body.strip()):
            raise ConfigDiffError("raw_unavailable", "The selected raw record has no usable body.")
        if body is None:
            raise ConfigDiffError("raw_budget_exceeded", "A raw record exceeds the 256 KiB byte budget.")
        raw_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        source = {"table": "netops." + table, "snapshot_id": actual_sid,
                  "device_name": actual_device, "command": actual_command}
        if table == LEDGER:
            metadata, null_metadata = values
            if metadata is None and not null_metadata:
                raise ConfigDiffError("source_budget_exceeded", "Historical source metadata exceeds 64 KiB.")
            try:
                provenance = json.loads(metadata or "null", object_pairs_hook=_unique_object)
            except (ValueError, RecursionError):
                provenance = None
            if (not isinstance(provenance, dict) or provenance.get("raw_artifact") != source
                    or provenance.get("actual_raw_sha256") != raw_hash):
                raise ConfigDiffError("source_integrity_error", "Historical raw identity or hash could not be verified.")
            scope = "historical_ledger_exact_label"
            collected = _ledger_time(provenance.get("collected_at"))
        else:
            scope = "last_writer_exact_label_partial"
            collected = _read_clock(clocks, values)
        return (body, {**source, "scope": scope, "coverage": "partial" if table == RAW_STORE else "record_only",
                       "collected_at": collected, "raw_sha256": raw_hash})
    except ConfigDiffError as error:
        error.side, error.table = side, table
        raise


def _block(line):
    words = line.split()
    tokens = [word.lower() for word in words]
    if len(tokens) < 2:
        return GLOBAL
    if tokens[0] in ("interface", "router"):
        return tokens[0], line.rstrip()
    if tokens[0] == "route-map" and len(tokens) >= 3 and tokens[2] in ("permit", "deny"):
        return "route_map", " ".join(words[:2])
    if tokens[0] == "access-list":
        return "acl", " ".join(words[:2])
    if tokens[:2] in (["ip", "access-list"], ["ipv6", "access-list"],
                      ["mac", "access-list"], ["arp", "access-list"]):
        return "acl", line.rstrip()
    return GLOBAL


def _config_lines(body):
    lines = body.replace("\r\n", "\n").removesuffix("\n").split("\n")
    if len(lines) > MAX_RAW_LINES:
        raise ConfigDiffError("raw_budget_exceeded", "A raw record exceeds the 4,000 line budget.")
    block = GLOBAL
    stanza = GLOBAL[1]
    preamble = True
    delimiter = None
    for line in lines:
        if delimiter:
            yield ConfigLine(GLOBAL, stanza, line)
            if delimiter in line:
                delimiter = None
            continue
        if preamble:
            if NOISE.fullmatch(line):
                continue
            if line and not line.startswith("!"):
                preamble = False
        if not line.startswith((" ", "\t")):
            block = GLOBAL if not line or line.startswith("!") else _block(line)
            stanza = GLOBAL[1] if not line or line.startswith("!") else line
            banner = BANNER.fullmatch(line)
            if banner:
                opening = banner[1]
                candidate = opening[:2] if re.match(r"\^[A-Z]", opening) else opening[0]
                if candidate not in opening[len(candidate):]:
                    delimiter = candidate
        yield ConfigLine(block, stanza, line)


def _select(lines, selectors, matched):
    if not selectors:
        return lines
    selected = []
    for line in lines:
        kind = line.block[0]
        words = tuple(line.stanza.casefold().split())
        hits = {selector for selector in selectors if selector == (kind,) or selector == words[:len(selector)]}
        if hits:
            selected.append(line)
            matched.update(hits)
    return selected


def _line_range(start, stop):
    size = stop - start
    if size == 1:
        return str(start + 1)
    return f"{start + (size > 0)},{size}"


def _diff_lines(matcher, before, after, context, baseline, target):
    groups = matcher.get_grouped_opcodes(context)
    for index, group in enumerate(groups):
        if not index:
            yield f"--- {baseline}"
            yield f"+++ {target}"
        start_a, stop_a = group[0][1], group[-1][2]
        start_b, stop_b = group[0][3], group[-1][4]
        yield f"@@ -{_line_range(start_a, stop_a)} +{_line_range(start_b, stop_b)} @@"
        for tag, a, b, c, d in group:
            if tag == "equal":
                yield from (" " + line.text for line in before[a:b])
            if tag in ("replace", "delete"):
                yield from ("-" + line.text for line in before[a:b])
            if tag in ("replace", "insert"):
                yield from ("+" + line.text for line in after[c:d])


def _preview(lines, line_limit):
    kept = []
    json_bytes = 2  # Quotes enclosing the diff string.
    total = 0
    for line in lines:
        total += 1
        if total > line_limit or len(kept) < total - 1:
            continue
        size = len(_json(line)) - 2 + (2 if kept else 0)
        if json_bytes + size <= MAX_DIFF_BYTES:
            kept.append(line)
            json_bytes += size
    return "\n".join(kept), {"shown_lines": len(kept), "line_limit": line_limit,
                              "byte_limit": MAX_DIFF_BYTES, "truncated": len(kept) < total}, total


def _changes(matcher, before, after):
    totals = defaultdict(lambda: [0, 0])
    added = removed = 0
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            for line in before[a:b]:
                totals[line.block][1] += 1
            removed += b - a
        if tag in ("replace", "insert"):
            for line in after[c:d]:
                totals[line.block][0] += 1
            added += d - c
    shown = []
    json_bytes = 2
    for (kind, header), (add, remove) in totals.items():
        group = {"kind": kind, "header": header, "added_lines": add, "removed_lines": remove}
        size = len(_json(group)) + bool(shown)
        if len(shown) >= MAX_GROUPS or json_bytes + size > MAX_GROUP_BYTES:
            break
        shown.append(group)
        json_bytes += size
    return added, removed, shown, len(totals)


def _side_lines(body, side, source):
    try:
        return list(_config_lines(body))
    except ConfigDiffError as exc:
        exc.side = side
        exc.table = source["table"].split(".")[1]
        raise


def _compare(request, left, right, version):
    raw_1, source_1 = left
    raw_2, source_2 = right
    baseline, target = source_1["snapshot_id"], source_2["snapshot_id"]
    sections = request["sections"]
    selectors = {tuple(section.casefold().split()) for section in sections}
    matched = set()
    before = _select(_side_lines(raw_1, "baseline", source_1), selectors, matched)
    after = _select(_side_lines(raw_2, "target", source_2), selectors, matched)
    if selectors - matched:
        raise ConfigDiffError("sections_not_found", "A requested section appears in neither compared body.")
    matcher = difflib.SequenceMatcher(
        None, [(line.block, line.text) for line in before], [(line.block, line.text) for line in after],
        autojunk=False,
    )
    added, removed, blocks, block_count = _changes(matcher, before, after)
    shown, preview, total = _preview(
        _diff_lines(matcher, before, after, request["context_lines"], baseline, target),
        400 if request["full"] else 40,
    )
    changed = added + removed
    return {
        "schema": SCHEMA, "status": "success", "diff_status": "observed_changes" if changed else "no_observed_changes",
        "device": source_1["device_name"], "command": source_1["command"],
        "snapshot_id_1": baseline, "snapshot_id_2": target,
        "database_version": version,
        "sources": {"baseline": source_1, "target": source_2},
        "sections": sections, "added_lines": added, "removed_lines": removed, "changed_lines": changed,
        "changed_blocks": blocks, "changed_block_count": block_count, "blocks_truncated": len(blocks) < block_count,
        "full_line_count": total, "preview": preview, "diff": shown, "summary": f"{changed} lines changed",
    }


def _worker(raw, scratch):
    import resource

    validate_native_reader_identity(READER_MODULE)
    deadline = time.monotonic() + DEADLINE_SECONDS
    hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    limit = ADDRESS_SPACE_BYTES if hard == resource.RLIM_INFINITY else min(ADDRESS_SPACE_BYTES, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    duckdb = bootstrap_native_reader(READER_MODULE)

    request = _request(raw)
    try:
        with open_native_snapshot(_project(), scratch, max_bytes=MAX_DB_BYTES, deadline=deadline) as (conn, version):
            left, right = (
                _read_side(conn, request[key], request["device"], request["command"], side)
                for key, side in (("snapshot_id_1", "baseline"), ("snapshot_id_2", "target"))
            )
        result = _compare(request, left, right, version)
        if len(_json(result)) > MAX_RESPONSE_BYTES:
            raise ConfigDiffError("response_budget_exceeded", "Configuration diff exceeded the response byte budget.")
        return result
    except ConfigDiffError as exc:
        error = exc
    except NativeSnapshotError as exc:
        error = ConfigDiffError(exc.code, str(exc))
    except (duckdb.OutOfMemoryException, MemoryError):
        error = ConfigDiffError("memory_budget_exceeded", "Configuration diff exceeded its memory budget.")
    except duckdb.Error:
        error = ConfigDiffError("source_or_query_error", "Native read failed. Check database format, access and writer locks.")
    except OSError:
        error = ConfigDiffError("source_unavailable", "Native database or its directory is missing or unsafe to open.")
    return _failure(error, request)


if __name__ == "__main__":
    try:
        result = _worker(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1), Path(sys.argv[1]))
    except NativeReaderBootstrapError as exc:
        result = _failure(ConfigDiffError(str(exc), "Configuration diff reader initialization was rejected."))
    except ConfigDiffError as exc:
        result = _failure(exc)
    except (ValueError, UnicodeError, RecursionError):
        result = _failure(ConfigDiffError("invalid_request", "Request must contain an unambiguous UTF-8 JSON object."))
    except MemoryError:
        result = _failure(ConfigDiffError("memory_budget_exceeded", "Configuration diff exceeded its memory budget."))
    if owns_native_reader(READER_MODULE):
        close_native_reader()
    sys.stdout.buffer.write(_json(result) + b"\n")
