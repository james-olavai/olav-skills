"""Bounded, resumable export of recorded rows from one native DuckDB project."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

from .native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)
from .native_snapshot import close_native_reader

SCHEMA = "olav.full-export/v1"
READER_MODULE = "olav_skills.bounded_export"
DB = "main.duckdb"
MAX_REQUEST_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_ROW_BYTES = 512 * 1024
MAX_PAGE_ROWS = 10_000
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_ROWS = 1_000_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PARTS = 256
MAX_TIMEOUT_MS = 60_000
MEMORY_MB = 128
ADDRESS_SPACE_BYTES = 768 * 1024 * 1024

SOURCES = {
    "parsed_outputs": {
        "snapshot_id": "VARCHAR", "device_name": "VARCHAR", "command": "VARCHAR",
        "parsed_data": "JSON", "platform": "VARCHAR", "ingested_at": "TIMESTAMP",
    },
    "topology_links": {
        "snapshot_id": "VARCHAR", "link_id": "VARCHAR", "source_device": "VARCHAR",
        "source_interface": "VARCHAR", "destination_device": "VARCHAR", "destination_interface": "VARCHAR",
        "discovery_protocol": "VARCHAR", "link_type": "VARCHAR", "link_status": "VARCHAR",
        "link_speed": "VARCHAR", "first_seen": "TIMESTAMP", "last_seen": "TIMESTAMP",
        "last_verified": "TIMESTAMP", "status_changes": "INTEGER", "platform": "VARCHAR",
    },
}
DEFAULT_COLUMNS = {
    "parsed_outputs": ["device_name", "command", "parsed_data"],
    "topology_links": ["source_device", "source_interface", "destination_device", "destination_interface"],
}
DIGEST = re.compile(r"[0-9a-f]{64}")
EXPORT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
FORMULA_PREFIX = re.compile(r"^[\s\x00-\x20\ufeff\u200b]*[=+\-@＝＋－＠]")
REQUIRED = {"project", "export_id", "source", "snapshot_id"}
OPTIONAL = {"columns", "filters", "format", "max_rows", "max_bytes", "timeout_ms", "receipt"}
MANIFEST = "manifest.json"
PAGE_TEMP = ".page.tmp"
MANIFEST_TEMP = ".manifest.tmp"


class ExportError(Exception):
    pass


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _fingerprint(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _unambiguous_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ExportError("invalid_request")
        obj[key] = value
    return obj


def _request(raw: bytes) -> tuple[dict, dict]:
    if len(raw) > MAX_REQUEST_BYTES:
        raise ExportError("request_byte_limit")
    obj = json.loads(raw, object_pairs_hook=_unambiguous_object)
    if not isinstance(obj, dict) or not REQUIRED <= obj.keys() <= REQUIRED | OPTIONAL:
        raise ExportError("invalid_request")
    project = obj["project"]
    if not isinstance(project, str) or len(project.encode("utf-8")) > 2048 or "\0" in project:
        raise ExportError("invalid_project")
    path = Path(project)
    if (not path.is_absolute() or path == Path("/") or ".." in path.parts
            or str(path) != project or project != os.environ.get("OLAV_HOME")):
        raise ExportError("invalid_project")
    export_id = obj["export_id"]
    if not isinstance(export_id, str) or not EXPORT_ID.fullmatch(export_id):
        raise ExportError("invalid_export_id")
    source = obj["source"]
    if not isinstance(source, str) or source not in SOURCES:
        raise ExportError("unsupported_source")
    sid = obj["snapshot_id"]
    if (not _label(sid) or sid != sid.strip()
            or sid.casefold() in {"latest", "current", "newest"}):
        raise ExportError("invalid_snapshot_id")
    allowed = SOURCES[source]
    columns = obj.get("columns", DEFAULT_COLUMNS[source])
    if (not isinstance(columns, list) or not columns or len(columns) > len(allowed)
            or any(not isinstance(col, str) or col not in allowed for col in columns)
            or len(set(columns)) != len(columns)):
        raise ExportError("invalid_columns")
    filters = obj.get("filters", {})
    if not isinstance(filters, dict) or any(
        name == "snapshot_id" or allowed.get(name) != "VARCHAR" or not _label(value)
        for name, value in filters.items()
    ):
        raise ExportError("invalid_filters")
    fmt = obj.get("format", "jsonl")
    if fmt not in ("jsonl", "csv"):
        raise ExportError("unsupported_format")
    receipt = obj.get("receipt")
    if "receipt" in obj and (not isinstance(receipt, str) or not DIGEST.fullmatch(receipt)):
        raise ExportError("invalid_receipt")
    budget = {}
    for key, default, maximum in (
        ("max_rows", 1000, MAX_PAGE_ROWS), ("max_bytes", 4 * 1024 * 1024, MAX_PAGE_BYTES),
        ("timeout_ms", 15_000, MAX_TIMEOUT_MS),
    ):
        value = obj.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ExportError("invalid_" + key)
        budget[key] = value
    scope = {"project": project, "export_id": export_id, "source": source, "snapshot_id": sid,
             "columns": columns, "filters": dict(sorted(filters.items())), "format": fmt}
    return scope, {**budget, "receipt": receipt}


def _label(value) -> bool:
    return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 256 and "\0" not in value


def _failed(reason: str, checkpoint_preserved: bool | None = True) -> dict:
    return {"schema": SCHEMA, "status": "failed", "error": reason,
            "checkpoint_preserved": checkpoint_preserved, "total_rows": None}


def export(raw: bytes) -> dict:
    """Submit a bounded request; the exporting process can be killed at its deadline."""
    try:
        _, options = _request(raw)
        if sys.platform != "linux":
            return _failed("unsupported_platform")
        with tempfile.TemporaryDirectory(prefix="olav-export-parent-") as parent_scratch:
            child_env = {"OLAV_HOME": os.environ["OLAV_HOME"], "TMPDIR": parent_scratch,
                         "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                         "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                         "OPENBLAS_NUM_THREADS": "1", "TZ": "UTC"}
            outcome = subprocess.run(
                [sys.executable, "-B", "-m", READER_MODULE],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=options["timeout_ms"] / 1000, check=False, env=child_env, cwd=parent_scratch,
            )
        if outcome.returncode or len(outcome.stdout) > MAX_MANIFEST_BYTES:
            return _failed("worker_failed", None)
        return json.loads(outcome.stdout)
    except subprocess.TimeoutExpired:
        return _failed("deadline_exceeded", None)
    except ExportError as exc:
        return _failed(str(exc))
    except (ValueError, UnicodeError, RecursionError):
        return _failed("invalid_request")
    except OSError:
        return _failed("worker_failed", None)


def _open_dir(name, parent=None):
    return os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _directory(stack: ExitStack, name, parent=None):
    fd = _open_dir(name, parent)
    stack.callback(os.close, fd)
    return fd


def _owned_directory(fd):
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ExportError("unsafe_output_directory")


def _mkdir(parent, name, *, existing_ok=False):
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        if not existing_ok:
            raise ExportError("export_id_exists") from None
    else:
        os.fsync(parent)


def _open_regular(parent, name, *, owner=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or owner and (info.st_uid != os.getuid() or info.st_mode & 0o022)):
        os.close(fd)
        raise ExportError("unsafe_file")
    return os.fdopen(fd, "rb")


def _unlink_recovery_file(parent, name):
    try:
        with _open_regular(parent, name, owner=True):
            pass
        os.unlink(name, dir_fd=parent)
    except FileNotFoundError:
        pass


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def _unchanged(parent, name, stream, identity):
    actual_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return _identity(actual_path) == identity == _identity(os.fstat(stream.fileno()))


def _reject_wal(parent):
    try:
        os.stat(DB + ".wal", dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ExportError("source_has_wal")


def _file_digest(stream, max_size):
    size = 0
    digest = hashlib.sha256()
    while chunk := stream.read(min(64 * 1024, max_size - size + 1)):
        size += len(chunk)
        if size > max_size:
            raise ExportError("file_byte_limit")
        digest.update(chunk)
    return size, digest.hexdigest()


def _verify_source(parent, source, identity):
    _reject_wal(parent)
    if not _unchanged(parent, DB, source, identity):
        raise ExportError("source_changed")


@contextmanager
def _connect(parent, source, duckdb, project, deadline):
    from .native_snapshot import NativeSnapshotError, open_native_snapshot
    try:
        with tempfile.TemporaryDirectory(prefix="export-snapshot-") as scratch:
            with open_native_snapshot(Path(project), Path(scratch), max_bytes=MAX_SOURCE_BYTES,
                                      deadline=deadline, memory_limit=f"{MEMORY_MB}MiB", threads=1) as (conn, actual):
                identity = _identity(os.fstat(source.fileno()))
                original = os.fstat(source.fileno())
                expected = actual["source_identity"]
                if ((original.st_dev, original.st_ino, original.st_mtime_ns, original.st_ctime_ns)
                        != (expected["device"],expected["inode"],expected["mtime_ns"],expected["ctime_ns"])):
                    raise ExportError("source_changed")
                _verify_source(parent,source,identity)
                yield conn, {"path":actual["path"],"bytes":actual["bytes"],"sha256":actual["sha256"],
                             "duckdb_version":actual["duckdb_version"],"snapshot_backend":actual["backend"],
                             "exporter_sha256":_fingerprint(Path(__file__).read_bytes())}, identity
    except NativeSnapshotError as exc:
        mapping={"source_has_wal":"source_has_wal","source_budget_exceeded":"source_byte_limit"}
        raise ExportError(mapping.get(exc.code,exc.code)) from None


def _read_page(conn, scope, offset, row_limit):
    table = scope["source"]
    required = sorted(set(scope["columns"]) | set(scope["filters"]) | {"snapshot_id"})
    placeholders = ", ".join("?" for _ in required)
    metadata = conn.execute(
        "SELECT column_name, data_type FROM system.main.duckdb_columns() "
        "WHERE database_name = system.main.current_database() AND schema_name = 'netops' AND table_name = ? "
        "AND EXISTS (SELECT 1 FROM system.main.duckdb_tables() "
        "WHERE database_name = system.main.current_database() AND schema_name = 'netops' AND table_name = ? "
        "AND NOT internal AND NOT temporary) "
        f"AND column_name IN ({placeholders})",
        [table, table, *required],
    ).fetchall()
    column_types = dict(metadata)
    if any(column_types.get(col) != SOURCES[table][col] for col in required):
        raise ExportError("source_schema_unavailable")
    # Render full rows before sorting. Equal sort keys are indistinguishable
    # output records, so OFFSET cannot drop a distinct value behind a sort tie.
    fields = ", ".join(f'CAST("{col}" AS VARCHAR)' for col in scope["columns"])
    keys = ["snapshot_id", *scope["filters"]]
    where = " AND ".join(f'CAST("{col}" AS VARCHAR) COLLATE "binary" = ?' for col in keys)
    sql = f"""
        WITH records AS (
            SELECT CAST(system.main.to_json(system.main.list_value({fields})) AS VARCHAR)
                COLLATE "binary" AS rendered
            FROM netops."{table}" WHERE {where}
        )
        SELECT CASE WHEN system.main.octet_length(system.main.encode(rendered)) <= ?
                    THEN rendered END
        FROM records ORDER BY rendered LIMIT ? OFFSET ?
    """
    params = [scope["snapshot_id"], *scope["filters"].values(), MAX_ROW_BYTES, row_limit + 1, offset]
    return conn.execute(sql, params)


def _formula_safe(value):
    if value is None:
        return ""
    if value.startswith(("\t", "\r", "\n")) or FORMULA_PREFIX.match(value):
        return "'" + value
    return value


def _csv(values):
    sink = io.StringIO(newline="")
    csv.writer(sink, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerow(_formula_safe(val) for val in values)
    return sink.getvalue().encode("utf-8")


@contextmanager
def _new_file(parent, name):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, mode=0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def _write_page(job, cursor, scope, row_limit, byte_limit):
    count = size = 0
    digest = hashlib.sha256()
    reason = None
    with _new_file(job, PAGE_TEMP) as stream:
        def write(payload):
            nonlocal size
            size += len(payload)
            stream.write(payload)
            digest.update(payload)

        if scope["format"] == "csv":
            header = _csv(scope["columns"])
            if len(header) > byte_limit:
                raise ExportError("header_byte_limit")
            write(header)
        while (row := cursor.fetchone()) is not None:
            if count == row_limit:
                reason = "row_limit"
                break
            if row[0] is None:
                reason = "row_byte_limit"
                break
            values = json.loads(row[0])
            payload = (_csv(values) if scope["format"] == "csv"
                       else _json(dict(zip(scope["columns"], values))) + b"\n")
            if len(payload) > MAX_ROW_BYTES:
                reason = "row_byte_limit"
                break
            if size + len(payload) > byte_limit:
                reason = "byte_limit"
                break
            write(payload)
            count += 1
        if reason and count == 0:
            raise ExportError(reason)
    return {"rows": count, "bytes": size, "sha256": digest.hexdigest()}, reason


def _part_name(index, scope):
    return f"part-{index:04d}.{scope['format']}"


def _part_rows(stream, scope):
    stream.seek(0)
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="strict", newline="")
    try:
        if scope["format"] == "csv":
            csv.field_size_limit(MAX_ROW_BYTES)
            reader = csv.reader(text, strict=True)
            if next(reader, None) != scope["columns"]:
                raise ExportError("checkpoint_invalid")
            return sum(_validate_record(row, scope) for row in reader)
        return sum(_validate_record(json.loads(line), scope) for line in text)
    finally:
        text.detach()


def _validate_record(row, scope):
    if scope["format"] == "csv":
        valid = len(row) == len(scope["columns"])
    else:
        valid = (isinstance(row, dict) and row.keys() == set(scope["columns"])
                 and all(val is None or isinstance(val, str) for val in row.values()))
    if not valid:
        raise ExportError("checkpoint_invalid")
    return 1


def _read_checkpoint(job, scope, receipt):
    try:
        with _open_regular(job, MANIFEST, owner=True) as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES or _fingerprint(payload) != receipt:
            raise ExportError("checkpoint_receipt_mismatch")
        saved = json.loads(payload, object_pairs_hook=_unambiguous_object)
        if (saved["schema"] != SCHEMA or saved["scope"] != scope
                or saved["request_sha256"] != _fingerprint(_json(scope))):
            raise ExportError("checkpoint_scope_mismatch")
        if saved["status"] != "partial":
            raise ExportError("checkpoint_not_partial")
        parts = saved["parts"]
        if not isinstance(parts, list) or not 0 < len(parts) < MAX_PARTS:
            raise ExportError("checkpoint_invalid")
        rows = size = 0
        for index, part in enumerate(parts, 1):
            if (part["file"] != _part_name(index, scope) or type(part["bytes"]) is not int
                    or not 0 < part["bytes"] <= MAX_PAGE_BYTES or type(part["rows"]) is not int
                    or not 0 < part["rows"] <= MAX_PAGE_ROWS):
                raise ExportError("checkpoint_invalid")
            rows += part["rows"]
            size += part["bytes"]
            if rows >= MAX_TOTAL_ROWS or size >= MAX_TOTAL_BYTES:
                raise ExportError("checkpoint_invalid")
            with _open_regular(job, part["file"], owner=True) as stream:
                identity = _identity(os.fstat(stream.fileno()))
                actual_bytes, digest = _file_digest(stream, part["bytes"])
                if (actual_bytes != part["bytes"] or digest != part["sha256"]
                        or part["rows"] != _part_rows(stream, scope)
                        or not _unchanged(job, part["file"], stream, identity)):
                    raise ExportError("checkpoint_part_mismatch")
        if rows != saved["rows_written"] or size != saved["bytes_written"]:
            raise ExportError("checkpoint_invalid")
        return saved, rows, size
    except (KeyError, TypeError, ValueError, OSError, csv.Error):
        raise ExportError("checkpoint_invalid") from None


def _persist(job, scope, manifest, new_part=None):
    payload = _json(manifest) + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ExportError("manifest_byte_limit")
    if new_part:
        os.rename(PAGE_TEMP, new_part, src_dir_fd=job, dst_dir_fd=job)
        os.fsync(job)
    with _new_file(job, MANIFEST_TEMP) as stream:
        stream.write(payload)
    os.replace(MANIFEST_TEMP, MANIFEST, src_dir_fd=job, dst_dir_fd=job)
    os.fsync(job)
    result = {"schema": SCHEMA, "status": manifest["status"], "reason": manifest.get("reason"),
              "total_rows": manifest.get("total_rows"), "rows_written": manifest.get("rows_written", 0),
              "bytes_written": manifest.get("bytes_written", 0), "receipt": _fingerprint(payload),
              "manifest": f".olav/exports/{scope['export_id']}/{MANIFEST}"}
    if new_part:
        result["part"] = new_part
    return result


def _perform(job, db_parent, duckdb, scope, options, saved, rows, size):
    parts = saved["parts"] if saved else []
    part_name = _part_name(len(parts) + 1, scope)
    with _open_regular(db_parent, DB) as source, _connect(db_parent, source, duckdb, scope["project"], time.monotonic()+options["timeout_ms"]/1000) as (conn, version, identity):
        if saved and saved["source_version"] != version:
            raise ExportError("source_changed")
        for name in (PAGE_TEMP, MANIFEST_TEMP, part_name):
            _unlink_recovery_file(job, name)
        row_limit = min(options["max_rows"], MAX_TOTAL_ROWS - rows)
        byte_limit = min(options["max_bytes"], MAX_TOTAL_BYTES - size)
        cursor = _read_page(conn, scope, rows, row_limit)
        part, reason = _write_page(job, cursor, scope, row_limit, byte_limit)
        _verify_source(db_parent, source, identity)
        rows += part["rows"]
        size += part["bytes"]
        if reason and (rows == MAX_TOTAL_ROWS or size == MAX_TOTAL_BYTES or len(parts) + 1 == MAX_PARTS
                       or (reason == "byte_limit" and byte_limit < options["max_bytes"])):
            raise ExportError("total_export_limit")
        if reason == "row_byte_limit":
            raise ExportError(reason)
        manifest = {
            "schema": SCHEMA, "status": "partial" if reason else "complete", "reason": reason,
            "scope": scope, "source_version": version, "request_sha256": _fingerprint(_json(scope)),
            "encoding": {"charset": "utf-8", "scalar": "duckdb_varchar",
                         "column_types": [SOURCES[scope["source"]][col] for col in scope["columns"]]},
            "parts": [*parts, {"file": part_name, **part}], "rows_written": rows,
            "bytes_written": size, "total_rows": None if reason else rows,
        }
    # Do not publish success before the snapshot context validates on exit.
    return _persist(job, scope, manifest, part_name)


def _job(stack, exports, scope, receipt):
    import fcntl

    if not receipt:
        _mkdir(exports, scope["export_id"])
    job = _directory(stack, scope["export_id"], exports)
    _owned_directory(job)
    try:
        fcntl.flock(job, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ExportError("export_busy") from None
    return job


def _worker(raw):
    import resource

    validate_native_reader_identity(READER_MODULE)
    hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    limit = ADDRESS_SPACE_BYTES if hard == resource.RLIM_INFINITY else min(ADDRESS_SPACE_BYTES, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    duckdb = bootstrap_native_reader(READER_MODULE)

    scope, options = _request(raw)
    receipt = options["receipt"]
    with ExitStack() as stack:
        project = _directory(stack, "/")
        for name in Path(scope["project"]).parts[1:]:
            project = _directory(stack, name, project)
        olav = _directory(stack, ".olav", project)
        db_parent = _directory(stack, "databases", olav)
        if not receipt:
            _mkdir(olav, "exports", existing_ok=True)
        exports = _directory(stack, "exports", olav)
        _owned_directory(exports)
        job = _job(stack, exports, scope, receipt)
        saved, rows, size = _read_checkpoint(job, scope, receipt) if receipt else (None, 0, 0)
        try:
            return _perform(job, db_parent, duckdb, scope, options, saved, rows, size)
        except (duckdb.OutOfMemoryException, MemoryError):
            reason = "memory_limit"
        except duckdb.Error:
            reason = "source_or_query_error"
        except ExportError as exc:
            reason = str(exc)
        except OSError:
            # An I/O failure during commit leaves publication status uncertain.
            return _failed("file_unavailable", None)
        _unlink_recovery_file(job, PAGE_TEMP)
        if saved:
            return _failed(reason)
        manifest = {"schema": SCHEMA, "status": "failed", "reason": reason, "scope": scope,
                    "request_sha256": _fingerprint(_json(scope)), "parts": [], "total_rows": None}
        return _persist(job, scope, manifest)


if __name__ == "__main__":
    try:
        result = _worker(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    except (ExportError, NativeReaderBootstrapError) as exc:
        result = _failed(str(exc))
    except (ValueError, UnicodeError, RecursionError):
        result = _failed("invalid_request")
    except OSError:
        result = _failed("file_unavailable")
    if owns_native_reader(READER_MODULE):
        close_native_reader()
    print(_json(result).decode("utf-8"))
