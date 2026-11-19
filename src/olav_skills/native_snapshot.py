"""Open a read-only DuckDB copy of one descriptor-bound native source."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import ctypes
import errno
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
import time


DB_NAME = "main.duckdb"
DB_RELATIVE = ".olav/databases/" + DB_NAME
BACKEND = "native_duckdb_locked_copy"
_CHUNK = 1024 * 1024
_MEMORY = re.compile(r"[1-9][0-9]{0,5} *(?:[KMG]i?B)")


class NativeSnapshotError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _FileLock(ctypes.Structure):
    # Linux LP64 struct flock: short, short, off_t, off_t, pid_t.
    _fields_ = [("kind", ctypes.c_short), ("whence", ctypes.c_short),
                ("start", ctypes.c_long), ("length", ctypes.c_long), ("pid", ctypes.c_int)]


def _deadline(deadline):
    if time.monotonic() >= deadline:
        raise NativeSnapshotError("deadline_exceeded", "Native snapshot read exceeded its deadline.")


def _check_limits(max_bytes, deadline, memory_limit, threads):
    try:
        valid_deadline = type(deadline) in (int, float) and math.isfinite(deadline)
    except OverflowError:
        valid_deadline = False
    if (type(max_bytes) is not int or max_bytes < 1 or not valid_deadline
            or type(threads) is not int or threads < 1
            or not isinstance(memory_limit, str) or not _MEMORY.fullmatch(memory_limit)):
        raise NativeSnapshotError("invalid_limits", "Native snapshot requires positive byte, thread and memory limits and a finite deadline.")


def _directory(stack, name, parent=None):
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(name, flags, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd


def _walk_directories(stack, components):
    parent = _directory(stack, "/")
    for component in components:
        parent = _directory(stack, component, parent)
    return parent


def _database_parent(stack, project):
    return _walk_directories(stack, (*project.parts[1:], ".olav", "databases"))


def _scratch_directory(stack, scratch):
    try:
        directory = _walk_directories(stack, scratch.parts[1:])
        info = os.fstat(directory)
        if info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700 and not os.listdir(directory):
            return directory, (info.st_dev, info.st_ino)
    except OSError:
        pass
    raise NativeSnapshotError("unsafe_scratch", "Native snapshot storage requires a private empty 0700 directory.")


def _read_lock(fd):
    import fcntl

    # Some Python builds omit this Linux UAPI constant. Both these LP64
    # architectures use the generic F_OFD_SETLK number from Linux headers.
    operation = getattr(fcntl, "F_OFD_SETLK", None)
    if operation is None and os.uname().machine in {"x86_64", "aarch64"}:
        operation = 37
    if operation is None:
        raise NativeSnapshotError("unsupported_platform", "Native snapshot reads require Linux OFD locking.")
    try:
        fcntl.fcntl(fd, operation, bytes(_FileLock(fcntl.F_RDLCK, os.SEEK_SET, 0, 0, 0)))
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise NativeSnapshotError("source_locked", "Native database has an active writer.") from None
        raise NativeSnapshotError("source_lock_unavailable", "Could not acquire the native source read lock.") from None


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_nlink, info.st_size, info.st_ctime_ns, info.st_mtime_ns)


def _verify_source(parent, fd, identity):
    for suffix in (".wal", ".wal.checkpoint", ".wal.recovery"):
        try:
            os.stat(DB_NAME + suffix, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise NativeSnapshotError("source_has_wal", "Native source has an active or uncheckpointed WAL.")
    try:
        visible = os.stat(DB_NAME, dir_fd=parent, follow_symlinks=False)
    except OSError:
        raise NativeSnapshotError("source_changed", "Native database path changed during the read.") from None
    if _identity(visible) != identity or _identity(os.fstat(fd)) != identity:
        raise NativeSnapshotError("source_changed", "Native database identity changed during the read.")


def _copy_source(source, target, max_bytes, deadline):
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(os.dup(target), "wb") as output:
        while True:
            _deadline(deadline)
            chunk = os.read(source, min(_CHUNK, max_bytes - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise NativeSnapshotError("source_budget_exceeded", "Native database exceeds the source byte budget.")
            output.write(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _snapshot_hash(fd, size, deadline):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        _deadline(deadline)
        chunk = os.pread(fd, min(_CHUNK, size - offset), offset)
        if not chunk:
            break
        offset += len(chunk)
        digest.update(chunk)
    if offset != size or os.fstat(fd).st_size != size:
        raise NativeSnapshotError("snapshot_changed", "Private database copy does not match the copied byte count.")
    return digest.hexdigest()


def _verify_snapshot(scratch, parent, directory_identity, fd, identity):
    try:
        current = os.stat(scratch, follow_symlinks=False)
        visible = os.stat(DB_NAME, dir_fd=parent, follow_symlinks=False)
    except OSError:
        raise NativeSnapshotError("snapshot_changed", "Private database copy path changed.") from None
    if ((current.st_dev, current.st_ino) != directory_identity or _identity(visible) != identity
            or _identity(os.fstat(fd)) != identity):
        raise NativeSnapshotError("snapshot_changed", "Private database copy identity changed.")


def _remove_copy(parent):
    try:
        os.unlink(DB_NAME, dir_fd=parent)
    except FileNotFoundError:
        pass


@contextmanager
def open_native_snapshot(
    project: Path,
    scratch: Path,
    *,
    max_bytes: int,
    deadline: float,
    memory_limit: str = "128MiB",
    threads: int = 1,
):
    """Yield a read-only connection and the version of its exact copied bytes.

    Internal primitive: call only in a dedicated exec reader with no existing
    source DB handles or inherited source locks. Public wrappers supervise that
    process and publish results only after this context exits successfully.
    The caller supplies an authorized absolute project, a private empty 0700
    directory and a monotonic deadline. The caller owns directory lifetime and
    a whole-operation deadline for blocking DuckDB or filesystem calls. Scratch
    must be beneath a trusted stable temporary root (including host TMPDIR).
    """
    if sys.platform != "linux" or ctypes.sizeof(ctypes.c_long) != 8:
        raise NativeSnapshotError("unsupported_platform", "Native snapshot reads require 64-bit Linux.")
    project, scratch = Path(project), Path(scratch)
    if (not project.is_absolute() or project.anchor != "/" or project == Path("/")
            or ".." in project.parts or "\0" in str(project)):
        raise NativeSnapshotError("invalid_project", "Provide an authorized absolute native project path.")
    if (not scratch.is_absolute() or scratch.anchor != "/" or scratch == Path("/")
            or ".." in scratch.parts or "\0" in str(scratch)):
        raise NativeSnapshotError("unsafe_scratch", "Native snapshot storage requires a private absolute path.")
    _check_limits(max_bytes, deadline, memory_limit, threads)
    _deadline(deadline)
    import duckdb

    try:
        with ExitStack() as stack:
            scratch_fd, scratch_identity = _scratch_directory(stack, scratch)
            parent = _database_parent(stack, project)
            source = os.open(DB_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=parent)
            stack.callback(os.close, source)
            info = os.fstat(source)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise NativeSnapshotError("unsafe_source", "Native database must be a singly linked regular file.")
            _read_lock(source)
            info = os.fstat(source)
            if info.st_nlink != 1:
                raise NativeSnapshotError("unsafe_source", "Native database must be a singly linked regular file.")
            identity = _identity(info)
            _verify_source(parent, source, identity)
            if info.st_size > max_bytes:
                raise NativeSnapshotError("source_budget_exceeded", "Native database exceeds the source byte budget.")
            target = os.open(DB_NAME, os.O_EXCL | os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=scratch_fd)
            stack.callback(_remove_copy, scratch_fd)
            stack.callback(os.close, target)
            size, digest = _copy_source(source, target, max_bytes, deadline)
            _verify_source(parent, source, identity)
            if size != info.st_size or _snapshot_hash(target, size, deadline) != digest:
                raise NativeSnapshotError("snapshot_changed", "Private database copy does not match the source bytes.")
            os.fchmod(target, 0o400)
            copy_identity = _identity(os.fstat(target))
            _verify_snapshot(scratch, scratch_fd, scratch_identity, target, copy_identity)
            config = {
                "enable_external_access": False, "temp_directory": "", "max_temp_directory_size": "0B",
                "threads": threads, "memory_limit": memory_limit, "allow_unsigned_extensions": False,
                "autoload_known_extensions": False, "autoinstall_known_extensions": False,
                "lock_configuration": True,
            }
            version = {
                "path": DB_RELATIVE, "backend": BACKEND, "bytes": size, "sha256": digest,
                "duckdb_version": duckdb.__version__,
                "source_identity": dict(device=info.st_dev, inode=info.st_ino,
                                        mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns),
            }
            # DuckDB resolves filename symlinks. Its path is the separately
            # verified copy in the caller-owned directory, never the original.
            with closing(duckdb.connect(str(scratch / DB_NAME), read_only=True, config=config)) as conn:
                _deadline(deadline)
                _verify_snapshot(scratch, scratch_fd, scratch_identity, target, copy_identity)
                yield conn, version
                _deadline(deadline)
                _verify_snapshot(scratch, scratch_fd, scratch_identity, target, copy_identity)
                with ExitStack() as check:
                    try:
                        current_parent = _database_parent(check, project)
                    except OSError:
                        raise NativeSnapshotError("source_changed", "Native database directory changed.") from None
                    _verify_source(current_parent, source, identity)
    except OSError:
        raise NativeSnapshotError("source_unavailable", "Native database or private snapshot storage is unavailable.") from None


def close_native_reader():
    """Join DuckDB's default scheduler before interpreter module finalization.

    Dedicated exec reader termination only: all explicit read connections and
    source validation contexts must already have finished. Never call from a
    host/public library API, which may own a default connection. Failure is not
    swallowed; the supervisor must reject an abnormal reader exit. This avoids
    the observed default-connection finalizer/scheduler allocator teardown race,
    not a claim that every native crash has been fixed.
    """
    module = sys.modules.get("duckdb")
    if module is not None:
        module.close()
