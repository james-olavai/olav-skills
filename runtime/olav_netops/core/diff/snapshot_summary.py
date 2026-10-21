"""Bounded cross-snapshot diff for the netops tables.

The legacy ``diff_snapshots`` scripts compared *identity key projections* with
DuckDB ``EXCEPT`` and then fetched every differing key into Python, slicing only
after the fetch.  This module keeps the exact projection/``EXCEPT`` set
semantics but moves both the count and the sample into SQL:

* every count is ``COUNT(*)`` over the original-key ``EXCEPT`` result;
* every sample is a stable ``ORDER BY ... ASC NULLS LAST LIMIT`` query;
* at most ``DEFAULT_SAMPLE_LIMIT`` sample rows per direction reach Python;
* key text is clipped only in the sample projection, never in the set
  operation or count;
* the sample list has an explicit UTF-8 JSON byte budget, and the complete
  response has a hard byte budget.

Cost note: a table needs four ``EXCEPT`` evaluations (added count, added
sample, removed count, removed sample) instead of the legacy two.  The count is
an in-DuckDB aggregate and the sample is an ordered top-N; no temporary table
or Python-side full materialisation is created.  A full scan for the aggregate
is not the same thing as a full fetch.

Resource/error contract mirrors the lightweight OW-043 rules: read-only
DuckDB, external access disabled, a bounded memory/thread configuration and a
query watchdog.  Missing source, corrupt source, SQL errors, timeouts and
budget failures are typed and never collapse to an empty diff or ``missing``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import duckdb

# Registry order is semantically significant for ``latest`` fallback: parsed
# outputs is richest, then the remaining tables.  Keys and device columns are
# the legacy contract and must not drift.
TABLES: dict[str, dict[str, Any]] = {
    "parsed_outputs": {
        "fqname": "netops.parsed_outputs",
        "key_cols": ("device_name", "command"),
        "device_col": "device_name",
    },
    "topology_links": {
        "fqname": "netops.topology_links",
        "key_cols": ("link_id",),
        "device_col": "source_device",
    },
    "raw_output_store": {
        "fqname": "netops.raw_output_store",
        "key_cols": ("device_name", "command"),
        "device_col": "device_name",
    },
    "oc_outputs": {
        "fqname": "netops.oc_outputs",
        "key_cols": ("device_name", "oc_module"),
        "device_col": "device_name",
    },
}

DEFAULT_SAMPLE_LIMIT = 50
#: A caller may lower the host sample cap but never raise it above the legacy
#: default.  The public scripts do not expose this as a model-controlled knob.
HARD_MAX_SAMPLE_LIMIT = 50

#: Server-side request bounds.  These are deliberately generous for real
#: snapshot identifiers/devices (the frozen fixture uses ~20 characters) while
#: preventing a multi-megabyte request value from entering SQL parameters or
#: being echoed back in an error.
MAX_SNAPSHOT_ID_CHARS = 1_024
MAX_DEVICE_CHARS = 1_024
MAX_TABLE_NAME_CHARS = 128

#: Characters clipped from one key cell in the *sample only*.  The full key is
#: still used for EXCEPT, COUNT and ordering.
MAX_KEY_CHARS = 256
#: UTF-8 JSON budget for one added/removed sample list, after escaping.
MAX_SAMPLE_BYTES = 48 * 1024
#: Hard budget for the complete response document (one newline is not counted).
MAX_RESPONSE_BYTES = 1_000_000

DEFAULT_TIMEOUT_MS = 15_000
HARD_MAX_TIMEOUT_MS = 60_000
# Same host environment names and hard ceilings as OW-043's sql_budget.
DEFAULT_MEMORY_LIMIT = "512MB"
DEFAULT_MEMORY_MB = 512
MAX_MEMORY_MB = 2_048
DEFAULT_THREADS = 2
MAX_THREADS = 8


class SnapshotDiffError(Exception):
    """Typed failure for the snapshot-diff entry points."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        stage: str | None = None,
        table: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.stage = stage
        self.table = table

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "error_type": self.error_type,
            "error": self.message[:4_000],
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.table:
            payload["table"] = self.table
        return payload


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _validation_error(message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error_type": "validation_error",
        "error": message[:4_000],
    }


