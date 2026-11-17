"""Fixture-verifiable writer-domain foundation; no caller wiring or provisioning.

The static ErrorCode enum is the complete public failure vocabulary. Matching a
runtime digest authenticates an operator pin, not installed-code provenance.
Trusted-root live edits and hostile arbitrary same-UID Python are out of scope.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
import uuid


class ErrorCode(str, Enum):
    MALFORMED = "MALFORMED"
    PIN = "PIN"
    TRUST = "TRUST"
    CONFIG_SWITCH = "CONFIG_SWITCH"
    VIEW = "VIEW"
    IDENTITY = "IDENTITY"
    MISSING = "MISSING"
    BUSY = "BUSY"
    NO_LEASE = "NO_LEASE"
    OWNER = "OWNER"
    INVALID_CONFIG = "INVALID_CONFIG"
    INTERNAL = "INTERNAL"


class WriterDomainError(Exception):
    """Credential-free error shared by the Python API and module CLI."""

    def __init__(self, code: ErrorCode):
        self.code = ErrorCode(code).value
        super().__init__(self.code)


def _fail(code):
    raise WriterDomainError(code)


def _safe(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except WriterDomainError:
            raise
        except FileNotFoundError:
            raise WriterDomainError(ErrorCode.MISSING) from None
        except OSError as exc:
            code = ErrorCode.TRUST if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM) else ErrorCode.IDENTITY
            raise WriterDomainError(code) from None
        except Exception:
            raise WriterDomainError(ErrorCode.INTERNAL) from None
    return wrapped


@dataclass(frozen=True)
class _Config:
    path: str
    sha256: str
    runtime_sha256: str
    deployment_id: str
    epoch: int
    boot_id: str
    service_uid: int
    service_gid: int
    user_namespace: str
    mount_namespace: str
    guard_path: str
    device: int
    inode: int
    parent_device: int
    parent_inode: int
    manifest_identity: tuple
    manifest_chain: tuple
    guard_chain: tuple


@dataclass(frozen=True)
class _Lease:
    config: _Config
    fd: int
    pid: int
    thread: int


_mutex = threading.RLock()
_binding = None
_configs = []
_active = None
_depth = 0
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _path(value):
    if not isinstance(value, str) or not value.startswith("/") or any(unicodedata.category(c) in ("Cc", "Cs") for c in value):
        _fail(ErrorCode.MALFORMED)
    if value == "/" or os.path.normpath(value) != value or value.startswith("//"):
        _fail(ErrorCode.MALFORMED)
    return value


def _root_trusted(info, *, directory):
    """Pure metadata policy; callers still perform real descriptor/path checks."""
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        _fail(ErrorCode.TRUST)
    if not directory and info.st_nlink != 1:
        _fail(ErrorCode.TRUST)


def _stable_stat(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(info):
    return (info.st_dev, info.st_ino)


@contextmanager
def _opened(path, flags):
    """Walk from / with pinned descriptors; never follow or create anything."""
    dirs = []
    fd = None
    try:
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        dirs.append(current)
        chain = []
        for part in path.split("/")[1:-1]:
            info = os.fstat(current)
            _root_trusted(info, directory=True)
            chain.append(_identity(info))
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
            dirs.append(current)
        parent = os.fstat(current)
        _root_trusted(parent, directory=True)
        chain.append(_identity(parent))
        name = path.rsplit("/", 1)[1]
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=current)
        info = os.fstat(fd)
        if _identity(info) != _identity(os.stat(name, dir_fd=current, follow_symlinks=False)):
            _fail(ErrorCode.IDENTITY)
        yield fd, info, parent, tuple(chain)
    finally:
        if fd is not None:
            os.close(fd)
        for directory in reversed(dirs):
            os.close(directory)


def _read_manifest(path):
    with _opened(path, os.O_RDONLY) as (fd, info, _, chain):
        _root_trusted(info, directory=False)
        if info.st_size > 65536:
            _fail(ErrorCode.MALFORMED)
        chunks = []
        remaining = 65537
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > 65536 or _stable_stat(os.fstat(fd)) != _stable_stat(info):
            _fail(ErrorCode.IDENTITY)
        return raw, _identity(info), chain


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail(ErrorCode.MALFORMED)
        result[key] = value
    return result


def _parse(raw):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _fail(ErrorCode.MALFORMED)
    fields = {"schema", "deployment_id", "epoch", "boot_id", "service_uid", "service_gid", "user_namespace", "mount_namespace", "runtime_sha256", "guard"}
    if type(data) is not dict or set(data) != fields:
        _fail(ErrorCode.MALFORMED)
    guard = data["guard"]
    if type(guard) is not dict or set(guard) != {"path", "device", "inode", "parent_device", "parent_inode"}:
        _fail(ErrorCode.MALFORMED)
    for key in fields - {"guard", "epoch", "service_uid", "service_gid"}:
        if type(data[key]) is not str:
            _fail(ErrorCode.MALFORMED)
    if data["schema"] != "olav.writer-domain/v1" or not _ID.fullmatch(data["deployment_id"]) or not _HEX.fullmatch(data["runtime_sha256"]):
        _fail(ErrorCode.MALFORMED)
    try:
        if str(uuid.UUID(data["boot_id"])) != data["boot_id"]:
            _fail(ErrorCode.MALFORMED)
    except ValueError:
        _fail(ErrorCode.MALFORMED)
    for name, prefix in (("user_namespace", "user"), ("mount_namespace", "mnt")):
        if not re.fullmatch(prefix + r":\[[0-9]+\]", data[name]):
            _fail(ErrorCode.MALFORMED)
    for values, positive, nonnegative in ((data, ("epoch",), ("service_uid", "service_gid")), (guard, ("inode", "parent_inode"), ("device", "parent_device"))):
        for key in positive + nonnegative:
            if type(values[key]) is not int or values[key] < (1 if key in positive else 0):
                _fail(ErrorCode.MALFORMED)
    _path(guard["path"])
    return data, guard


def _boot():
    with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as stream:
        return stream.read(128).strip()


def _view(config):
    if os.getuid() != config.service_uid or os.geteuid() != config.service_uid or os.getgid() != config.service_gid or os.getegid() != config.service_gid:
        _fail(ErrorCode.OWNER)
    if _boot() != config.boot_id or os.readlink("/proc/self/ns/user") != config.user_namespace or os.readlink("/proc/self/ns/mnt") != config.mount_namespace:
        _fail(ErrorCode.VIEW)


def _guard(config, info, parent, chain):
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        _fail(ErrorCode.IDENTITY)
    if (info.st_uid, info.st_gid) != (config.service_uid, config.service_gid):
        _fail(ErrorCode.OWNER)
    if _identity(info) != (config.device, config.inode) or _identity(parent) != (config.parent_device, config.parent_inode) or chain != config.guard_chain:
        _fail(ErrorCode.IDENTITY)


def _registered(config):
    if not any(config is known for known in _configs):
        _fail(ErrorCode.INVALID_CONFIG)


def _validate(config, held_fd=None):
    _view(config)
    raw, identity, chain = _read_manifest(config.path)
    if hashlib.sha256(raw).hexdigest() != config.sha256:
        _fail(ErrorCode.PIN)
    if identity != config.manifest_identity or chain != config.manifest_chain:
        _fail(ErrorCode.IDENTITY)
    with _opened(config.guard_path, os.O_RDONLY) as (_, info, parent, chain):
        _guard(config, info, parent, chain)
        if held_fd is not None:
            _guard(config, os.fstat(held_fd), parent, chain)
    _view(config)


@_safe
def load_manifest(path, *, expected_sha256, expected_runtime_sha256):
    """Validate operator pins and root trust, then permanently bind this process."""
    global _binding
    try:
        path = _path(os.fspath(path))
    except TypeError:
        _fail(ErrorCode.MALFORMED)
    if not all(type(value) is str and _HEX.fullmatch(value) for value in (expected_sha256, expected_runtime_sha256)):
        _fail(ErrorCode.PIN)
    with _mutex:
        raw, identity, chain = _read_manifest(path)
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            _fail(ErrorCode.PIN)
        data, guard = _parse(raw)
        if data["runtime_sha256"] != expected_runtime_sha256:
            _fail(ErrorCode.PIN)
        binding = (path, expected_sha256, data["deployment_id"], data["epoch"])
        if _binding is not None and binding != _binding:
            _fail(ErrorCode.CONFIG_SWITCH)
        if _configs:
            _validate(_configs[0])
        if path == guard["path"] or identity == (guard["device"], guard["inode"]):
            _fail(ErrorCode.IDENTITY)
        with _opened(guard["path"], os.O_RDONLY) as (_, _, _, guard_chain):
            config = _Config(path, expected_sha256, expected_runtime_sha256, data["deployment_id"], data["epoch"], data["boot_id"], data["service_uid"], data["service_gid"], data["user_namespace"], data["mount_namespace"], guard["path"], guard["device"], guard["inode"], guard["parent_device"], guard["parent_inode"], identity, chain, guard_chain)
        _validate(config)
        _binding = binding
        _configs.append(config)
        return config


@_safe
def preflight(config):
    """Observe identity only. This never takes a flock or establishes a lease."""
    with _mutex:
        _registered(config)
        _validate(config)
        return {"readonly": True, "offline_verified": False, "schema": "olav.writer-domain/preflight-v1", "guard_device": config.device, "guard_inode": config.inode, "epoch": config.epoch}


@_safe
def assert_writer_domain():
    """Return the active lease only for its owning process and thread."""
    with _mutex:
        if _active is None:
            _fail(ErrorCode.NO_LEASE)
        if (_active.pid, _active.thread) != (os.getpid(), threading.get_ident()):
            _fail(ErrorCode.OWNER)
        _registered(_active.config)
        _validate(_active.config, _active.fd)
        return _active


@contextmanager
def writer_domain(config):
    """Hold one nonblocking flock; same-thread reentry reuses its descriptor."""
    global _active, _depth
    lease = _acquire(config)
    try:
        yield lease
    finally:
        with _mutex:
            # A child exiting an inherited context must not affect its parent.
            if _active is lease and lease.pid == os.getpid():
                _depth -= 1
                if _depth == 0:
                    _active = None
                    os.close(lease.fd)  # Never LOCK_UN: fork shares descriptions.


@_safe
def _acquire(config):
    global _active, _depth
    with _mutex:
        _registered(config)
        if _active is not None:
            lease = assert_writer_domain()
            if lease.config != config:
                _fail(ErrorCode.CONFIG_SWITCH)
            _depth += 1
            return lease
        _validate(config)
        # _opened owns the original descriptor. The duplicate keeps precisely
        # that open description alive after the traversal context closes.
        fd = None
        try:
            with _opened(config.guard_path, os.O_RDWR) as (original, info, parent, chain):
                _guard(config, info, parent, chain)
                try:
                    fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EACCES):
                        _fail(ErrorCode.BUSY)
                    raise
                _validate(config, original)
                fd = os.dup(original)
            lease = _Lease(config, fd, os.getpid(), threading.get_ident())
            _active = lease
            _depth = 1
            return lease
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise


def _after_fork():
    global _active, _depth, _mutex
    if _active is not None:
        os.close(_active.fd)
    _active = None
    _depth = 0
    _mutex = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=lambda: _mutex.acquire(), after_in_parent=lambda: _mutex.release(), after_in_child=_after_fork)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _fail(ErrorCode.MALFORMED)


def main(argv=None):
    """Operator-only, bounded JSON preflight entry point."""
    try:
        parser = _Parser(description="Read-only writer-domain identity preflight")
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--sha256", required=True)
        parser.add_argument("--runtime-sha256", required=True)
        parser.add_argument("verb", choices=("preflight",))
        args = parser.parse_args(argv)
        config = load_manifest(args.manifest, expected_sha256=args.sha256, expected_runtime_sha256=args.runtime_sha256)
        result = preflight(config)
    except Exception as exc:
        result = {"error": exc.code if isinstance(exc, WriterDomainError) else ErrorCode.INTERNAL.value}
        print(json.dumps(result, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
