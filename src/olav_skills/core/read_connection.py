"""Host-only DuckLake reader. This is a storage boundary, not a SQL sandbox."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# Deliberate private, read-only reuse; do not change the proven writer lifecycle.
from olav_skills.core.ingest_connection import IngestConnectionError, _host_config, _literal


class ReadConnectionError(ValueError):
    code = "WAREHOUSE_UNAVAILABLE"


def read_host_config():
    try:
        home = os.environ.get("OLAV_HOME")
        if home:
            selected = Path(home)
        else:
            cwd = Path.cwd()
            selected = next((p for p in (cwd, *cwd.parents) if (p / ".olav").is_dir()), cwd)
        storage = selected / ".olav/config/ingest-storage.json"
        if os.path.lexists(storage) and (not home or not storage.is_file()):
            raise ValueError()
        config = _host_config()
        if config is None:
            return None
        if os.environ.get("OLAV_DB_PATH"):
            raise ValueError()
        for key in ("data_path", "extension_directory"):
            path = Path(config[key])
            # No relative paths, traversal, alternate spelling or symlink roots.
            if not path.is_absolute() or config[key] != str(path.resolve(strict=True)) or not path.is_dir():
                raise ValueError()
        if config["version"] == 2:
            from olav_skills.core.pg_writer import reader_catalog_uri
            config = dict(config, catalog_uri=reader_catalog_uri(config))
        return config
    except (IngestConnectionError, OSError, ValueError, RuntimeError):
        raise ReadConnectionError("configured reader unavailable") from None


def _metadata_paths(config):
    """Exact lazy metadata capabilities derived only from the fixed host URI.

    DuckLake/Postgres automatically registers both descriptor spellings plus
    three WAL suffixes, including during locked read transactions. These are
    file capabilities, not an OS sandbox; never expose their credential text.
    """
    uri = config["catalog_uri"]
    descriptors = (uri[len("ducklake:"):], uri[len("ducklake:postgres:"):])
    return frozenset(str(Path(str(Path.cwd() / descriptor) + suffix).resolve())
                     for descriptor in descriptors
                     for suffix in ("", ".wal", ".wal.recovery", ".wal.checkpoint"))


def _normalized_paths(paths):
    """Resolve engine-reported paths the same way host config is vetted.

    DuckDB reports directories in allowed_directories with a trailing slash,
    so plain string equality is wrong; compare resolved, order-independent
    sets instead.
    """
    return frozenset(str(Path(p).resolve()) for p in paths)


def _expected_allowed_directories(config):
    """Exact directories DuckDB may grant a spill-configured read session.

    DuckDB automatically appends the configured temp_directory to
    allowed_directories: after SET allowed_directories=['/lake/b'] a real
    engine returns ['/lake/b/', '/work/duckdb-tmp/']. The expected set is
    therefore {data_path} plus, only when temp_directory is configured and
    non-empty, {temp_directory}. This stays a strict equality -- any extra
    directory (including the default cwd/.tmp) still fails.
    """
    expected = {str(Path(config["data_path"]).resolve())}
    temp_directory = config.get("temp_directory", "")
    if temp_directory:
        expected.add(str(Path(temp_directory).resolve()))
    return expected


@dataclass(frozen=True)
class ReadSession:
    connection: object
    read_version: int


@contextmanager
def open_read_connection():
    """Own a fresh connection and one snapshot transaction; never fall back."""
    config = read_host_config()
    if config is None:
        raise ReadConnectionError("configured reader unavailable")
    conn = None
    temp_directory = config.get("temp_directory", "")
    try:
        import duckdb
        conn = duckdb.connect(":memory:", config={
            "allow_unsigned_extensions": False,
            **({"async_threads": 1} if config["version"] == 2 else {}),
            "autoinstall_known_extensions": False, "autoload_known_extensions": False,
            "extension_directory": config["extension_directory"],
            "threads": 1, "memory_limit": config.get("memory_limit", "256MB"),
            # Spill to the validated host temp directory when configured;
            # otherwise keep spill disabled so DuckDB never grants the default
            # cwd/.tmp directory file access.
            "temp_directory": temp_directory,
        })
        conn.execute("LOAD postgres")
        conn.execute("LOAD ducklake")
        conn.execute("ATTACH " + _literal(config["catalog_uri"]) +
                     " AS reader (READ_ONLY, DATA_PATH " + _literal(config["data_path"]) +
                     (", OVERRIDE_DATA_PATH true, CREATE_IF_NOT_EXISTS false" if config.get("override_data_path", False) else "") + ")")
        conn.execute("SET search_path=''")
        conn.execute("USE reader")
        conn.execute("SET allowed_directories=[" + _literal(config["data_path"]) + "]")
        conn.execute("SET enable_external_access=false")
        conn.execute("SET lock_configuration=true")
        permissions = conn.execute("SELECT system.main.current_setting('temp_directory'), "
                                   "system.main.current_setting('allowed_directories')").fetchone()
        if permissions[0] != temp_directory or _normalized_paths(permissions[1]) != _expected_allowed_directories(config):
            raise ValueError()
        conn.execute("BEGIN TRANSACTION")
        identity = conn.execute("SELECT system.main.current_database(), system.main.current_schema(), "
                                "system.main.current_setting('search_path')").fetchone()
        if identity != ("reader", "main", "reader.main"):
            raise ValueError()
        # Extension-provided current transaction snapshot, not MAX(snapshot_id).
        # Reject a host macro replacing its name before invoking this trusted entry.
        shadows = conn.execute("SELECT function_name FROM system.main.duckdb_functions() "
                               "WHERE (internal IS DISTINCT FROM true) AND "
                               "system.main.lower(function_name) IN ('current_snapshot','ducklake_current_snapshot')").fetchall()
        if shadows:
            raise ValueError()
        version = conn.execute("SELECT * FROM system.main.ducklake_current_snapshot('reader')").fetchall()
        if len(version) != 1 or len(version[0]) != 1 or type(version[0][0]) is not int or version[0][0] < 0:
            raise ValueError()
        # The catalog lazily registers its exact descriptor/WAL capabilities.
        # They cannot be persistently removed in this engine version. Permit
        # only the host-derived set, plus the single data directory, never an
        # arbitrary extra path or the default cwd/.tmp directory.
        # allowed_paths gates per-file capabilities and, unlike
        # allowed_directories, DuckDB does not auto-append temp_directory to
        # it (real engine observed: only the catalog descriptor paths). The
        # file-capability set therefore stays exactly _metadata_paths(config).
        capabilities = conn.execute("SELECT system.main.current_setting('temp_directory'), "
                                    "system.main.current_setting('allowed_directories'), "
                                    "system.main.current_setting('allowed_paths')").fetchone()
        expected_paths = _metadata_paths(config)
        if (capabilities[0] != temp_directory or
                _normalized_paths(capabilities[1]) != _expected_allowed_directories(config) or
                frozenset(capabilities[2]) != expected_paths):
            raise ValueError()
        session = ReadSession(conn, version[0][0])
    except BaseException as exc:
        if conn is not None:
            conn.close()
        if not isinstance(exc, Exception):
            raise
        if config["version"] == 2:
            from olav_skills.core.pg_writer import capacity_error
            if capacity_error(exc):
                error = ReadConnectionError("configured reader capacity exceeded")
                error.code = "CONCURRENCY_LIMIT"
                raise error from None
        raise ReadConnectionError("configured reader unavailable") from None
    try:
        yield session
    finally:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