def _validate_request(
    snapshot_id_1: Any,
    snapshot_id_2: Any,
    table_name: Any,
    device: Any,
) -> dict[str, Any] | None:
    """Reject unbounded or wrongly typed request values before any query.

    The legacy API accepted strings only.  Short values keep their exact
    meaning; an overlong value is a typed validation failure rather than an
    echoed response that can exceed the hard response budget.
    """
    for label, value in (
        ("snapshot_id_1", snapshot_id_1),
        ("snapshot_id_2", snapshot_id_2),
    ):
        if not isinstance(value, str):
            return _validation_error(f"{label} must be a string")
        if len(value) > MAX_SNAPSHOT_ID_CHARS:
            return _validation_error(
                f"{label} must be at most {MAX_SNAPSHOT_ID_CHARS} characters "
                f"(got {len(value)})"
            )
    if table_name is not None:
        if not isinstance(table_name, str):
            return _validation_error("table_name must be a string or null")
        if len(table_name) > MAX_TABLE_NAME_CHARS:
            return _validation_error(
                f"table_name must be at most {MAX_TABLE_NAME_CHARS} characters "
                f"(got {len(table_name)})"
            )
        if table_name not in TABLES:
            return _validation_error(
                f"unknown table_name={table_name!r}; choose from {sorted(TABLES)}"
            )
    if device is not None:
        if not isinstance(device, str):
            return _validation_error("device must be a string or null")
        if len(device) > MAX_DEVICE_CHARS:
            return _validation_error(
                f"device must be at most {MAX_DEVICE_CHARS} characters "
                f"(got {len(device)})"
            )
    return None


def finalize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Apply the complete-response contract to every success/error branch.

    This is not only a serializer check: callers receive a dict that already
    obeys the hard response budget.  An over-budget result becomes the fixed
    compact ``budget_exceeded`` error, so neither a tool nor a CLI can turn a
    large error echo into an oversized success.
    """
    try:
        text = json.dumps(
            result, ensure_ascii=False, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        return {
            "status": "error",
            "error_type": "validation_error",
            "error": f"result is not JSON serializable: {exc}"[:4_000],
        }
    if len(text.encode("utf-8")) <= MAX_RESPONSE_BYTES:
        return result
    return {
        "status": "error",
        "error_type": "budget_exceeded",
        "error": (
            f"response exceeded the {MAX_RESPONSE_BYTES}-byte hard budget; "
            "narrow by table_name/device or use execute_sql"
        ),
    }


def _json_bytes(value: Any) -> int:
    """UTF-8 bytes of the exact JSON wire representation used for samples."""
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _clamp_sample_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_SAMPLE_LIMIT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_LIMIT
    return max(1, min(HARD_MAX_SAMPLE_LIMIT, parsed))


def _resolve_timeout_ms(value: int | None = None) -> int:
    if value is None:
        env_value = os.environ.get("OLAV_DIFF_TIMEOUT_MS")
        if env_value:
            try:
                value = int(env_value)
            except ValueError:
                value = None
    if value is None:
        return DEFAULT_TIMEOUT_MS
    return max(1, min(HARD_MAX_TIMEOUT_MS, int(value)))


def _parse_memory_mb(value: str | None) -> int:
    """Parse the OW-043 env spelling (``512MB``, ``2GB``, bare MB)."""
    if not value:
        return DEFAULT_MEMORY_MB
    text = value.strip().upper()
    factor = 1
    if text.endswith("GB"):
        factor, text = 1024, text[:-2]
    elif text.endswith("MB"):
        text = text[:-2]
    try:
        return max(1, min(int(float(text) * factor), MAX_MEMORY_MB))
    except (TypeError, ValueError):
        return DEFAULT_MEMORY_MB


def _resolve_threads(value: str | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_THREADS
    except (TypeError, ValueError):
        parsed = DEFAULT_THREADS
    return max(1, min(parsed, MAX_THREADS))


def _resolve_connect_config() -> dict[str, str]:
    """Host-env resource limits, matching OW-043's names and ceilings."""
    return {
        "enable_external_access": "false",
        "memory_limit": f"{_parse_memory_mb(os.environ.get('OLAV_SQL_MEMORY_LIMIT'))}MB",
        "threads": str(_resolve_threads(os.environ.get("OLAV_SQL_THREADS"))),
    }


