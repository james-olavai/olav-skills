"""Versioned parsers and bounded offline replay in an explicit native project."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any
from uuid import uuid4

from .native_reader_bootstrap import (
    NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader, validate_native_reader_identity,
)

REGISTRY = "parser-lifecycle"
EXPORT_DIR = "derived-reparses"
VERSION_SCHEMA = "olav.parser-version/v1"
RESULT_SCHEMA = "olav.parser-management/v1"
EXPORT_SCHEMA = "olav.offline-reparse/v1"
SOURCE_READER_MODULE = "olav_skills.parser_lifecycle"
MAX_SOURCE = 32 * 1024
MAX_SAMPLE = 256 * 1024
MAX_TOTAL_RAW = 4 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_REQUEST = 8 * 1024 * 1024
MAX_SLOT = 2 * 1024 * 1024
MAX_VERSIONS = 16
MAX_SAMPLES = 16
MAX_RECORDS = 128
MAX_ROWS = 2000
MAX_TOTAL_ROWS = 20000
WORKER_TIMEOUT = 12
SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SLOT_NAME = re.compile(r"[a-f0-9]{64}\.json\Z")
FULL_SHA = re.compile(r"[a-f0-9]{64}\Z")


class LifecycleError(ValueError):
    """Bounded, operator-facing lifecycle or replay error."""


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _json_load(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise LifecycleError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_number(_value):
        raise LifecycleError("non-finite JSON number")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_number)


def _text(value, name, limit, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LifecycleError(f"{name} must be a nonempty string" if not empty else f"{name} must be a string")
    if "\x00" in value or len(value.encode("utf-8")) > limit:
        raise LifecycleError(f"{name} exceeds its byte limit or contains a NUL")
    return value


def _project(project: str) -> Path:
    path = Path(_text(project, "project", 2048))
    if path.anchor != "/" or ".." in path.parts:
        raise LifecycleError("project must be an explicit absolute path without traversal")
    with _directory(path):
        return path


def _scope(platform, command) -> tuple[str, str]:
    from .netops.core.platform_canonical import canonicalize_platform

    platform = canonicalize_platform(_text(platform, "platform", 64))
    if not platform or not re.fullmatch(r"[a-z0-9][a-z0-9_]*", platform):
        raise LifecycleError("unsupported platform name")
    command = _text(command, "command", 256)
    if not command.isprintable():
        raise LifecycleError("command must contain printable characters")
    return platform, " ".join(command.lower().split())


def _slot_name(scope: tuple[str, str]) -> str:
    return _digest(_json(scope)) + ".json"


@contextmanager
def _directory(path: Path, *, create=False):
    """Open each directory without following links, including during writes."""
    if path.anchor != "/" or ".." in path.parts:
        raise LifecycleError("directory must be an absolute path without traversal")
    if not hasattr(os, "O_NOFOLLOW"):
        raise LifecycleError("safe no-follow filesystem operations are unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise LifecycleError("unsafe or inaccessible directory component") from exc
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _read_file(directory: int, name: str, limit: int) -> bytes:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise LifecycleError("unsafe or inaccessible file") from exc
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise LifecycleError("file must be a bounded regular file without extra hard links")
        content = file.read(limit + 1)
    if len(content) > limit:
        raise LifecycleError("file exceeds byte budget")
    return content


def _write_file(directory: int, name: str, content: bytes, *, replace: bool):
    temp_name = ".pending-" + uuid4().hex
    fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if replace:
            os.replace(temp_name, name, src_dir_fd=directory, dst_dir_fd=directory)
        else:
            try:
                os.link(temp_name, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError as exc:
                raise LifecycleError("output already exists") from exc
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory)
        except FileNotFoundError:
            pass
    os.fsync(directory)


@contextmanager
def _lock(directory: int):
    import fcntl

    fd = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                 dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LifecycleError("unsafe parser registry lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleError("parser registry is busy") from exc
        yield
    finally:
        os.close(fd)


def _blank_slot(scope):
    return {"schema": VERSION_SCHEMA, "platform": scope[0], "command": scope[1],
            "versions": {}, "active": None, "previous": None, "selection_revision": None}


def _read_slot(directory, scope):
    try:
        content = _read_file(directory, _slot_name(scope), MAX_SLOT)
    except FileNotFoundError:
        return _blank_slot(scope)
    try:
        result = _json_load(content)
        if (
            not isinstance(result, dict) or set(result) != set(_blank_slot(scope))
            or result["schema"] != VERSION_SCHEMA
            or (result["platform"], result["command"]) != scope
            or not isinstance(result["versions"], dict) or len(result["versions"]) > MAX_VERSIONS
            or any(value is not None and (not isinstance(value, str) or value not in result["versions"])
                   for value in (result["active"], result["previous"]))
            or (result["selection_revision"] is not None
                and (not isinstance(result["selection_revision"], str)
                     or not re.fullmatch(r"[a-f0-9]{32}", result["selection_revision"])))
            or (result["selection_revision"] is None
                and (result["active"] is not None or result["previous"] is not None))
        ):
            raise LifecycleError("invalid parser slot")
        return result
    except (ValueError, RecursionError) as exc:
        raise LifecycleError("invalid parser slot") from exc


def _load_slot(agent_dir, scope):
    try:
        with _directory(agent_dir / REGISTRY) as directory:
            return _read_slot(directory, scope)
    except FileNotFoundError:
        return _blank_slot(scope)


def _base_candidate(scope, version, dsl, source, training):
    return {"version": version, "dsl": dsl, "source": source,
            "source_sha256": _digest(source.encode("utf-8")), "training_sha256": training,
            "scope": list(scope)}


def _sha(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA.fullmatch(value) is not None


def _candidate(slot, version, *, validated=False):
    if version not in slot["versions"]:
        raise LifecycleError("unknown parser version")
    candidate = slot["versions"][version]
    try:
        base = _base_candidate((slot["platform"], slot["command"]), version, candidate["dsl"],
                               _text(candidate["source"], "source", MAX_SOURCE),
                               candidate["training_sha256"])
        training = base["training_sha256"]
        if (
            set(candidate) != set(base) | {"validation"}
            or base["dsl"] not in {"python", "textfsm"}
            or not isinstance(training, list) or not 1 <= len(training) <= MAX_SAMPLES
            or not all(_sha(item) for item in training) or len(set(training)) != len(training)
            or any(value != candidate[key] for key, value in base.items())
        ):
            raise LifecycleError("parser candidate identity or source hash mismatch")
        evidence = candidate["validation"]
        if evidence is not None:
            _check_evidence(evidence, base)
    except (TypeError, KeyError, RecursionError) as exc:
        raise LifecycleError("invalid parser candidate") from exc
    if validated and (evidence is None or evidence["status"] != "validated"):
        raise LifecycleError("a validated parser version is required")
    return candidate


def _check_evidence(evidence, base):
    if not isinstance(evidence, dict) or set(evidence) != {"report", "sha256", "status"}:
        raise LifecycleError("invalid validation evidence")
    report = evidence["report"]
    if (
        not isinstance(report, dict) or set(report) != {"candidate_sha256", "samples", "status"}
        or evidence["status"] != report["status"]
        or report["status"] not in {"validated", "failed"}
        or report["candidate_sha256"] != _digest(_json(base))
        or evidence["sha256"] != _digest(_json(report))
        or not isinstance(report["samples"], list) or not 2 <= len(report["samples"]) <= MAX_SAMPLES
    ):
        raise LifecycleError("validation evidence digest mismatch")
    inputs = set(base["training_sha256"])
    for item in report["samples"]:
        if (
            not isinstance(item, dict)
            or not _sha(item.get("sample_sha256")) or item["sample_sha256"] in inputs
            or not _sha(item.get("expected_sha256"))
            or type(item.get("expected_count")) is not int or not 0 <= item["expected_count"] <= MAX_ROWS
            or (report["status"] == "validated"
                and (item.get("actual_sha256") != item["expected_sha256"]
                     or item.get("actual_count") != item["expected_count"]))
        ):
            raise LifecycleError("invalid independent sample evidence")
        inputs.add(item["sample_sha256"])
    if not any(sample["expected_count"] for sample in report["samples"]):
        raise LifecycleError("validation evidence has no positive example")


def _candidate_view(candidate):
    evidence = candidate["validation"]
    return {key: candidate[key] for key in ("version", "dsl", "source_sha256")} | {
        "status": evidence["status"] if evidence else "draft",
        "training_sample_count": len(candidate["training_sha256"]), "validation": evidence,
    }


def _sample_digest(raw):
    # Ignore newline style and exterior whitespace when screening duplicates.
    return _digest(raw.replace("\r\n", "\n").replace("\r", "\n").strip().encode("utf-8"))


def _training_hashes(samples):
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise LifecycleError(f"training_samples must contain 1..{MAX_SAMPLES} raw strings")
    texts = [_text(raw, "training sample", MAX_SAMPLE) for raw in samples]
    return sorted({_sample_digest(raw) for raw in texts})


def _holdout(samples, candidate):
    if not isinstance(samples, list) or not 2 <= len(samples) <= MAX_SAMPLES:
        raise LifecycleError(f"holdout must contain 2..{MAX_SAMPLES} independent samples")
    inputs = set(candidate["training_sha256"])
    raw_outputs, expected, summaries = [], [], []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"raw_output", "expected_rows"}:
            raise LifecycleError("holdout requires raw_output and expected_rows")
        raw = _text(sample["raw_output"], "holdout raw_output", MAX_SAMPLE, empty=True)
        digest = _sample_digest(raw)
        if digest in inputs:
            raise LifecycleError("holdout samples must be distinct and independent of training bytes")
        inputs.add(digest)
        rows = sample["expected_rows"]
        serialized = _checked_rows(rows)
        raw_outputs.append(raw)
        expected.append(serialized)
        summaries.append({"sample_sha256": digest, "expected_sha256": _digest(serialized),
                          "expected_count": len(rows)})
    if not any(item["expected_count"] for item in summaries):
        raise LifecycleError("holdout requires at least one expected nonempty result")
    if sum(map(len, expected)) > MAX_OUTPUT:
        raise LifecycleError("expected records exceed byte budget")
    return raw_outputs, expected, summaries


def _validate(candidate, samples):
    raw, expected, summaries = _holdout(samples, candidate)
    existing = candidate["validation"]
    if existing and existing["status"] == "validated":
        if [{key: item[key] for key in summaries[0]} for item in existing["report"]["samples"]] != summaries:
            raise LifecycleError("successful validation is immutable; use a new version")
        return existing
    status = "failed"
    try:
        actual = _parse_many(candidate, raw)
        for rows, item in zip(actual, summaries, strict=True):
            item.update(actual_sha256=_digest(_json(rows)), actual_count=len(rows))
        if [_json(rows) for rows in actual] == expected:
            status = "validated"
    except LifecycleError as exc:
        for item in summaries:
            item.update(actual_sha256=None, actual_count=None)
        summaries[0]["diagnostic"] = str(exc)
    base = {key: value for key, value in candidate.items() if key != "validation"}
    report = {"status": status, "samples": summaries, "candidate_sha256": _digest(_json(base))}
    return {"status": status, "report": report, "sha256": _digest(_json(report))}


def _result(slot, version=None):
    result = {"schema": RESULT_SCHEMA, "status": "ok", "platform": slot["platform"],
              "command": slot["command"], "active_version": slot["active"],
              "previous_version": slot["previous"]}
    if slot["active"] is not None:
        _candidate(slot, slot["active"], validated=True)
    if version is None:
        result["versions"] = [_candidate_view(_candidate(slot, key)) for key in sorted(slot["versions"])]
    else:
        result["candidate"] = _candidate_view(_candidate(slot, version))
        if result["candidate"]["status"] == "failed":
            result["status"] = "invalid"
    return result


def manage_parser(*, project: str, platform: str, command: str, operation: str,
                  version=None, dsl=None, source=None, training_samples=None, holdout=None,
                  target=None) -> dict:
    """Manage one named parser slot, without touching a legacy parser file."""
    operations = {
        "create": {"version", "dsl", "source", "training_samples"},
        "validate": {"version", "holdout"},
        "inspect": {"version"},
        "activate": {"version"},
        "rollback": {"target"},
    }
    fields = {"version": version, "dsl": dsl, "source": source,
              "training_samples": training_samples, "holdout": holdout, "target": target}
    if not isinstance(operation, str) or operation not in operations:
        raise LifecycleError("operation must be create, validate, inspect, activate or rollback")
    if any(value is not None and key not in operations[operation] for key, value in fields.items()):
        raise LifecycleError("unexpected field for parser operation")
    if version is not None or operation in {"create", "validate", "activate"}:
        if not isinstance(version, str) or not SAFE_LABEL.fullmatch(version) or version == "legacy":
            raise LifecycleError("version must be a safe label other than legacy")
    if operation == "rollback" and target not in (None, "previous", "legacy"):
        raise LifecycleError("rollback target must be previous or legacy")
    if os.environ.get("AGENT_DIR", ".olav") != ".olav":
        raise LifecycleError("parser management requires the native .olav project layout")
    root = _project(project)
    agent_dir = root / ".olav"
    scope = _scope(platform, command)
    if operation == "inspect":
        return _result(_load_slot(agent_dir, scope), version)
    original = _load_slot(agent_dir, scope)
    if operation == "create":
        if dsl not in ("textfsm", "python"):
            raise LifecycleError("dsl must be textfsm or python")
        candidate = _base_candidate(scope, version, dsl, _text(source, "source", MAX_SOURCE),
                                    _training_hashes(training_samples)) | {"validation": None}
    if operation == "validate":
        original_candidate = _candidate(original, version)
        evidence = _validate(original_candidate, holdout)
    elif operation == "activate":
        _candidate(original, version, validated=True)
    elif operation == "rollback" and not original["versions"]:
        return _result(original)
    with _directory(agent_dir / REGISTRY, create=operation == "create") as directory, _lock(directory):
        slot = _read_slot(directory, scope)
        before = _json(slot)
        prior_active = slot["active"]
        if operation == "create":
            old = slot["versions"].get(version)
            if old is not None and old | {"validation": None} != candidate:
                raise LifecycleError("parser version source and training set are immutable")
            if old is None:
                if len(slot["versions"]) >= MAX_VERSIONS:
                    raise LifecycleError("parser version limit reached")
                slot["versions"][version] = candidate
        elif operation == "validate":
            current = _candidate(slot, version)
            if current != original_candidate:
                raise LifecycleError("parser validation changed concurrently")
            slot["versions"][version] = current | {"validation": evidence}
        elif operation == "activate":
            _candidate(slot, version, validated=True)
            if slot["active"] != version:
                slot["previous"], slot["active"] = slot["active"], version
        else:
            rollback_to = None if target == "legacy" else slot["previous"]
            if rollback_to is not None:
                _candidate(slot, rollback_to, validated=True)
            if slot["active"] != rollback_to:
                slot["previous"], slot["active"] = slot["active"], rollback_to
        if slot["active"] != prior_active:
            slot["selection_revision"] = uuid4().hex
        result = _result(slot, version)
        content = _json(slot)
        if len(content) > MAX_SLOT:
            raise LifecycleError("parser slot exceeds byte budget")
        if content != before:
            _write_file(directory, _slot_name(scope), content, replace=True)
        return result


def _parser_reference(candidate):
    return {"version": candidate["version"], "dsl": candidate["dsl"],
            "source_sha256": candidate["source_sha256"],
            "validation_sha256": candidate["validation"]["sha256"]}


def parse_active(agent_dir: Path, platform: str, command: str, raw: str):
    """Return (rows, trace) for an active parser, or None when using legacy lookup."""
    try:
        scope = _scope(platform, command)
    except LifecycleError:
        return None  # Preserve older callers' input behavior outside versioned scopes.
    slot = _load_slot(agent_dir, scope)
    version = slot["active"]
    if version is None:
        return None
    candidate = _candidate(slot, version, validated=True)
    rows, = _parse_many(candidate, [raw])
    trace = {"tier": "managed", "template_id": f"{REGISTRY}/{_slot_name(scope)}#{version}",
             "template_path_base": ".olav",
             "template_sha256": candidate["source_sha256"],
             "validation_sha256": candidate["validation"]["sha256"]}
    return rows, trace


def active_parser_identity(agent_dir: Path):
    """Identify only active selections for an ingest closure fingerprint."""
    selections = {}
    revisions = {}
    try:
        with _directory(agent_dir / REGISTRY) as directory:
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        raise LifecycleError("parser registry entry budget exceeded")
                    if not SLOT_NAME.fullmatch(entry.name):
                        continue
                    try:
                        cached = _json_load(_read_file(directory, entry.name, MAX_SLOT))
                        scope = _scope(cached["platform"], cached["command"])
                    except (ValueError, KeyError, TypeError, RecursionError) as exc:
                        raise LifecycleError("invalid parser slot") from exc
                    if entry.name != _slot_name(scope):
                        raise LifecycleError("parser slot identity mismatch")
                    slot = _read_slot(directory, scope)
                    if slot["selection_revision"] is not None:
                        revisions[entry.name] = slot["selection_revision"]
                    if slot["active"] is not None:
                        selections[entry.name] = _parser_reference(
                            _candidate(slot, slot["active"], validated=True))
    except FileNotFoundError:
        pass
    if selections:
        from importlib import import_module
        from importlib.metadata import version

        dependencies = (
            "olav_skills.netops.core.parser_learner", "olav_skills.platform.sandbox",
            "olav_skills.platform.safety.sandbox_guard",
        )
        execution = {module: _digest(Path(import_module(module).__file__).read_bytes())
                     for module in dependencies}
        execution[__name__] = _digest(Path(__file__).read_bytes())
        return {"selections": selections, "execution": execution,
                "python": sys.version.split()[0], "textfsm": version("textfsm")}, revisions
    return {}, revisions


def _checked_rows(rows):
    from .netops.core.parser_contract import validate_parser_output

    valid, reason = validate_parser_output(rows, max_records=MAX_ROWS)
    if not valid:
        raise LifecycleError(reason)
    try:
        # JSON encode as bytes, so exact comparisons distinguish bool/int and
        # number/string and do not allow NaN. Require string object keys.
        data = _json(rows)
    except (TypeError, ValueError, RecursionError) as exc:
        raise LifecycleError("parser rows must contain JSON values") from exc
    if len(data) > MAX_OUTPUT or _json_load(data) != rows:
        raise LifecycleError("parser rows exceed byte budget or use non-JSON types")
    return data


def _worker(action: str, payload: dict) -> Any:
    data = _json(payload)
    if len(data) > MAX_REQUEST:
        raise LifecycleError("worker input exceeds byte budget")
    with TemporaryDirectory(prefix="olav-parser-") as scratch:
        # Fixed interpreter and package path. Neither workspace executables nor
        # the caller's environment are inherited by either child.
        env = {
            "OLAV_HOME": scratch, "TMPDIR": scratch, "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "OPENBLAS_NUM_THREADS": "1",
        }
        with subprocess.Popen(
            [sys.executable, "-B", "-m", "olav_skills.parser_lifecycle", action],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=scratch, env=env, start_new_session=True,
        ) as process:
            try:
                stdout, _ = process.communicate(data, timeout=WORKER_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise LifecycleError("parser work exceeded time budget") from None
    if process.returncode or len(stdout) > MAX_REQUEST:
        raise LifecycleError(f"parser worker stopped before a bounded result (exit={process.returncode}, bytes={len(stdout)})")
    result = _json_load(stdout)
    if result.get("error"):
        raise LifecycleError(result["error"])
    return result["value"]


def _parse_many(candidate, raw_outputs):
    raw = [_text(body, "stored parser input", MAX_SAMPLE, empty=True) for body in raw_outputs]
    if len(raw) > MAX_RECORDS or sum(len(body.encode("utf-8")) for body in raw) > MAX_TOTAL_RAW:
        raise LifecycleError("parser input exceeds record or byte budget")
    return _worker("parse", {"dsl": candidate["dsl"], "source": candidate["source"], "raw": raw})


_PURE_MODULES = {
    "re": ("compile", "search", "match", "fullmatch", "findall", "finditer", "split", "sub",
           "subn", "escape", "IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE", "ASCII", "error"),
    "json": ("loads", "dumps", "JSONDecodeError"),
    "ipaddress": ("ip_address", "ip_interface", "ip_network", "IPv4Address", "IPv6Address",
                  "IPv4Network", "IPv6Network", "AddressValueError", "NetmaskValueError"),
    "collections": ("Counter", "defaultdict", "deque"),
    "itertools": ("chain", "groupby", "islice", "pairwise", "product", "zip_longest"),
}
_PURE_BUILTINS = (
    "abs", "all", "any", "bool", "bytes", "dict", "enumerate", "filter", "float",
    "frozenset", "int", "isinstance", "issubclass", "len", "list", "map", "max", "min",
    "next", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple",
    "zip", "Exception", "IndexError", "KeyError", "StopIteration", "TypeError", "ValueError",
)


def _python_profile(source):
    from .netops.core.parser_learner import ast_safety_check
    from .platform.safety.sandbox_guard import scan_sandbox_code

    ok, _reason = ast_safety_check(source)
    guard = scan_sandbox_code(source, network_isolation=True)
    if not ok or guard.hard_block or guard.requires_approval:
        raise LifecycleError("Python parser rejected by AST or sandbox guard")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in _PURE_MODULES for alias in node.names):
                raise LifecycleError("Python parser import is outside the pure parser profile")
        elif isinstance(node, ast.ImportFrom):
            if node.level or any(alias.name not in _PURE_MODULES.get(node.module, ())
                                 for alias in node.names):
                raise LifecycleError("Python parser import is outside the pure parser profile")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise LifecycleError("Python parser cannot access private attributes")
        elif isinstance(node, (ast.ClassDef, ast.Global, ast.Nonlocal)):
            raise LifecycleError("Python parser cannot declare classes or shared interpreter state")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            raise LifecycleError("Python parser cannot access interpreter internals")
    return tree


def _python_rows(source, raw_outputs):
    """Trusted driver executed by the existing parser sandbox child."""
    import builtins
    import importlib
    from types import SimpleNamespace

    try:
        tree = _python_profile(source)
        compiled = compile(tree, "<managed parser>", "exec", dont_inherit=True)
        modules = {}
        for name, allowed in _PURE_MODULES.items():
            module = importlib.import_module(name)
            modules[name] = SimpleNamespace(**{item: getattr(module, item) for item in allowed})

        def available_module(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name not in modules:
                raise ImportError("module is outside the pure parser profile")
            return modules[name]

        allowed = {name: getattr(builtins, name) for name in _PURE_BUILTINS}
        allowed["__import__"] = available_module
        _checked_rows([])  # Load the trusted shape checker before disabling I/O.

        def reject_io(event, _args):
            if (event in {"open", "os.listdir", "os.scandir", "os.chdir", "sys.settrace", "sys.setprofile"}
                    or event.startswith(("os.", "socket.", "subprocess.", "ctypes.", "winreg."))):
                raise PermissionError("I/O and process operations are outside the parser profile")

        sys.addaudithook(reject_io)
        results = []
        total_bytes = total_rows = 0
        for raw in raw_outputs:
            namespace = {"__builtins__": allowed.copy()}
            exec(compiled, namespace)
            rows = namespace["parse"](raw)
            total_bytes += len(_checked_rows(rows))
            total_rows += len(rows)
            if total_bytes > MAX_OUTPUT or total_rows > MAX_TOTAL_ROWS:
                raise LifecycleError("cumulative parser output budget exceeded")
            results.append(rows)
        return {"rows": results}
    except LifecycleError:
        # AST/shape diagnostics are fixed by this module. Do not print parser
        # exceptions or their source/raw data through sandbox stderr.
        return {"error": "Python parser failed AST, result shape, or resource limits"}
    except Exception:
        return {"error": "Python parser execution failed"}


def _apply_parser(dsl, source, raw):
    from .netops.tools.field_normalizer import normalize_fields

    if dsl == "python":
        from base64 import b64encode
        from .platform.sandbox import execute_in_sandbox

        _python_profile(source)
        packed = b64encode(_json([source, raw])).decode("ascii")
        driver = (
            "from base64 import b64decode\n"
            "from olav_skills.parser_lifecycle import _json_load, _python_rows\n"
            f"_result = _python_rows(*_json_load(b64decode({packed!r})))\n"
        )
        result = execute_in_sandbox(driver, timeout=8, network_isolation=False)
        data = result.get("result")
        if result.get("status") != "success" or not isinstance(data, dict):
            raise LifecycleError("Python parser stopped before a bounded result")
        if data.get("error"):
            raise LifecycleError(data["error"])
        parsed = data["rows"]
    elif dsl == "textfsm":
        import io
        import textfsm

        parsed = []
        total_bytes = total_rows = 0
        try:
            template = textfsm.TextFSM(io.StringIO(source))
            headers = [header.lower() for header in template.header]
            if len(set(headers)) != len(headers):
                raise LifecycleError("TextFSM headers conflict after normalization")
            for body in raw:
                template.Reset()
                rows = [dict(zip(headers, row, strict=True)) for row in template.ParseText(body)]
                total_bytes += len(_checked_rows(rows))
                total_rows += len(rows)
                if total_bytes > MAX_OUTPUT or total_rows > MAX_TOTAL_ROWS:
                    raise LifecycleError("cumulative parser output budget exceeded")
                parsed.append(rows)
        except textfsm.TextFSMError as exc:
            raise LifecycleError("TextFSM template or parse failed") from exc
    else:
        raise LifecycleError("unsupported parser DSL")

    normalized = [normalize_fields(rows) for rows in parsed]
    if (sum(len(rows) for rows in normalized) > MAX_TOTAL_ROWS
            or sum(len(_checked_rows(rows)) for rows in normalized) > MAX_OUTPUT):
        raise LifecycleError("cumulative parser output budget exceeded")
    return normalized


def _source_table(conn, name, required_text, optional_type_columns=()):
    kind = conn.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE encode(table_schema)=encode('netops') AND encode(table_name)=encode(?)",
        [name],
    ).fetchall()
    if not kind:
        return None
    if kind != [("BASE TABLE",)]:
        raise LifecycleError("stored raw source must be a native base table")
    columns = dict(conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE encode(table_schema)=encode('netops') AND encode(table_name)=encode(?) LIMIT 65", [name],
    ).fetchall())
    if (len(columns) > 64
            or not all(columns.get(key) == "VARCHAR" for key in required_text)
            or not all(key in columns for key in optional_type_columns)):
        raise LifecycleError("stored raw source schema is unsupported")
    return columns


def _time(value, source):
    from .netops.core.query.temporal import time_value, unknown

    if value is None:
        return unknown()
    if not isinstance(value, dict) or not {"value", "precision", "status", "source"} <= set(value):
        raise LifecycleError("invalid stored collection-time evidence")
    status = value["status"]
    if value["source"] is not None:
        _text(value["source"], "stored collection-time source", 128)
    if status in ("unknown", "conflict", "invalid") and value["value"] is None:
        return unknown(value["source"], status)
    parsed = time_value(value["value"], source)
    if (status in ("known", "partial") and value["source"] and parsed["status"] == "known"
            and parsed["precision"] == value["precision"]):
        return {key: value[key] for key in ("value", "precision", "status", "source")}
    raise LifecycleError("invalid stored collection-time evidence")


def _table_time(fields):
    from .netops.core.query.temporal import time_value, unknown

    evidence = [time_value(value, "netops.raw_output_store." + key, date_only=kind == "DATE")
                for key, kind, value in fields if value is not None]
    if not evidence:
        return unknown()
    if any((stamp["value"], stamp["precision"], stamp["status"]) !=
           (evidence[0]["value"], evidence[0]["precision"], evidence[0]["status"])
           for stamp in evidence[1:]):
        return unknown("netops.raw_output_store.collection_fields", "conflict")
    return evidence[0]


def _source_metadata(source_json, key, raw_hash):
    provenance = _json_load(source_json)
    if not isinstance(provenance, dict) or provenance.get("raw_artifact") != key:
        raise LifecycleError("stored raw source identity mismatch")
    if provenance.get("actual_raw_sha256") != raw_hash:
        raise LifecycleError("stored raw source hash mismatch")
    meta = {
        name: _text(provenance[name], name, 512)
        for name in ("input_artifact", "input_sha256", "body_sha256", "source_fingerprint",
                     "collection_source", "parser_fingerprint")
        if provenance.get(name) is not None
    }
    reported = provenance.get("platform")
    if reported is not None:
        meta["platform"] = _text(reported, "stored source platform", 64)
    return (provenance.get("effective_platform") or reported,
            _time(provenance.get("collected_at"), "netops.ingest_sources.source_json.collected_at"),
            key | meta)


def _bundle_times(conn, rows, snapshot_id):
    from .netops.core.query.temporal import time_value

    identities = {(bundle_id, bundle_sha)
                  for _, _, _, _, _, bundle_id, bundle_sha, *_ in rows if bundle_id}
    if not identities:
        return {}
    columns = _source_table(conn, "bundle_ingests", ("snapshot_id", "bundle_id", "bundle_sha256"),
                            ("collected_at",))
    if not columns or not columns["collected_at"].startswith(("TIMESTAMP", "DATE")):
        return {}
    times = {}
    for bundle_id, bundle_sha in sorted(identities, key=lambda key: (key[0], key[1] or "")):
        matches = conn.execute(
            "SELECT CAST(collected_at AS VARCHAR) FROM netops.bundle_ingests "
            "WHERE encode(snapshot_id)=encode(?) AND encode(bundle_id)=encode(?) "
            "AND (? IS NULL OR encode(bundle_sha256)=encode(?)) LIMIT 2",
            [snapshot_id, bundle_id, bundle_sha, bundle_sha],
        ).fetchall()
        if len(matches) > 1:
            raise LifecycleError("ambiguous stored bundle time for raw provenance")
        if matches:
            times[(bundle_id, bundle_sha)] = time_value(
                matches[0][0], "netops.bundle_ingests.collected_at",
                date_only=columns["collected_at"] == "DATE",
            ) | {"scope": "bundle_metadata_only"}
    return times


def _source_rows(conn, snapshot_id, command):
    ledger = "ingest_sources"
    native = "raw_output_store"
    keys = ("snapshot_id", "device_name", "command")
    ledger_cols = _source_table(conn, ledger, (*keys, "raw_output", "source_json"))
    if ledger_cols is not None and conn.execute(
        "SELECT 1 FROM netops.ingest_sources WHERE encode(snapshot_id)=encode(?) LIMIT 1", [snapshot_id],
    ).fetchone():
        table = ledger
        source_scope = "historical_ledger_exact_label"
        record_limit = ("octet_length(encode(source_json)) <= 65536 AND "
                        "octet_length(encode(raw_output)) <= ?")
        fields = [f"CASE WHEN {record_limit} THEN source_json END"]
        params = [MAX_SAMPLE]
    else:
        table = native
        source_scope = "last_writer_exact_label_partial"
        native_cols = _source_table(conn, native, (*keys, "raw_output", "platform"))
        if native_cols is None:
            return "source_unavailable", [], [], {}
        optional = ("bundle_id", "bundle_sha256", "ingested_via")
        times = [(name, native_cols[name]) for name in ("collected_at", "captured_at", "snapshot_date")
                 if native_cols.get(name, "").startswith(("TIMESTAMP", "DATE"))]
        fields = ["substr(platform,1,65)"]
        fields.extend(f"substr({name},1,513)" if name in native_cols else "NULL" for name in optional)
        fields.extend(f"CAST({name} AS VARCHAR)" for name, _ in times)
        params = []
    # Read at most the documented budget before moving text into Python.
    # The native branch selects only the retained last-writer labels, not a
    # snapshot partition. It cannot retrieve earlier overwritten records.
    # Compare encoded text so declared collations cannot change identity.
    rows = conn.execute(
        "SELECT substr(snapshot_id,1,257), substr(device_name,1,513), substr(command,1,257), "
        "CASE WHEN octet_length(encode(raw_output))<=? THEN raw_output END, "
        + ", ".join(fields)
        + f" FROM netops.{table} WHERE encode(snapshot_id)=encode(?)"
          " AND encode(regexp_replace(lower(trim(command)), ' +', ' ', 'g'))=encode(?)"
          " ORDER BY encode(device_name), encode(command) LIMIT ?",
        [MAX_SAMPLE, *params, snapshot_id, command, MAX_RECORDS + 1],
    ).fetchall()
    if len(rows) > MAX_RECORDS:
        raise LifecycleError("stored raw source exceeds record budget")
    if table == native:
        return source_scope, rows, times, _bundle_times(conn, rows, snapshot_id)
    return source_scope, rows, [], {}


def _read_source(project, snapshot_id, platform, command):
    import duckdb

    root = _project(project)
    from .native_snapshot import NativeSnapshotError, open_native_snapshot
    # Preserve unavailable versus unsafe-path semantics without opening the DB.
    try:
        with _directory(root / '.olav/databases') as directory:
            info=os.stat('main.duckdb',dir_fd=directory,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise LifecycleError('native raw source rejected: unsafe source path')
    except FileNotFoundError:
        return {"scope":"source_unavailable","records":[],"unknown_platform_count":0}
    except OSError:
        raise LifecycleError('native raw source rejected: unsafe source path') from None
    try:
        with TemporaryDirectory(prefix="reparse-snapshot-") as scratch:
            with open_native_snapshot(root, Path(scratch), max_bytes=256*1024*1024,
                                      deadline=time.monotonic()+8, memory_limit="128MiB", threads=1) as (conn, source_version):
                scope, rows, time_fields, bundle_times = _source_rows(conn, snapshot_id, command)
    except NativeSnapshotError as exc:
        if exc.code=="source_unavailable":
            return {"scope":"source_unavailable","records":[],"unknown_platform_count":0}
        raise LifecycleError("active or uncheckpointed database WAL is refused" if exc.code=="source_has_wal" else "native raw source rejected: "+exc.code) from None
    except duckdb.Error:
        raise LifecycleError("native raw source cannot be read within its schema and resource limits") from None

    records, seen = [], set()
    unknown_platform = total_raw = 0
    for stored_sid, device, command_stored, raw, *fields in rows:
        if stored_sid != snapshot_id:
            raise LifecycleError("stored snapshot identity mismatch")
        device = _text(device, "stored device name", 512)
        command_stored = _text(command_stored, "stored command", 256)
        raw = _text(raw, "stored raw record", MAX_SAMPLE, empty=True)
        encoded = raw.encode("utf-8")
        total_raw += len(encoded)
        if total_raw > MAX_TOTAL_RAW:
            raise LifecycleError("stored raw source exceeds byte budget")
        raw_hash = _digest(encoded)
        if scope == "historical_ledger_exact_label":
            key = {"table": "netops.ingest_sources", "snapshot_id": stored_sid, "device_name": device,
                   "command": command_stored}
            if not isinstance(fields[0], str):
                raise LifecycleError("stored raw provenance missing or exceeds byte budget")
            reported_platform, capture, source = _source_metadata(fields[0], key, raw_hash)
        else:
            reported_platform, bundle_id, bundle_sha, ingested_via, *values = fields
            capture = _table_time([(name, kind, value)
                                   for (name, kind), value in zip(time_fields, values, strict=True)])
            source = {"table": "netops.raw_output_store", "snapshot_id": stored_sid, "device_name": device,
                      "command": command_stored,
                      "bundle_id": bundle_id, "bundle_sha256": bundle_sha, "ingested_via": ingested_via}
            for key in ("bundle_id", "bundle_sha256", "ingested_via"):
                if source[key] is not None:
                    _text(source[key], f"stored {key}", 512)
            if (bundle_id, bundle_sha) in bundle_times:
                source["bundle_collected_at"] = bundle_times[(bundle_id, bundle_sha)]
        try:
            stored_scope = _scope(reported_platform, command_stored)
        except LifecycleError:
            unknown_platform += 1
            continue
        if stored_scope != (platform, command):
            continue
        if device in seen:
            raise LifecycleError("multiple stored raw records for the same device and command")
        seen.add(device)
        records.append({"snapshot_id": stored_sid, "device_name": device, "command": command_stored,
                        "platform": reported_platform, "raw_sha256": raw_hash, "collected_at": capture,
                        "source": source, "raw": raw})
    return {"scope": scope, "records": records, "unknown_platform_count": unknown_platform}


def reparse_snapshot(*, project: str, snapshot_id: str, platform: str, command: str,
                     version: str, output: str) -> dict:
    """Read one explicitly labelled source and publish a new derived JSON."""
    root = _project(project)
    scope = _scope(platform, command)
    sid = _text(snapshot_id, "snapshot_id", 256)
    if sid.strip() != sid or sid.lower() in {"latest", "current", "newest"}:
        raise LifecycleError("snapshot_id must be an exact label, without aliases")
    if not isinstance(version, str) or not SAFE_LABEL.fullmatch(version):
        raise LifecycleError("invalid parser version label")
    if not isinstance(output, str) or not SAFE_LABEL.fullmatch(output) or not output.endswith(".json"):
        raise LifecycleError("output must be a new safe .json filename")
    agent = root / ".olav"
    candidate = _candidate(_load_slot(agent, scope), version, validated=True)
    report = {
        "schema": EXPORT_SCHEMA, "snapshot_id": sid, "platform": scope[0], "command": scope[1],
        "parser": _parser_reference(candidate),
    }
    source = _worker("source", {"project": str(root), "snapshot_id": sid,
                                "platform": scope[0], "command": scope[1]})
    records = source["records"]
    coverage = {"source_scope": source["scope"], "matched_raw_records": len(records),
                "unknown_platform_count": source["unknown_platform_count"],
                "snapshot_collection_coverage": "unknown"}
    if not records:
        return report | {"status": "unavailable", "coverage": coverage, "artifact": None}
    parsed = _parse_many(candidate, [record.pop("raw") for record in records])
    for record, rows in zip(records, parsed, strict=True):
        record.update(rows=rows, parse_status="parsed" if rows else "no_rows")
    report |= {"status": "ok", "coverage": coverage}
    content = _json(report | {"records": records}) + b"\n"
    if len(content) > MAX_OUTPUT:
        raise LifecycleError("derived export exceeds byte budget")
    with _directory(agent / EXPORT_DIR, create=True) as directory:
        _write_file(directory, output, content, replace=False)
    return report | {"artifact": str(agent / EXPORT_DIR / output), "sha256": _digest(content)}


def _worker_main(action):
    import resource

    try:
        if action == "source":
            validate_native_reader_identity(SOURCE_READER_MODULE)
        resource.setrlimit(resource.RLIMIT_CPU, (8, 9))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, ((256 if action=="source" else 24) * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if action == "source":
            bootstrap_native_reader(SOURCE_READER_MODULE)
        payload = _json_load(sys.stdin.buffer.read(MAX_REQUEST + 1))
        value = _apply_parser(**payload) if action == "parse" else _read_source(**payload)
        content = _json({"value": value})
        if len(content) > MAX_REQUEST:
            raise LifecycleError("worker output exceeds byte budget")
    except (LifecycleError, NativeReaderBootstrapError) as exc:
        content = _json({"error": str(exc)[:512]})
    except (OSError, ValueError, TypeError, RecursionError, MemoryError):
        content = _json({"error": "parser worker rejected data or exceeded resource limits"})
    if action == "source" and owns_native_reader(SOURCE_READER_MODULE):
        from .native_snapshot import close_native_reader
        close_native_reader()
    sys.stdout.buffer.write(content)


def main(action):
    """Read one bounded stdin request for the packaged skill entry."""
    handler = manage_parser if action == "manage" else reparse_snapshot
    schema = RESULT_SCHEMA if action == "manage" else EXPORT_SCHEMA
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise LifecycleError("request exceeds byte budget")
        payload = _json_load(data)
        if not isinstance(payload, dict):
            raise LifecycleError("request must be a JSON object")
        value = handler(**payload)
    except LifecycleError as exc:
        value = {"schema": schema, "status": "error", "error": str(exc)[:512]}
    except (OSError, ValueError, TypeError, RecursionError, MemoryError):
        value = {"schema": schema, "status": "error",
                 "error": "invalid request, unsafe filesystem, or resource limit"}
    print(_json(value).decode("utf-8"))
    return 0 if value["status"] == "ok" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("parse", "source"):
        raise SystemExit(2)
    _worker_main(sys.argv[1])
