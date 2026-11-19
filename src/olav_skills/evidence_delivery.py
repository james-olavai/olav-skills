"""Build and inspect a bounded offline artifact without importing data code."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import sys
import zipfile

from . import __version__

SCHEMA = "olav.evidence-bundle/v1"
RESULT_SCHEMA = "olav.evidence-bundle-result/v1"
SOURCE_DIR = ("exports", "evidence_sources")
BUNDLE_DIR = ("exports", "evidence_bundles")
MANIFEST = "manifest.json"
MAX_REQUEST = 16 * 1024
MAX_FILES = 16
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * MAX_FILE
MAX_MANIFEST = 16 * 1024
MAX_ARCHIVE = MAX_TOTAL + MAX_MANIFEST + 8 * 1024
MAX_CENTRAL_DIRECTORY = 4 * 1024
MAX_LINES = 20_000
MAX_JSON_NODES = 30_000
MAX_SECONDS = 10
SEGMENT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9a-zA-Z][0-9a-zA-Z.+!-]{0,63}\Z")
SENSITIVE_NAME = re.compile(
    r"(?:^|[_.-])(?:private|secret[s]?|credential[s]?|password[s]?|token[s]?|"
    r"deploy(?:ment)?|tenant[s]?|id_rsa|id_ed25519|env)(?:[_.-]|$)",
    re.IGNORECASE,
)
SECRET_FIELD = (
    r"(?:pass(?:word|wd)|pwd|(?:api|access|private|secret|client)[_ -]?key|"
    r"client[_ -]?secret|(?:access|refresh|auth|bearer)[_ -]?token|token|"
    r"credential[s]?|community[_ -]?string)"
)
SECRET_KEY = re.compile(rf"{SECRET_FIELD}\Z", re.IGNORECASE)
SECRET_CONTENT = re.compile(
    rf"""(?im)(?:\b{SECRET_FIELD}\s*["']?\s*[:=]\s*\S|"""
    r"\bauthorization\s*[:=]\s*(?:basic|bearer)\s+\S|"
    r"^[ \t]*(?:enable\s+(?:secret|password)|snmp-server\s+community|"
    r"username\s+\S+\s+(?:secret|password))\s+\S|"
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----|"
    r"\b(?:sk-(?:proj-)?[a-zA-Z0-9_-]{16,}|gh[pousr]_[a-zA-Z0-9]{20,}|"
    r"xox[baprs]-[a-zA-Z0-9-]{10,}|AKIA[A-Z0-9]{16})\b|"
    r"://[^/\s:@]+:[^/\s@]+@)"
)
SECRET_COLUMN = re.compile(
    rf'(?:^|[,\n])[ \t]*"?{SECRET_FIELD}"?[ \t]*(?=[,\n]|$)', re.IGNORECASE
)
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
SUFFIXES = {".json", ".csv", ".md"}
TRUNCATED_FLAGS = {
    "truncated", "byte_truncated", "rows_truncated", "sample_truncated",
    "excerpt_truncated", "cell_truncated", "has_more", "partial",
}
EOCD = struct.Struct("<4s4H2IH")


class BundleError(ValueError):
    """A stable public error code that never echoes an unsafe path or content."""


class DeadlineExceeded(Exception):
    pass


def _segment(name):
    return (
        SEGMENT.fullmatch(name) is not None
        and not name.endswith(".")
        and SENSITIVE_NAME.search(name) is None
        and SECRET_CONTENT.search(name) is None
    )


def _source_paths(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_FILES:
        raise BundleError("source_count_out_of_bounds")
    paths = []
    for filename in value:
        if not isinstance(filename, str) or len(filename) > 384:
            raise BundleError("invalid_source_path")
        parts = tuple(filename.split("/"))
        if (not 3 <= len(parts) <= 8 or parts[:2] != SOURCE_DIR
                or not all(_segment(part) for part in parts)
                or Path(parts[-1]).suffix.lower() not in SUFFIXES):
            raise BundleError("invalid_source_path")
        paths.append(filename)
    if len({path.lower() for path in paths}) != len(paths):
        raise BundleError("duplicate_source_path")
    return sorted(paths)


@contextmanager
def _directories(original, parts):
    """Walk from an open parent, refusing symlinks even during a path swap."""
    descriptor = os.dup(original)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _project(raw):
    if (not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//")
            or len(raw) > 2048 or "\x00" in raw or ".." in raw.split("/")):
        raise BundleError("absolute_project_required")
    parts = Path(raw).parts[1:]
    if not parts:
        raise BundleError("absolute_project_required")
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise BundleError("unsupported_filesystem")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with _directories(descriptor, parts) as project:
            yield project
    finally:
        os.close(descriptor)


def _file_stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(parent, name, cap, *, expected_size=None):
    initial = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise BundleError("unsafe_file_type")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as stream:
        if _file_stamp(os.fstat(stream.fileno())) != _file_stamp(initial):
            raise BundleError("file_changed_during_read")
        if expected_size is not None and initial.st_size != expected_size:
            return None
        if initial.st_size > cap:
            raise BundleError("file_byte_budget_exceeded")
        data = stream.read(cap + 1)
        if len(data) > cap:
            raise BundleError("file_byte_budget_exceeded")
        if (len(data) != initial.st_size
                or _file_stamp(os.fstat(stream.fileno())) != _file_stamp(initial)
                or _file_stamp(os.stat(name, dir_fd=parent, follow_symlinks=False))
                != _file_stamp(initial)):
            raise BundleError("file_changed_during_read")
        return data


def _read_source(project, filename, cap, *, expected_size=None):
    parts = filename.split("/")
    with _directories(project, parts[:-1]) as parent:
        return _read_file(parent, parts[-1], cap, expected_size=expected_size)


def _json_object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise BundleError("duplicate_json_key")
    return obj


def _json_constant(_value):
    raise BundleError("invalid_json_number")


def _load_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object,
                          parse_constant=_json_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, BundleError):
            raise
        raise BundleError("invalid_json") from exc


def _json_quality(value):
    pending = [value]
    remaining = MAX_JSON_NODES
    truncated = False
    while pending:
        remaining -= 1
        if remaining < 0:
            raise BundleError("json_node_budget_exceeded")
        item = pending.pop()
        if isinstance(item, dict):
            if any(SECRET_KEY.fullmatch(key) or SECRET_CONTENT.search(key) for key in item):
                raise BundleError("sensitive_content_refused")
            for key, val in item.items():
                if (key.lower() in TRUNCATED_FLAGS and val is True
                        or key == "total_is_complete" and val is False
                        or key in {"excerpt_truncated_rows", "rows_omitted_by_char_budget"}
                        and type(val) is int and val > 0
                        or key == "body_scope" and val == "bounded_excerpt"):
                    truncated = True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and SECRET_CONTENT.search(item):
            raise BundleError("sensitive_content_refused")
    return "partial_marker_present" if truncated else "unknown"


def _source_record(filename, data, ordinal):
    try:
        body = data.decode("utf-8")
    except UnicodeError as exc:
        raise BundleError("source_is_not_utf8") from exc
    suffix = Path(filename).suffix.lower()
    if CONTROL_CHARACTER.search(body):
        raise BundleError("source_contains_control_characters")
    if body.count("\n") + 1 > MAX_LINES:
        raise BundleError("source_line_budget_exceeded")
    if SECRET_CONTENT.search(body) or (suffix == ".csv" and SECRET_COLUMN.search(body)):
        raise BundleError("sensitive_content_refused")
    quality = _json_quality(_load_json(data)) if suffix == ".json" else "unknown"
    return {
        "project_relative_path": filename,
        "entry": f"files/{ordinal:02}{suffix}",
        "sha256": _sha256(data),
        "bytes": len(data),
        "coverage_marker": quality,
    }


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _manifest(records, version=__version__):
    return {
        "schema": SCHEMA,
        "generator": {"distribution": "olav-skills", "version": version},
        "source_scope": "selected_project_files",
        "source_claims": "unverified",
        "collection_time": {"status": "unknown", "value": None},
        "coverage": "partial",
        "files": records,
    }


def _encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def _pack(manifest, files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", allowZip64=False) as archive:
        for name, data in ((MANIFEST, manifest), *files):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(entry, data)
    return stream.getvalue()


def _publish(parent, name, data):
    temporary = ".evidence-" + secrets.token_hex(16)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # An exclusive link commits the fully written file without replacing
        # any existing entry. Both names are on the same filesystem.
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
    except FileExistsError as exc:
        raise BundleError("bundle_already_exists") from exc
    finally:
        os.unlink(temporary, dir_fd=parent)


def _create(project, parent, name, paths):
    used = 0
    records, files = [], []
    for ordinal, filename in enumerate(paths, 1):
        data = _read_source(project, filename, min(MAX_FILE, MAX_TOTAL - used))
        record = _source_record(filename, data, ordinal)
        records.append(record)
        files.append((record["entry"], data))
        used += len(data)
    manifest = _manifest(records)
    manifest_bytes = _encode(manifest)
    if len(manifest_bytes) > MAX_MANIFEST:
        raise BundleError("manifest_byte_budget_exceeded")
    archive = _pack(manifest_bytes, files)
    if len(archive) > MAX_ARCHIVE:
        raise BundleError("archive_byte_budget_exceeded")
    _publish(parent, name, archive)
    return manifest, _sha256(manifest_bytes)


def _check_zip_directory(raw):
    if len(raw) < EOCD.size:
        raise BundleError("invalid_archive")
    signature, disk, directory_disk, disk_entries, entries, size, offset, comment = EOCD.unpack(
        raw[-EOCD.size:]
    )
    if (signature != b"PK\x05\x06" or disk or directory_disk or comment
            or disk_entries != entries or not 2 <= entries <= MAX_FILES + 1
            or not 0 < size <= MAX_CENTRAL_DIRECTORY
            or offset + size != len(raw) - EOCD.size):
        raise BundleError("invalid_archive")


def _stored_size(entry, limit):
    return (entry.compress_type == zipfile.ZIP_STORED and entry.flag_bits == 0
            and entry.file_size == entry.compress_size and 0 <= entry.file_size <= limit)


def _read_member(archive, entry):
    with archive.open(entry) as stream:
        data = stream.read(entry.file_size + 1)
    if len(data) != entry.file_size:
        raise BundleError("archive_entry_size_mismatch")
    return data


def _verify(raw, expected_digest):
    _check_zip_directory(raw)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if entries[0].filename != MANIFEST or not _stored_size(entries[0], MAX_MANIFEST):
                raise BundleError("invalid_manifest_entry")
            manifest_bytes = _read_member(archive, entries[0])
            digest = _sha256(manifest_bytes)
            if expected_digest is not None and expected_digest != digest:
                raise BundleError("manifest_digest_mismatch")
            manifest = _load_json(manifest_bytes)
            if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
                raise BundleError("unsupported_manifest_schema")
            generator = manifest.get("generator")
            version = generator.get("version") if isinstance(generator, dict) else None
            if not isinstance(version, str) or not VERSION.fullmatch(version):
                raise BundleError("invalid_manifest_generator")
            declared = manifest.get("files")
            if not isinstance(declared, list) or not all(isinstance(row, dict) for row in declared):
                raise BundleError("invalid_manifest_files")
            paths = _source_paths([row.get("project_relative_path") for row in declared])
            if len(entries) != len(paths) + 1:
                raise BundleError("archive_entry_set_mismatch")
            used = 0
            for ordinal, (filename, entry) in enumerate(zip(paths, entries[1:]), 1):
                entry_name = f"files/{ordinal:02}{Path(filename).suffix.lower()}"
                if entry.filename != entry_name or not _stored_size(entry, min(MAX_FILE, MAX_TOTAL - used)):
                    raise BundleError("invalid_payload_entry")
                used += entry.file_size
            verified, files = [], []
            for ordinal, (filename, row, entry) in enumerate(zip(paths, declared, entries[1:]), 1):
                data = _read_member(archive, entry)
                record = _source_record(filename, data, ordinal)
                if row.get("sha256") != record["sha256"]:
                    raise BundleError("payload_digest_mismatch")
                verified.append(record)
                files.append((entry.filename, data))
            if _encode(_manifest(verified, version)) != manifest_bytes:
                raise BundleError("manifest_metadata_mismatch")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, IndexError) as exc:
        raise BundleError("invalid_archive") from exc
    if _pack(manifest_bytes, files) != raw:
        raise BundleError("archive_encoding_mismatch")
    return manifest, digest


def _recheck(project, manifest):
    results = []
    for row in manifest["files"]:
        path = row["project_relative_path"]
        try:
            data = _read_source(project, path, row["bytes"], expected_size=row["bytes"])
        except FileNotFoundError:
            state = "missing"
        except (OSError, BundleError):
            state = "unavailable"
        else:
            state = "unchanged" if data is not None and _sha256(data) == row["sha256"] else "changed"
        results.append({"project_relative_path": path, "state": state})
    states = {result["state"] for result in results}
    if states & {"changed", "missing"}:
        return "stale", results
    return ("unknown" if "unavailable" in states else "unchanged"), results


def _result(operation, **fields):
    return {
        "schema": RESULT_SCHEMA,
        "operation": operation,
        "status": "success",
        "integrity": "not_checked",
        "freshness": "not_checked",
        **fields,
    }


def run(request):
    """Execute a single operation. Only caller-approved paths authorize recheck."""
    if not isinstance(request, dict):
        raise BundleError("invalid_request")
    operation = request.get("operation")
    if operation not in ("create", "verify", "recheck"):
        raise BundleError("invalid_operation")
    allowed = {"operation", "project", "bundle"}
    if operation != "verify":
        allowed.add("sources")
    if operation != "create":
        allowed.add("expected_manifest_sha256")
    if set(request) - allowed:
        raise BundleError("unexpected_request_field")
    name = request.get("bundle")
    if not isinstance(name, str) or not _segment(name) or not name.endswith(".zip"):
        raise BundleError("invalid_bundle_name")
    paths = _source_paths(request.get("sources")) if operation != "verify" else None
    expected = request.get("expected_manifest_sha256")
    if "expected_manifest_sha256" in request and (
        not isinstance(expected, str) or HASH.fullmatch(expected) is None
    ):
        raise BundleError("invalid_expected_manifest_digest")
    with _project(request.get("project")) as project, _directories(project, BUNDLE_DIR) as parent:
        if operation == "create":
            manifest, digest = _create(project, parent, name, paths)
            return _result(operation, bundle="/".join((*BUNDLE_DIR, name)),
                           manifest=manifest, manifest_sha256=digest)
        try:
            raw = _read_file(parent, name, MAX_ARCHIVE)
            manifest, digest = _verify(raw, expected)
        except (OSError, BundleError) as exc:
            code = str(exc) if isinstance(exc, BundleError) else "bundle_unavailable"
            return _result(operation, status="error", integrity="failed", error_type=code)
        result = _result(operation, integrity="verified",
                         manifest_pin="matched" if expected else "not_supplied",
                         manifest=manifest, manifest_sha256=digest)
        if operation == "recheck":
            if paths != [row["project_relative_path"] for row in manifest["files"]]:
                result.update(status="error", error_type="source_scope_mismatch")
            else:
                result["freshness"], result["sources"] = _recheck(project, manifest)
        return result


def _expired(_signal, _frame):
    raise DeadlineExceeded


def main():
    operation = None
    if not hasattr(signal, "setitimer"):
        result = _result(operation, status="error", error_type="unsupported_filesystem")
    else:
        previous = signal.signal(signal.SIGALRM, _expired)
        signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
        try:
            raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                raise BundleError("request_byte_budget_exceeded")
            request = _load_json(raw)
            if isinstance(request, dict) and request.get("operation") in ("create", "verify", "recheck"):
                operation = request["operation"]
            result = run(request)
        except BundleError as exc:
            result = _result(operation, status="error", error_type=str(exc))
        except (OSError, UnicodeError):
            result = _result(operation, status="error", error_type="project_or_file_unavailable")
        except DeadlineExceeded:
            result = _result(operation, status="error", freshness="unknown", error_type="time_budget_exceeded")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "success" else 1