def _classify_db_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "out of memory" in text or "memory limit" in text:
        return "budget_exceeded"
    source_markers = (
        "not a valid duckdb database",
        "not a valid database",
        "could not open",
        "cannot open",
        "unable to open",
        "io error",
        "permission denied",
        "no such file",
        "database is locked",
        "conflicting lock",
    )
    if any(marker in text for marker in source_markers):
        return "source_error"
    return "sql_error"


def _run_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    action: Callable[[], Any],
    *,
    timeout_ms: int,
    stage: str,
    table: str | None = None,
) -> Any:
    """Run one DuckDB action with ``conn.interrupt()`` as the watchdog."""
    timed_out = False

    def _interrupt() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            conn.interrupt()
        except Exception:
            # Best-effort watchdog; the original exception still carries the
            # failure if interrupt itself cannot be delivered.
            pass

    timer = threading.Timer(max(timeout_ms, 1) / 1000.0, _interrupt)
    timer.daemon = True
    timer.start()
    try:
        result = action()
    except SnapshotDiffError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to the typed contract
        if timed_out:
            raise SnapshotDiffError(
                "timeout",
                f"snapshot diff exceeded the {timeout_ms} ms budget during {stage}",
                stage=stage,
                table=table,
            ) from exc
        raise SnapshotDiffError(
            _classify_db_error(exc),
            f"snapshot diff {stage} failed: {exc}",
            stage=stage,
            table=table,
        ) from exc
    finally:
        timer.cancel()
    if timed_out:
        raise SnapshotDiffError(
            "timeout",
            f"snapshot diff exceeded the {timeout_ms} ms budget during {stage}",
            stage=stage,
            table=table,
        )
    return result


def table_exists(
    conn: duckdb.DuckDBPyConnection,
    fqname: str,
    *,
    timeout_ms: int,
) -> bool:
    schema, name = fqname.split(".", 1)

    def _query() -> Any:
        return conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ? LIMIT 1",
            [schema, name],
        ).fetchone()

    return _run_with_timeout(
        conn, _query, timeout_ms=timeout_ms, stage="table_exists"
    ) is not None


