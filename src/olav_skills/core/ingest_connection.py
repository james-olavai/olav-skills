"""Administrator-configured ingest connection; never accepts a request DSN."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path



class IngestConnectionError(ValueError):
    """Fixed, credential-free connection failure."""
    code = "INGEST_REQUEST_FAILED"


def _valid_memory_limit(value):
    if not isinstance(value, str) or not value.endswith("MB"):
        return False
    digits = value[:-2]
    return digits.isdigit() and 256 <= int(digits) <= 4096


def _within(parent, child):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _paths_overlap(a, b):
    return a == b or _within(a, b) or _within(b, a)


def _validate_temp_directory(temp_directory, data_path, extension_directory):
    if not isinstance(temp_directory, str) or not temp_directory:
        raise ValueError()
    path = Path(temp_directory)
    if not path.is_absolute():
        raise ValueError()
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError()
    # Symlink roots, traversal and alternate spellings must not pass; the
    # directory must already exist.
    if temp_directory != str(resolved) or not resolved.is_dir():
        raise ValueError()
    for other in (data_path, extension_directory):
        if _paths_overlap(resolved, Path(other).resolve(strict=False)):
            raise ValueError()


def _host_config():
    # Only an explicitly selected trusted project can enable a remote catalog.
    home = os.environ.get("OLAV_HOME")
    if not home:
        if os.environ.get("OLAV_STORAGE_MODE") == "v3":
            raise IngestConnectionError("explicit OLAV_HOME required for configured ingest")
        # Detect the same nearest project marker as native path discovery,
        # but never activate its remote mapping without explicit host scope.
        cwd = Path.cwd()
        project = next((p for p in (cwd, *cwd.parents) if (p / ".olav").is_dir()), cwd)
        if (project / ".olav/config/ingest-storage.json").exists():
            raise IngestConnectionError("explicit OLAV_HOME required for configured ingest")
        return None
    try:
        path = Path(home) / ".olav/config/ingest-storage.json"
        if not path.exists():
            if os.environ.get("OLAV_STORAGE_MODE") == "v3":
                raise ValueError()
            return None
        config = json.loads(path.read_text())
        common = {"version", "backend", "data_path", "extension_directory", "threads", "memory_limit"}
        if not isinstance(config, dict) or type(config.get("version")) is not int:
            raise ValueError()
        if config["version"] == 1:
            fields = common | {"catalog_uri"}
            if not fields <= set(config) <= fields | {"override_data_path", "temp_directory"}:
                raise ValueError()
            if not isinstance(config["catalog_uri"], str) or not config["catalog_uri"].startswith("ducklake:postgres:"):
                raise ValueError()
        elif config["version"] == 2:
            if not common <= set(config) <= common | {"writer", "reader", "override_data_path", "temp_directory"}:
                raise ValueError()
            if not (set(config) & {"writer", "reader"}):
                raise ValueError()
            for role, keys in (("writer", {"lock_dsn_file", "catalog_dsn_file"}), ("reader", {"catalog_dsn_file"})):
                if role in config:
                    if not isinstance(config[role], dict) or set(config[role]) != keys:
                        raise ValueError()
                    if not all(isinstance(v, str) and Path(v).is_absolute() for v in config[role].values()):
                        raise ValueError()
            if any(k.startswith("OLAV_WRITER_DOMAIN_") or k == "OLAV_WRITER_RUNTIME_SHA256" for k in os.environ):
                raise ValueError()
        else:
            raise ValueError()
        if os.environ.get("OLAV_STORAGE_MODE") == "v3" and config["version"] != 2:
            raise ValueError()
        if type(config.get("override_data_path", False)) is not bool or config["backend"] != "ducklake":
            raise ValueError()
        if not all(isinstance(config[k], str) and Path(config[k]).is_absolute() for k in ("data_path", "extension_directory")):
            raise ValueError()
        if type(config["threads"]) is not int or config["threads"] != 1:
            raise ValueError()
        if not _valid_memory_limit(config["memory_limit"]):
            raise ValueError()
        if config.get("temp_directory") is not None:
            _validate_temp_directory(config["temp_directory"], config["data_path"], config["extension_directory"])
        return config
    except Exception:
        raise IngestConnectionError("invalid host ingest configuration") from None


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@contextmanager
def open_ingest_connection(*, native_path=None):
    from olav_skills.core.writer_host import (configured_ingest_config,
        assert_configured_writer_domain, has_domain_intent)
    # A configured factory cannot authenticate/acquire in place of a lease.
    if _host_config() is not None or has_domain_intent():
        assert_configured_writer_domain()
    config = configured_ingest_config()
    if config is None:
        from olav_skills.core.db_write import open_write_connection
        from olav_skills.core.config import MAIN_DB_PATH
        with open_write_connection(native_path or os.environ.get("OLAV_DB_PATH") or MAIN_DB_PATH) as conn:
            yield conn
        return
    if native_path is not None or os.environ.get("OLAV_DB_PATH"):
        raise IngestConnectionError("database override forbidden for configured ingest")
    assert_configured_writer_domain()
    if config["version"] == 2:
        from olav_skills.core.pg_writer import catalog_uri
        config = dict(config, catalog_uri=catalog_uri())
    conn = None
    try:
        import duckdb
        assert_configured_writer_domain()
        duckdb_config = {
            "allow_unsigned_extensions": False,
            **({"async_threads": 1} if config["version"] == 2 else {}),
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "extension_directory": config["extension_directory"],
            "threads": config["threads"], "memory_limit": config["memory_limit"],
        }
        if config.get("temp_directory"):
            duckdb_config["temp_directory"] = config["temp_directory"]
        conn = duckdb.connect(":memory:", config=duckdb_config)
        assert_configured_writer_domain()
        conn.execute("LOAD postgres")
        assert_configured_writer_domain()
        conn.execute("LOAD ducklake")
        assert_configured_writer_domain()
        conn.execute(
            "ATTACH " + _literal(config["catalog_uri"]) + " AS ingest_lake (DATA_PATH " + _literal(config["data_path"]) +
            (", OVERRIDE_DATA_PATH true, CREATE_IF_NOT_EXISTS false" if config.get("override_data_path", False) else "") + ")"
        )
        conn.execute("USE ingest_lake")
        assert_configured_writer_domain()
    except Exception as exc:
        if conn is not None:
            conn.close()
        if config["version"] == 2:
            from olav_skills.core.pg_writer import capacity_error, PgWriterError
            if capacity_error(exc):
                raise PgWriterError("CONCURRENCY_LIMIT") from None
        raise IngestConnectionError("configured ingest connection unavailable") from None
    try:
        yield conn
    finally:
        conn.close()
