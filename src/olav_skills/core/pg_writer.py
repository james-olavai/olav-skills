"""PostgreSQL session lock and acquire tag for the v3 storage backend."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import os
import threading
import uuid

K_CLASS = 1330397526
K_OBJ = 114
_LOCK_SQL = """SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory'
 AND granted AND mode='ExclusiveLock' AND pid=pg_backend_pid()
 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
 AND classid=%s AND objid=%s AND objsubid=2)"""
_local = threading.local()


class PgWriterError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class PgLease:
    application_name: str
    connection: object
    pid: int
    thread: int


def capacity_error(exc):
    # libpq handshake failures may omit SQLSTATE even though PostgreSQL sent
    # FATAL 53300. Match only PostgreSQL's finite capacity diagnostics.
    return getattr(exc, 'sqlstate', None) == '53300' or any(
        message in str(exc).lower() for message in (
            'too many connections for role', 'too many clients already',
            'remaining connection slots are reserved'))


def _error(exc, default='WRITER_UNAVAILABLE'):
    code = 'CONCURRENCY_LIMIT' if capacity_error(exc) else default
    for candidate in ('FENCE_INCOMPLETE', 'FENCE_FORBIDDEN', 'LOCK_NOT_HELD'):
        if candidate in str(exc):
            code = candidate
    return PgWriterError(code)


def _config():
    from olav_skills.core.ingest_connection import _host_config
    config = _host_config()
    if not config or config.get('version') != 2 or 'writer' not in config or os.environ.get('OLAV_DB_PATH'):
        raise PgWriterError('HOST_BINDING_INVALID')
    return config


def _dsn(path):
    try:
        p = Path(path)
        if not p.is_absolute() or not p.is_file() or p.stat().st_size > 16384:
            raise ValueError()
        value = p.read_text().strip()
        if not value or '\x00' in value:
            raise ValueError()
        return value
    except Exception:
        raise PgWriterError('HOST_BINDING_INVALID') from None


def active_lease():
    lease = getattr(_local, 'lease', None)
    return lease if lease and (lease.pid, lease.thread) == (os.getpid(), threading.get_ident()) else None


def assert_held(lease=None):
    lease = lease or active_lease()
    if lease is None or lease is not active_lease():
        raise PgWriterError('LOCK_NOT_HELD')
    try:
        row = lease.connection.execute(_LOCK_SQL, (K_CLASS, K_OBJ)).fetchone()
        app = lease.connection.execute("SELECT current_setting('application_name')").fetchone()[0]
        if not row or row[0] is not True or app != lease.application_name:
            raise PgWriterError('LOCK_NOT_HELD')
    except PgWriterError:
        raise
    except Exception:
        raise PgWriterError('LOCK_NOT_HELD') from None
    return lease


def fence(lease=None):
    lease = assert_held(lease)
    try:
        lease.connection.execute('SELECT olav_guard.olav_fence()')
    except Exception as exc:
        raise _error(exc, 'FENCE_INCOMPLETE') from None


@contextmanager
def acquire(warehouse=None, action=None):
    """Fail immediately on contention; closing the session releases the lock."""
    if active_lease() is not None:
        raise PgWriterError('CONCURRENCY_LIMIT')
    config = _config()
    conn = None
    try:
        import psycopg
        tag = 'olav-' + uuid.uuid4().hex
        conn = psycopg.connect(_dsn(config['writer']['lock_dsn_file']), application_name=tag,
                               autocommit=True, connect_timeout=5)
        if conn.execute('SELECT pg_try_advisory_lock(%s,%s)', (K_CLASS, K_OBJ)).fetchone()[0] is not True:
            raise PgWriterError('CONCURRENCY_LIMIT')
        lease = PgLease(tag, conn, os.getpid(), threading.get_ident())
        _local.lease = lease
        fence(lease)
    except PgWriterError:
        if conn is not None:
            conn.close()
        _local.lease = None
        raise
    except Exception as exc:
        if conn is not None:
            conn.close()
        _local.lease = None
        raise _error(exc) from None
    try:
        yield lease
    finally:
        _local.lease = None
        conn.close()


def catalog_uri():
    from psycopg.conninfo import make_conninfo
    lease = assert_held()
    return 'ducklake:postgres:' + make_conninfo(_dsn(_config()['writer']['catalog_dsn_file']),
                                               application_name=lease.application_name)


def reader_catalog_uri(config):
    try:
        from psycopg.conninfo import make_conninfo
        return 'ducklake:postgres:' + make_conninfo(_dsn(config['reader']['catalog_dsn_file']),
                                                   application_name='olav-reader')
    except Exception:
        raise PgWriterError('HOST_BINDING_INVALID') from None