def resolve_snapshot_id(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    *,
    timeout_ms: int,
    tables: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """Resolve ``latest`` using the legacy per-table priority.

    ``parsed_outputs`` is consulted first; the other tables are fallbacks only
    when parsed is absent or has no non-NULL snapshot.  This deliberately is
    not a cross-table global MAX().  A real SQL failure while reading an
    existing table is an error, not a silent fallback.
    """
    if sid and sid.lower() != "latest":
        return sid

    registry = tables or TABLES
    for meta in registry.values():
        fq = meta["fqname"]
        if not table_exists(conn, fq, timeout_ms=timeout_ms):
            continue

        # One statement both computes the legacy MAX(snapshot_id) and applies
        # the bounded projection to that single result.  This is deliberately
        # *not* a length check over every row: a long older/lower id must not
        # invalidate a normal short MAX.  The CASE returns either the exact
        # MAX value or NULL -- never a substr() that could be mistaken for a
        # real snapshot id.
        def _query(fq: str = fq) -> Any:
            return conn.execute(
                "WITH __ow044_latest AS ("
                f"SELECT MAX(snapshot_id) AS snapshot_id FROM {fq} "
                "WHERE snapshot_id IS NOT NULL"
                ") SELECT length(CAST(snapshot_id AS VARCHAR)), "
                "CASE WHEN length(CAST(snapshot_id AS VARCHAR)) <= ? "
                "THEN snapshot_id ELSE NULL END"
                " FROM __ow044_latest",
                [MAX_SNAPSHOT_ID_CHARS],
            ).fetchone()

        row = _run_with_timeout(
            conn, _query, timeout_ms=timeout_ms, stage="resolve_latest", table=fq
        )
        if not row or row[0] is None:
            # No non-NULL snapshot in this table; keep the legacy fallback.
            continue
        max_length = int(row[0])
        if max_length > MAX_SNAPSHOT_ID_CHARS:
            raise SnapshotDiffError(
                "budget_exceeded",
                f"latest snapshot in {fq} has a snapshot_id of {max_length} "
                f"characters, above the {MAX_SNAPSHOT_ID_CHARS}-character "
                "bounded metadata limit; refusing to fetch the unbounded value",
                stage="resolve_latest",
                table=fq,
            )
        value = row[1]
        if not isinstance(value, str):
            raise SnapshotDiffError(
                "sql_error",
                f"latest snapshot in {fq} did not resolve to a string",
                stage="resolve_latest",
                table=fq,
            )
        if not value:
            # Legacy contract: ``if row and row[0]`` skipped an empty-string
            # MAX and continued to the next table.  Keep MAX() semantics and
            # skip this table; do not search for its greatest non-empty value.
            continue
        return value
    return None


def _where_clause(device_col: str | None, device: str | None) -> tuple[str, list[Any]]:
    # Legacy contract: an empty string means "no device filter", unlike an
    # explicit device name.  Preserve that instead of turning "" into a match
    # against an empty device name.
    if not device:
        return "", []
    return f" AND {_quote_ident(device_col or '')} = ?", [device]


def _diff_projection(
    fqname: str,
    key_cols: Sequence[str],
    device_col: str,
    device: str | None,
) -> tuple[str, list[Any]]:
    key_expr = ", ".join(_quote_ident(col) for col in key_cols)
    device_clause, device_params = _where_clause(device_col, device)
    sql = (
        f"SELECT {key_expr} FROM {fqname} "
        f"WHERE snapshot_id = ?{device_clause} "
        f"EXCEPT "
        f"SELECT {key_expr} FROM {fqname} "
        f"WHERE snapshot_id = ?{device_clause}"
    )
    return sql, device_params


def _sample_projection(key_cols: Sequence[str]) -> str:
    """Return an any-key-clipped flag expression for the sample projection.

    The truncation itself lives in the outer SELECT, after ``EXCEPT`` has
    already deduplicated full keys.  Two overlong keys that share a clipped
    prefix therefore remain two rows in the count and in the sample.
    """
    trunc_flags: list[str] = []
    for col in key_cols:
        q = _quote_ident(col)
        trunc_flags.append(
            f"({q} IS NOT NULL AND "
            f"length(CAST({q} AS VARCHAR)) > {MAX_KEY_CHARS})"
        )
    return (
        "CASE WHEN " + " OR ".join(trunc_flags) + ' THEN TRUE ELSE FALSE END '
        'AS "__ow044_key_truncated"'
    )


def _fit_sample(
    rows: list[dict[str, Any]],
    truncated: list[bool],
    *,
    byte_budget: int,
) -> tuple[list[dict[str, Any]], int, bool, bool]:
    """Keep a stable prefix of *rows* that fits the JSON byte budget."""
    kept: list[dict[str, Any]] = []
    kept_truncated = False
    dropped = False
    for row, is_truncated in zip(rows, truncated):
        candidate = kept + [row]
        if _json_bytes(candidate) > byte_budget:
            dropped = True
            break
        kept.append(row)
        kept_truncated = kept_truncated or is_truncated
    return kept, _json_bytes(kept), kept_truncated, dropped


def _count_and_sample(
    conn: duckdb.DuckDBPyConnection,
    *,
    fqname: str,
    key_cols: Sequence[str],
    device_col: str,
    sid1: str,
    sid2: str,
    device: str | None,
    sample_limit: int,
    timeout_ms: int,
    table: str,
) -> tuple[int, list[dict[str, Any]], int, bool, bool]:
    """Return count, sample, sample bytes, truncated flag, has_more."""
    diff_sql, device_params = _diff_projection(
        fqname, key_cols, device_col, device
    )
    params = [sid2] + device_params + [sid1] + device_params

    count_sql = f"SELECT count(*) FROM ({diff_sql}) AS __ow044_diff"

    def _count() -> Any:
        return conn.execute(count_sql, params).fetchone()

    count_row = _run_with_timeout(
        conn, _count, timeout_ms=timeout_ms, stage="count", table=table
    )
    exact_count = int(count_row[0]) if count_row else 0

    ordering = ", ".join(
        f"{_quote_ident(col)} ASC NULLS LAST" for col in key_cols
    )
    flag_expr = _sample_projection(key_cols)
    # The outer ORDER BY names the *sampled* original key columns, not the
    # clipped output aliases, so the final row order is explicitly stable at
    # the last SELECT rather than relying on subquery ordering.
    clustered = ", ".join(
        f"CASE WHEN {_quote_ident(col)} IS NULL THEN NULL "
        f"WHEN length(CAST({_quote_ident(col)} AS VARCHAR)) > {MAX_KEY_CHARS} "
        f"THEN substr(CAST({_quote_ident(col)} AS VARCHAR), 1, {MAX_KEY_CHARS}) "
        f"|| '…' ELSE CAST({_quote_ident(col)} AS VARCHAR) END AS __ow044_key_{i}"
        for i, col in enumerate(key_cols)
    )
    sample_sql = (
        f"WITH __ow044_diff AS ({diff_sql}), "
        f"__ow044_sampled AS ("
        f"SELECT * FROM __ow044_diff ORDER BY {ordering} LIMIT ?"
        f") "
        f"SELECT {clustered}, {flag_expr} FROM __ow044_sampled "
        f"ORDER BY {ordering}"
    )
    sample_params = params + [sample_limit]

    def _sample() -> list[tuple[Any, ...]]:
        cur = conn.execute(sample_sql, sample_params)
        return cur.fetchmany(sample_limit)

    rows = _run_with_timeout(
        conn, _sample, timeout_ms=timeout_ms, stage="sample", table=table
    )
    samples = [
        dict(zip(key_cols, row[:-1]))
        for row in rows
    ]
    flags = [bool(row[-1]) for row in rows]
    kept, sample_bytes, truncated, dropped = _fit_sample(
        samples, flags, byte_budget=MAX_SAMPLE_BYTES
    )
    has_more = exact_count > len(kept)
    return exact_count, kept, sample_bytes, truncated or dropped, has_more


def diff_one_table(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    meta: dict[str, Any],
    sid1: str,
    sid2: str,
    device: str | None = None,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Diff one table while preserving the legacy missing-table behaviour."""
    timeout_ms = _resolve_timeout_ms(timeout_ms)
    sample_limit = _clamp_sample_limit(sample_limit)
    fq = meta["fqname"]
    key_cols = tuple(meta["key_cols"])
    device_col = meta["device_col"]

    if not table_exists(conn, fq, timeout_ms=timeout_ms):
        return {
            "status": "missing",
            "added": [],
            "removed": [],
            "added_count": 0,
            "removed_count": 0,
        }

    added_count, added, added_bytes, added_trunc, added_more = _count_and_sample(
        conn,
        fqname=fq,
        key_cols=key_cols,
        device_col=device_col,
        sid1=sid1,
        sid2=sid2,
        device=device,
        sample_limit=sample_limit,
        timeout_ms=timeout_ms,
        table=table,
    )
    removed_count, removed, removed_bytes, removed_trunc, removed_more = (
        _count_and_sample(
            conn,
            fqname=fq,
            key_cols=key_cols,
            device_col=device_col,
            sid1=sid2,
            sid2=sid1,
            device=device,
            sample_limit=sample_limit,
            timeout_ms=timeout_ms,
            table=table,
        )
    )

    return {
        "status": "ok",
        "key_cols": list(key_cols),
        "added_count": added_count,
        "removed_count": removed_count,
        "added": added,
        "removed": removed,
        # Appended completeness metadata; existing fields keep their meaning.
        "added_truncated": added_trunc,
        "removed_truncated": removed_trunc,
        "added_has_more": added_more,
        "removed_has_more": removed_more,
        "added_sample_bytes": added_bytes,
        "removed_sample_bytes": removed_bytes,
        "sample_limit": sample_limit,
        "sample_key_chars": MAX_KEY_CHARS,
        "sample_byte_budget": MAX_SAMPLE_BYTES,
    }


def diff_snapshots_summary(
    db_path: str | Path,
    snapshot_id_1: str,
    snapshot_id_2: str,
    table_name: str | None = None,
    device: str | None = None,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Open the database read-only and return the bounded diff summary.

    Every success and error return goes through :func:`finalize_result`, so the
    function, @tool and CLI share one hard response contract.
    """
    return finalize_result(
        _diff_snapshots_summary_impl(
            db_path,
            snapshot_id_1,
            snapshot_id_2,
            table_name,
            device,
            sample_limit=sample_limit,
            timeout_ms=timeout_ms,
        )
    )


def _diff_snapshots_summary_impl(
    db_path: str | Path,
    snapshot_id_1: str,
    snapshot_id_2: str,
    table_name: str | None = None,
    device: str | None = None,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    validated = _validate_request(snapshot_id_1, snapshot_id_2, table_name, device)
    if validated is not None:
        return validated

    path = Path(db_path)
    if not path.exists():
        return {
            "status": "error",
            "error_type": "source_missing",
            "error": f"main.duckdb not found at {path}",
        }

    timeout_ms = _resolve_timeout_ms(timeout_ms)
    sample_limit = _clamp_sample_limit(sample_limit)
    tables = [table_name] if table_name else list(TABLES)

    try:
        conn = duckdb.connect(
            str(path), read_only=True, config=_resolve_connect_config()
        )
    except Exception as exc:  # noqa: BLE001 - typed source/resource failure
        error_type = _classify_db_error(exc)
        if error_type == "sql_error":
            error_type = "source_error"
        return {
            "status": "error",
            "error_type": error_type,
            "error": f"could not open database read-only: {exc}"[:4_000],
            "stage": "connect",
        }

    try:
        sid1 = resolve_snapshot_id(conn, snapshot_id_1, timeout_ms=timeout_ms)
        sid2 = resolve_snapshot_id(conn, snapshot_id_2, timeout_ms=timeout_ms)
        if sid1 is None or sid2 is None:
            return {
                "status": "error",
                "error_type": "source_missing",
                "error": "no snapshots available",
                "stage": "resolve_latest",
            }

        results: dict[str, Any] = {}
        total_added = total_removed = 0
        for tbl in tables:
            meta = TABLES[tbl]
            result = diff_one_table(
                conn,
                tbl,
                meta,
                sid1,
                sid2,
                device,
                sample_limit=sample_limit,
                timeout_ms=timeout_ms,
            )
            results[tbl] = result
            if result["status"] == "ok":
                total_added += result["added_count"]
                total_removed += result["removed_count"]
    except SnapshotDiffError as exc:
        payload = exc.as_dict()
        payload.update(
            {"snapshot_id_1": sid1, "snapshot_id_2": sid2}
            if "sid1" in locals() and "sid2" in locals()
            else {}
        )
        return payload
    except Exception as exc:  # noqa: BLE001 - final fail-closed mapping
        return {
            "status": "error",
            "error_type": _classify_db_error(exc),
            "error": f"snapshot diff failed: {exc}"[:4_000],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

    connect_config = _resolve_connect_config()
    return {
        "status": "success",
        "snapshot_id_1": sid1,
        "snapshot_id_2": sid2,
        "device": device,
        "tables": results,
        "total_added": total_added,
        "total_removed": total_removed,
        "bounds": {
            "sample_limit": sample_limit,
            "sample_key_chars": MAX_KEY_CHARS,
            "sample_byte_budget": MAX_SAMPLE_BYTES,
            "response_byte_budget": MAX_RESPONSE_BYTES,
            "timeout_ms": timeout_ms,
            "timeout_scope": "per DuckDB statement, not the whole diff",
            "memory_limit": connect_config["memory_limit"],
            "threads": int(connect_config["threads"]),
            "count_and_sample": "separate EXCEPT evaluations; no temp table",
        },
    }


def serialize_result(result: dict[str, Any]) -> str:
    """Serialize an already-finalized result under the hard byte budget.

    A response that cannot fit becomes a compact typed ``budget_exceeded``
    error rather than an over-budget success.  Counts are computed before this
    finalizer, so a failure here never turns a real diff into zero.  Callers
    that need the exit/error decision must use the returned wire status (the
    CLI parses this exact text) rather than the pre-finalization dict.
    """
    return json.dumps(
        finalize_result(result),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
