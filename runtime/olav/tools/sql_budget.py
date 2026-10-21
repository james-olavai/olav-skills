"""Bounded, read-only DuckDB execution shared by the SQL entry points.

This module is intentionally small and dependency-light: DuckDB plus the
standard library.  The workspace ``execute_sql`` script and @tool both import
it, and the skill-pack generator vendors it through the normal import closure.

The contract has separate, explicitly named budgets:

* request/SQL: the input SQL is hard-capped before it can be echoed.  A column
  list/name width and projected row width are validated from ``DESCRIBE`` before
  any result row is fetched.
* preview: at most ``max_rows + 1`` rows are fetched.  The extra row is used
  only to set ``has_more``; it is never returned.  A conservative fixed-width
  scalar whitelist passes through natively; every other type (text, BLOB,
  LIST, fixed/multidimensional ARRAY, STRUCT, MAP, UNION, BIT, VARINT, ENUM and
  unknown extension types) uses a SQL-side bounded shape: native only while its
  text rendering fits the cell cap, otherwise NULL plus a bounded helper text.
  This keeps small values (including BLOB bytes) on their existing wire path
  while ensuring an over-budget value never first crosses into Python whole.
* ARCH-16 tier context rows/cell caps are effective server caps at the LLM
  entry points; a model may lower a request but cannot raise the tier cap.
* full envelope: entry points additionally verify the actual UTF-8 size of the
  serialized response (SQL echo, columns, data, schema context, message, error)
  against ``max_total_bytes`` and the fixed ``HARD_MAX_RESPONSE_BYTES``.  A
  request too small for even the minimal metadata is rejected, never returned
  as an over-budget success.
* time: a watchdog calls ``connection.interrupt()`` when the wall-clock budget
  expires.  A timeout is an error, not an empty result.

``total`` stays ``None`` unless the complete result was actually observed.  For
``offset > 0`` the total is always unknown (``None``); ``returned_rows`` is the
page size.  ``fetched_bytes`` is the UTF-8 JSON-equivalent size of every fetched
field position (including duplicate aliases and internal helpers), not physical
I/O or host RSS.  A row/fetch limit is not a scan limit, and ``memory_limit`` is
a DuckDB setting, not a hard bound on Python or host RSS.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb

# Server-side ceilings.  A caller may ask for less, never more.
HARD_MAX_ROWS = 1_000
HARD_MAX_CELL_CHARS = 8_000
HARD_MAX_TOTAL_BYTES = 1_000_000
HARD_MAX_RESPONSE_BYTES = 1_000_000
HARD_MAX_TIMEOUT_MS = 60_000
HARD_MAX_OFFSET = 1_000_000
HARD_MAX_SQL_BYTES = 65_536
HARD_MAX_COLUMNS = 256
HARD_MAX_COLUMN_NAME_BYTES = 256
HARD_MAX_METADATA_BYTES = 65_536
HARD_MAX_ROW_BYTES = 524_288

DEFAULT_TOTAL_BYTES = 256 * 1024
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_MEMORY_LIMIT = "512MB"
DEFAULT_THREADS = 2
MAX_THREADS = 8
MAX_MEMORY_MB = 2_048

TRUNCATION_MARKER = "…[truncated]"

_EXTERNAL_TOKENS = re.compile(
    r"\b(?:"
    r"attach|detach|load|install|copy|export|import|pragma|call|"
    r"checkpoint|force_checkpoint|vacuum|"
    r"read_text|read_blob|read_csv|read_csv_auto|read_parquet|read_json|"
    r"read_json_auto|parquet_scan|csv_scan|json_scan|iceberg_scan|"
    r"delta_scan|sqlite_scan|postgres_scan|mysql_scan|glob|"
    r"query|query_table"
    r")\b",
    re.IGNORECASE,
)

_BLOCKED_WRITE_TOKENS = re.compile(
    r"\b(?:insert|update|delete|truncate|create|drop|alter|grant|revoke)\b",
    re.IGNORECASE,
)

_STRING_TYPES = re.compile(
    r"^\s*(?:VARCHAR|CHAR|TEXT|STRING|JSON)(?:\s*\(\d+\))?\s*$", re.IGNORECASE
)
_BLOB_TYPE = re.compile(r"^\s*BLOB(?:\s*\(\d+\))?\s*$", re.IGNORECASE)

# Fixed-width scalar whitelist.  These types have a bounded wire representation
# and can be moved through Python without a per-cell projection.  Everything
# else (strings, BLOB, LIST/ARRAY including fixed and multidimensional arrays,
# STRUCT/MAP/UNION, BIT/BIGNUM/VARINT/ENUM, and unknown extension types) goes
# through the SQL-side bounded path.  A whitelist is deliberately conservative:
# a future DuckDB variable-width type cannot silently become an unbounded fetch.
_FIXED_SCALAR_TYPES = re.compile(
    r"^\s*(?:"
    r"BOOLEAN|BOOL|"
    r"TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT|"
    r"UTINYINT|USMALLINT|UINTEGER|UBIGINT|UHUGEINT|"
    r"REAL|FLOAT|DOUBLE|"
    r"DECIMAL(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?|"
    r"DATE|TIME(?:\s+WITH\s+TIME\s+ZONE)?|"
    r"TIMESTAMP(?:\s*\(\s*\d+\s*\))?(?:\s+WITH\s+TIME\s+ZONE)?|"
    r"TIMESTAMP_S|TIMESTAMP_MS|TIMESTAMP_NS|INTERVAL|UUID|NULL|SQLNULL"
    r")\s*$",
    re.IGNORECASE,
)
_COMPLEX_TYPES = ("[]", "STRUCT(", "MAP(", "LIST(", "ARRAY(", "UNION(")


def utf8_json_size(payload: Any, *, indent: int | None = None) -> int:
    """Actual UTF-8 size of the JSON envelope the caller will emit."""
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            indent=indent,
        ).encode("utf-8")
    )


class SqlBudgetError(Exception):
    """A query failure with a machine-readable class.

    ``error_type`` is one of ``budget_exceeded``, ``sql_rejected``,
    ``source_missing``, ``source_error``, ``sql_error``, ``parameter_error``,
    ``timeout`` or ``connection_error``.  It is intentionally separate from a
    normal empty result and from a normal row-cap truncation.
    """

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        stage: str | None = None,
        elapsed_ms: float | None = None,
        partial_rows: list[dict[str, Any]] | None = None,
        fetched_rows: int = 0,
        fetched_bytes: int = 0,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.stage = stage
        self.elapsed_ms = elapsed_ms
        self.partial_rows = partial_rows or []
        self.fetched_rows = fetched_rows
        self.fetched_bytes = fetched_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": str(self),
            "stage": self.stage,
            "elapsed_ms": self.elapsed_ms,
            "fetched_rows": self.fetched_rows,
            "fetched_bytes": self.fetched_bytes,
            "partial_rows": self.partial_rows,
        }


@dataclass(frozen=True)
class SqlBudget:
    """Resolved limits for one query."""

    max_rows: int
    max_cell_chars: int
    max_total_bytes: int
    timeout_ms: int
    memory_limit: str
    threads: int
    offset: int = 0
    server_max_rows: int = HARD_MAX_ROWS
    server_max_cell_chars: int = HARD_MAX_CELL_CHARS

    @property
    def fetch_limit(self) -> int:
        return min(self.max_rows + 1, HARD_MAX_ROWS + 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_rows": self.max_rows,
            "max_cell_chars": self.max_cell_chars,
            "max_total_bytes": self.max_total_bytes,
            "timeout_ms": self.timeout_ms,
            "memory_limit": self.memory_limit,
            "threads": self.threads,
            "offset": self.offset,
            "fetch_limit": self.fetch_limit,
            "server_max_rows": self.server_max_rows,
            "server_max_cell_chars": self.server_max_cell_chars,
            "hard_max_rows": HARD_MAX_ROWS,
            "hard_max_cell_chars": HARD_MAX_CELL_CHARS,
            "hard_max_total_bytes": HARD_MAX_TOTAL_BYTES,
            "hard_max_response_bytes": HARD_MAX_RESPONSE_BYTES,
            "hard_max_sql_bytes": HARD_MAX_SQL_BYTES,
            "hard_max_columns": HARD_MAX_COLUMNS,
            "hard_max_column_name_bytes": HARD_MAX_COLUMN_NAME_BYTES,
            "hard_max_row_bytes": HARD_MAX_ROW_BYTES,
            "hard_max_timeout_ms": HARD_MAX_TIMEOUT_MS,
            "note": (
                "max_total_bytes bounds the preview and the complete serialized "
                "response; server_max_rows/server_max_cell_chars are the "
                "effective host/tier caps (hard_max_* are absolute); row/cell/"
                "byte limits bound Python fetch, not DuckDB scan work; "
                "fetched_bytes is the UTF-8 JSON-equivalent size of every "
                "fetched field including internal helpers, not physical I/O; "
                "memory_limit is not a Python/RSS hard cap"
            ),
        }


@dataclass
class BoundedQueryResult:
    """A bounded preview plus the facts needed to audit it."""

    rows: list[dict[str, Any]]
    columns: list[str]
    fetched_rows: int
    fetched_bytes: int
    returned_rows: int
    returned_bytes: int
    has_more: bool
    truncated: bool
    cell_truncated: bool
    budget_exceeded: bool
    total: int | None
    next_offset: int | None
    complex_columns: list[str]
    timing: dict[str, float]
    budget: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.rows,
            "complex_columns": self.complex_columns,
            "fetched_rows": self.fetched_rows,
            "fetched_bytes": self.fetched_bytes,
            "returned_rows": self.returned_rows,
            "returned_bytes": self.returned_bytes,
            "has_more": self.has_more,
            "truncated": self.truncated,
            "cell_truncated": self.cell_truncated,
            "budget_exceeded": self.budget_exceeded,
            "total": self.total,
            "next_offset": self.next_offset,
            "timing": self.timing,
            "budget": self.budget,
        }


def _clamp_int(value: Any, default: int, hard_max: int) -> int:
    if value is None:
        return max(1, min(default, hard_max))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(1, min(default, hard_max))
    return max(1, min(parsed, hard_max))


def _parse_memory_mb(value: str | None) -> int:
    """Parse an env memory limit and clamp it to the documented ceiling."""
    if not value:
        return 512
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)\s*", value, re.I)
    if not match:
        return 512
    amount, unit = float(match.group(1)), match.group(2).upper()
    factor = {"B": 1 / (1024 * 1024), "KB": 1 / 1024, "MB": 1, "GB": 1024}[unit]
    return max(1, min(int(amount * factor), MAX_MEMORY_MB))


def _resolve_threads(value: str | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_THREADS
    except (TypeError, ValueError):
        parsed = DEFAULT_THREADS
    return max(1, min(parsed, MAX_THREADS))


def resolve_budget(
    *,
    default_rows: int = 50,
    default_cell_chars: int = 2_000,
    overrides: dict[str, Any] | None = None,
    offset: int = 0,
    environ: dict[str, str] | None = None,
    row_cap: int | None = None,
    cell_cap: int | None = None,
) -> SqlBudget:
    """Resolve defaults, model-supplied reductions and host env limits.

    ``overrides`` may lower a limit but every value is clamped to the hard
    server ceiling.  ``row_cap``/``cell_cap`` are optional effective server
    caps (for example the host tier's ARCH-16 context budget).  The LLM entry
    points pass the tier cap here so a model can lower its request but cannot
    raise the existing tier limit; the shared non-LLM API leaves them unset
    and therefore keeps the global hard ceiling.  Memory/thread configuration
    comes from the host environment, never from the model.
    """
    overrides = overrides or {}
    env = os.environ if environ is None else environ
    memory_mb = _parse_memory_mb(env.get("OLAV_SQL_MEMORY_LIMIT"))
    threads = _resolve_threads(env.get("OLAV_SQL_THREADS"))
    effective_row_cap = max(
        1, min(HARD_MAX_ROWS, int(row_cap) if row_cap is not None else HARD_MAX_ROWS)
    )
    effective_cell_cap = max(
        1,
        min(
            HARD_MAX_CELL_CHARS,
            int(cell_cap) if cell_cap is not None else HARD_MAX_CELL_CHARS,
        ),
    )

    return SqlBudget(
        max_rows=_clamp_int(
            overrides.get("max_rows"),
            max(1, min(default_rows, effective_row_cap)),
            effective_row_cap,
        ),
        max_cell_chars=_clamp_int(
            overrides.get("max_cell_chars"),
            max(1, min(default_cell_chars, effective_cell_cap)),
            effective_cell_cap,
        ),
        max_total_bytes=_clamp_int(
            overrides.get("max_total_bytes"), DEFAULT_TOTAL_BYTES, HARD_MAX_TOTAL_BYTES
        ),
        timeout_ms=_clamp_int(
            overrides.get("timeout_ms"), DEFAULT_TIMEOUT_MS, HARD_MAX_TIMEOUT_MS
        ),
        memory_limit=f"{memory_mb}MB",
        threads=threads,
        offset=_clamp_int(offset, 0, HARD_MAX_OFFSET) if offset else 0,
        server_max_rows=effective_row_cap,
        server_max_cell_chars=effective_cell_cap,
    )


def _mask_quoted_and_comments(sql: str) -> str:
    """Replace string literals and comments with spaces for lexical checks."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        out.extend("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                out.append(" ")
                i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            out.extend("  ")
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                out.append(" ")
                i += 1
            if i + 1 < n:
                out.extend("  ")
                i += 2
            continue
        if ch == "$":
            match = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if match:
                tag = match.group(0)
                out.extend(" " * len(tag))
                i += len(tag)
                end = sql.find(tag, i)
                if end == -1:
                    out.extend(" " * (n - i))
                    i = n
                else:
                    out.extend(" " * (end - i))
                    i = end + len(tag)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def validate_readonly_sql(sql: str) -> None:
    """Reject multiple statements and SQL-level external/write escape hatches.

    ``read_only=True`` protects the database file, but it does not stop
    ``read_text``/``read_parquet``/``ATTACH``.  The connection also disables
    external access; this lexical gate is the second, explicit layer.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SqlBudgetError("sql_rejected", "empty SQL")

    masked = _mask_quoted_and_comments(sql).strip()
    parts = [p.strip() for p in masked.split(";")]
    nonempty = [p for p in parts if p]
    if len(nonempty) > 1:
        raise SqlBudgetError("sql_rejected", "multiple SQL statements are not allowed")

    if not re.match(r"^(?:WITH|SELECT)\b", masked, re.IGNORECASE):
        raise SqlBudgetError("sql_rejected", "only read-only SELECT/WITH SQL is allowed")

    match = _EXTERNAL_TOKENS.search(masked)
    if match:
        token = match.group(0)
        raise SqlBudgetError(
            "sql_rejected",
            f"SQL token '{token}' is not allowed on the read-only query path",
        )
    match = _BLOCKED_WRITE_TOKENS.search(masked)
    if match:
        token = match.group(0)
        raise SqlBudgetError(
            "sql_rejected",
            f"SQL token '{token}' is not allowed on a read-only query path",
        )


def validate_no_external_access(sql: str) -> None:
    """Reject file/remote escape hatches without demanding SELECT-only SQL."""
    masked = _mask_quoted_and_comments(sql).strip()
    parts = [p.strip() for p in masked.split(";") if p.strip()]
    if len(parts) > 1:
        raise SqlBudgetError("sql_rejected", "multiple SQL statements are not allowed")
    match = _EXTERNAL_TOKENS.search(masked)
    if match:
        raise SqlBudgetError(
            "sql_rejected",
            f"SQL token '{match.group(0)}' is not allowed on this query path",
        )


def _strip_trailing_semicolons(sql: str) -> str:
    """Remove trailing statement separators without touching quoted text.

    A single statement is allowed to end in ``;`` (and possibly a trailing
    comment).  Wrapping that text in ``FROM (...)`` is a syntax error, so the
    separator is removed by position from the original string, never by a
    naive ``split(';')`` that would corrupt literals or comments.
    """
    while True:
        masked = _mask_quoted_and_comments(sql)
        stripped = masked.rstrip()
        if not stripped or stripped[-1] != ";":
            return sql
        index = len(stripped) - 1
        sql = sql[:index] + sql[index + 1:]


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _fetchmany_all(cur: Any, limit: int = 10_000) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    while len(rows) < limit:
        batch = cur.fetchmany(min(512, limit - len(rows)))
        if not batch:
            break
        rows.extend(batch)
    return rows


def _describe(conn: duckdb.DuckDBPyConnection, sql: str, params: Iterable[Any] | None) -> list[tuple[str, str]]:
    cur = conn.execute(f"DESCRIBE (\n{sql}\n)", list(params or []))
    return [(str(row[0]), str(row[1])) for row in _fetchmany_all(cur)]


def _is_string_scalar(type_name: str) -> bool:
    return bool(_STRING_TYPES.match(type_name or ""))


def _is_fixed_scalar(type_name: str) -> bool:
    """True only for the conservative fixed-width scalar whitelist."""
    return bool(_FIXED_SCALAR_TYPES.match(type_name or ""))


def _is_complex(type_name: str) -> bool:
    """True for non-text variable-width values (arrays, structs, BIT, …)."""
    return not _is_fixed_scalar(type_name) and not _is_string_scalar(type_name)


def _is_fixed_array_type(type_name: str) -> bool:
    """DuckDB fixed-size ARRAY, including multidimensional ``[2][3]``."""
    return bool(re.search(r"\[\s*\d+\s*\]", type_name or ""))


def _validate_metadata(
    columns: list[tuple[str, str]], budget: SqlBudget
) -> None:
    """Bound the column list before any result row is fetched.

    ``DESCRIBE`` itself is already bounded by ``HARD_MAX_SQL_BYTES``; this
    second layer rejects an absurd alias/column count and caps the worst-case
    projected row width so one row cannot first enter Python unbounded.
    """
    if len(columns) > HARD_MAX_COLUMNS:
        raise SqlBudgetError(
            "budget_exceeded",
            f"query projects {len(columns)} columns; the hard maximum is "
            f"{HARD_MAX_COLUMNS}. Narrow the projection.",
            stage="metadata",
        )
    metadata_bytes = 0
    cell_cap = min(budget.max_cell_chars, HARD_MAX_CELL_CHARS)
    marker_bytes = len(TRUNCATION_MARKER.encode("utf-8"))
    # Worst-case UTF-8/JSON wire estimate, not a character count. A clipped
    # cell can contain control characters (six JSON bytes each) or 4-byte
    # Unicode; keys are JSON-escaped too. Fixed-size scalars get a generous
    # value margin. Fixed arrays use an always-present helper, so their native
    # and helper representations can both be non-NULL for small values.
    estimated_row_bytes = 2 + max(0, len(columns) - 1)
    for index, (name, type_name) in enumerate(columns):
        name_bytes = len(str(name).encode("utf-8"))
        if name_bytes > HARD_MAX_COLUMN_NAME_BYTES:
            raise SqlBudgetError(
                "budget_exceeded",
                f"column name is {name_bytes} UTF-8 bytes; the hard maximum is "
                f"{HARD_MAX_COLUMN_NAME_BYTES}. Use a short alias.",
                stage="metadata",
            )
        metadata_bytes += name_bytes + len(str(type_name).encode("utf-8"))
        key_bytes = len(json.dumps(str(name), ensure_ascii=False).encode("utf-8")) + 1
        if _is_fixed_scalar(type_name):
            estimated_row_bytes += key_bytes + 64
            continue
        # Every non-whitelisted type goes through the SQL-side bounded path,
        # including fixed arrays and future variable-width extension types.
        cell_bytes = cell_cap * 6 + marker_bytes
        estimated_row_bytes += key_bytes + cell_bytes
        helper_key = f"__ow043_trunc_{index}"
        estimated_row_bytes += (
            len(json.dumps(helper_key, ensure_ascii=False).encode("utf-8"))
            + 16
        )
        estimated_row_bytes += (
            cell_bytes if _is_fixed_array_type(type_name) else 4
        )
    if metadata_bytes > HARD_MAX_METADATA_BYTES:
        raise SqlBudgetError(
            "budget_exceeded",
            f"column metadata is {metadata_bytes} UTF-8 bytes; the hard maximum "
            f"is {HARD_MAX_METADATA_BYTES}. Narrow the projection.",
            stage="metadata",
        )
    if estimated_row_bytes > HARD_MAX_ROW_BYTES:
        raise SqlBudgetError(
            "budget_exceeded",
            "worst-case UTF-8/JSON row width (clipped cells, JSON escaping, "
            "keys and internal bounded helpers) exceeds the server row bound of "
            f"{HARD_MAX_ROW_BYTES} bytes. Lower max_cell_chars or project fewer "
            "text/variable-width columns.",
            stage="metadata",
        )


def _build_bounded_sql(
    sql: str,
    columns: list[tuple[str, str]],
    budget: SqlBudget,
) -> tuple[str, list[str], list[tuple[str, str, str]]]:
    """Wrap the caller SQL and clip cells inside DuckDB.

    Every non-whitelisted type uses the same bounded shape: the native value is
    returned only while its SQL-side text rendering fits the cell cap; an
    over-budget value is NULL natively and arrives only through a bounded text
    helper column.  This covers text, BLOB, LIST, fixed/multidimensional ARRAY,
    STRUCT, MAP, UNION, BIT, VARINT, ENUM, JSON-like extensions and unknown
    future types.  Small values therefore keep their existing wire semantics
    (including bytes for BLOB), while a large value never crosses into Python.

    Helper names are allocated from the actual user column-name set (and from
    previously allocated helper names), never from a fixed prefix alone.  The
    returned ``projection`` records every SELECT-list position and wire key so
    duplicate user aliases keep their existing last-wins semantics, a helper
    can never overwrite a user column, and transferred bytes can be counted
    before the helper fields are removed.
    """
    reserved = {str(name).casefold() for name, _type in columns}

    def _helper_alias(index: int) -> str:
        base = f"__ow043_trunc_{index}"
        candidate = base
        suffix = 0
        while candidate.casefold() in reserved:
            suffix += 1
            candidate = f"{base}_{suffix}"
        reserved.add(candidate.casefold())
        return candidate

    projected: list[str] = []
    projection: list[tuple[str, str, str]] = []
    complex_columns: list[str] = []
    for index, (name, type_name) in enumerate(columns):
        quoted = _quote_ident(name)
        # Positional references (#1, #2, …) are essential here: a user query
        # may repeat an alias, and referencing "x" by name would silently pick
        # the first occurrence for every projected position. Position also
        # keeps the SQL-side type/column alignment from DESCRIBE exact.
        source = f"#{index + 1}"
        cast = f"CAST({source} AS VARCHAR)"
        if _is_fixed_scalar(type_name):
            projected.append(f"{source} AS {quoted}")
            projection.append(("user", name, name))
            continue

        alias = _helper_alias(index)
        projected.append(
            f"CASE WHEN length({cast}) <= {budget.max_cell_chars} "
            f"THEN {source} ELSE NULL END AS {quoted}"
        )
        if _is_fixed_array_type(type_name):
            # DuckDB 1.5 can OOM when a fixed-array native CASE and a
            # CASE-wrapped substr share a projection. Keep the helper always
            # present and bounded; Python ignores it when the native value is
            # non-NULL.
            helper_expr = (
                f"substr({cast}, 1, {budget.max_cell_chars}) || "
                f"'{TRUNCATION_MARKER}'"
            )
        else:
            helper_expr = (
                f"CASE WHEN length({cast}) > {budget.max_cell_chars} "
                f"THEN substr({cast}, 1, {budget.max_cell_chars}) || "
                f"'{TRUNCATION_MARKER}' ELSE NULL END"
            )
        projected.append(f"{helper_expr} AS {_quote_ident(alias)}")
        projection.append(("user", name, name))
        projection.append(("helper", name, alias))
        if not _is_string_scalar(type_name) and not _BLOB_TYPE.match(type_name or ""):
            complex_columns.append(name)

    select_list = ", ".join(projected) if projected else "*"
    offset = f" OFFSET {budget.offset}" if budget.offset else ""
    return (
        f"SELECT {select_list} FROM (\n{sql}\n) AS __ow043_q "
        f"LIMIT {budget.fetch_limit}{offset}",
        complex_columns,
        projection,
    )


def _serialized_size(payload: Any) -> int:
    """UTF-8 JSON-equivalent size of a value (not physical I/O bytes)."""
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _classify_db_error(exc: BaseException, timed_out: bool) -> str:
    if timed_out or type(exc).__name__ in {
        "InterruptException",
        "KeyboardInterrupt",
    }:
        return "timeout"
    text = str(exc).lower()
    if (
        type(exc).__name__ in {"OutOfMemoryException", "OutOfMemoryError"}
        or "out of memory" in text
    ):
        # A valid query that exhausts the configured DuckDB memory budget is a
        # resource-budget failure, not a malformed SQL error. Host RSS remains
        # an observation, not a guarantee of this classification.
        return "budget_exceeded"
    if "values were not provided" in text or "parameter" in text and "bound" in text:
        return "parameter_error"
    if "not a valid duckdb database" in text or "database file" in text and "corrupt" in text:
        return "source_error"
    if "could not set lock" in text or "conflicting lock" in text:
        return "connection_error"
    return "sql_error"


def run_bounded_query(
    db_path: str | Path,
    sql: str,
    params: Iterable[Any] | None,
    budget: SqlBudget,
) -> BoundedQueryResult:
    """Execute one read-only SELECT/WITH with bounded fetch and time budget."""
    if not isinstance(sql, str):
        raise SqlBudgetError("sql_rejected", "SQL must be a string")
    sql_bytes = len(sql.encode("utf-8"))
    if sql_bytes > HARD_MAX_SQL_BYTES:
        raise SqlBudgetError(
            "budget_exceeded",
            f"SQL is {sql_bytes} UTF-8 bytes; the server hard maximum is "
            f"{HARD_MAX_SQL_BYTES}. Shorten the SQL (comments included).",
        )
    validate_readonly_sql(sql)
    sql = _strip_trailing_semicolons(sql)

    path = Path(db_path)
    if not path.exists():
        raise SqlBudgetError("source_missing", f"database not found: {path}")
    if not path.is_file():
        raise SqlBudgetError("source_error", f"database path is not a file: {path}")

    config: dict[str, str] = {
        "enable_external_access": "false",
        "memory_limit": budget.memory_limit,
        "threads": str(budget.threads),
    }
    temp_dir = os.environ.get("OLAV_SQL_TEMP_DIR")
    if temp_dir:
        try:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            config["temp_directory"] = temp_dir
        except OSError:
            pass

    started = time.perf_counter()
    deadline = started + max(budget.timeout_ms, 1) / 1000.0
    params_list = list(params or [])
    timed_out = False

    try:
        conn = duckdb.connect(str(path), read_only=True, config=config)
    except Exception as exc:  # noqa: BLE001 - mapped to a typed contract below
        raise SqlBudgetError(
            _classify_db_error(exc, False),
            f"could not open database read-only: {exc}",
            stage="connect",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        ) from exc

    timer: threading.Timer | None = None

    def _deadline_error(stage: str, partial_rows: list[dict[str, Any]] | None = None) -> SqlBudgetError:
        return SqlBudgetError(
            "timeout",
            f"query exceeded the {budget.timeout_ms} ms budget",
            stage=stage,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            partial_rows=partial_rows or [],
        )

    if time.perf_counter() > deadline:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise SqlBudgetError(
            "timeout",
            f"query exceeded the {budget.timeout_ms} ms budget during connect",
            stage="connect",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _interrupt() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001 - best-effort watchdog
            pass

    try:
        timer = threading.Timer(max(budget.timeout_ms, 1) / 1000.0, _interrupt)
        timer.daemon = True
        timer.start()

        prepare_started = time.perf_counter()
        try:
            columns = _describe(conn, sql, params_list)
        except Exception as exc:  # noqa: BLE001
            if timed_out or time.perf_counter() > deadline:
                raise _deadline_error("describe") from exc
            raise SqlBudgetError(
                _classify_db_error(exc, timed_out),
                f"SQL describe failed: {exc}",
                stage="describe",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            ) from exc
        prepare_ms = (time.perf_counter() - prepare_started) * 1000
        if time.perf_counter() > deadline:
            raise _deadline_error("describe")
        _validate_metadata(columns, budget)

        bounded_sql, complex_columns, projection = _build_bounded_sql(
            sql, columns, budget
        )

        execute_started = time.perf_counter()
        try:
            cur = conn.execute(bounded_sql, params_list)
        except Exception as exc:  # noqa: BLE001
            if timed_out or time.perf_counter() > deadline:
                raise _deadline_error("execute") from exc
            raise SqlBudgetError(
                _classify_db_error(exc, timed_out),
                f"SQL execution failed: {exc}",
                stage="execute",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            ) from exc
        execute_ms = (time.perf_counter() - execute_started) * 1000
        if time.perf_counter() > deadline:
            raise _deadline_error("execute")

        names = [str(name) for name, _type in columns]
        rows: list[dict[str, Any]] = []
        fetched_rows = 0
        fetched_bytes = 0
        returned_bytes = 0
        cell_truncated = False
        budget_exceeded = False
        fetch_started = time.perf_counter()
        serialize_ms = 0.0

        try:
            # One row at a time: the byte budget is a real *fetch* stop, not
            # only a preview slice.  At most one row can cross the boundary.
            while fetched_rows < budget.fetch_limit:
                batch = cur.fetchmany(1)
                if not batch:
                    break
                values = batch[0]
                if len(values) != len(projection):
                    raise SqlBudgetError(
                        "sql_error",
                        "internal bounded projection did not match the result shape",
                        stage="fetch",
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        fetched_rows=fetched_rows,
                        fetched_bytes=fetched_bytes,
                    )
                row: dict[str, Any] = {}
                # Ordered key/value pairs retain every fetched projection
                # position, including duplicate user aliases and helpers, so
                # fetched_bytes does not silently become a last-wins payload.
                wire_row: list[list[Any]] = []
                # Rebuild by projection position. Duplicate user aliases keep
                # the historical last-wins dict semantics, while helper values
                # are applied only at their own position and can never remove a
                # user column with a colliding name.
                for (kind, column_name, wire_key), value in zip(projection, values):
                    wire_row.append([wire_key, value])
                    if kind == "helper":
                        # The helper is always a bounded text rendering. Only
                        # use it when the native projection is NULL, which is
                        # exactly the over-budget branch; a genuine SQL NULL
                        # produces a NULL helper as well. This is the only
                        # truncation signal (never an endswith marker test).
                        if value is not None and row.get(column_name) is None:
                            row[column_name] = value
                            cell_truncated = True
                    else:
                        row[column_name] = value
                serialize_started = time.perf_counter()
                # fetched_bytes counts every fetched field position, including
                # duplicate user aliases and internal helper fields, before
                # helpers are removed. This is a JSON-equivalent size, not
                # DuckDB physical I/O or host RSS.
                fetched_row_size = _serialized_size(wire_row)
                row_size = _serialized_size(row)
                serialize_ms += (time.perf_counter() - serialize_started) * 1000
                fetched_rows += 1
                fetched_bytes += fetched_row_size

                if len(rows) >= budget.max_rows:
                    # This is the extra row that only proves has_more.
                    break
                if returned_bytes + row_size > budget.max_total_bytes:
                    budget_exceeded = True
                    break
                rows.append(row)
                returned_bytes += row_size

                if time.perf_counter() > deadline:
                    raise _deadline_error("fetch", rows)
        except SqlBudgetError:
            raise
        except Exception as exc:  # noqa: BLE001
            if timed_out or time.perf_counter() > deadline:
                raise _deadline_error("fetch", rows) from exc
            raise SqlBudgetError(
                _classify_db_error(exc, timed_out),
                f"SQL fetch failed: {exc}",
                stage="fetch",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                partial_rows=rows,
                fetched_rows=fetched_rows,
                fetched_bytes=fetched_bytes,
            ) from exc

        fetch_ms = (time.perf_counter() - fetch_started) * 1000

        # If the byte budget stopped the fetch before the row cap, we cannot
        # know whether more rows exist; report has_more conservatively (never
        # claim a complete result) and let the caller page/refine.
        has_more = fetched_rows > budget.max_rows or budget_exceeded
        truncated = has_more or budget_exceeded or cell_truncated or len(rows) < fetched_rows
        total = (
            len(rows)
            if (
                budget.offset == 0
                and not has_more
                and not budget_exceeded
                and len(rows) == fetched_rows
            )
            else None
        )
        # A byte-budget stop is an error at the entry points.  Do not offer an
        # offset that would make no progress (offset + 0 == current offset).
        next_offset = (
            budget.offset + len(rows)
            if has_more and not budget_exceeded and len(rows) > 0
            else None
        )

        if timed_out or time.perf_counter() > deadline:
            raise SqlBudgetError(
                "timeout",
                f"query exceeded the {budget.timeout_ms} ms budget",
                stage="fetch",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                partial_rows=rows,
                fetched_rows=fetched_rows,
                fetched_bytes=fetched_bytes,
            )

        total_ms = (time.perf_counter() - started) * 1000
        return BoundedQueryResult(
            rows=rows,
            columns=names,
            fetched_rows=fetched_rows,
            fetched_bytes=fetched_bytes,
            returned_rows=len(rows),
            returned_bytes=returned_bytes,
            has_more=has_more,
            truncated=truncated,
            cell_truncated=cell_truncated,
            budget_exceeded=budget_exceeded,
            total=total,
            next_offset=next_offset,
            complex_columns=complex_columns,
            timing={
                "prepare_ms": round(prepare_ms, 3),
                "execute_ms": round(execute_ms, 3),
                "fetch_ms": round(fetch_ms, 3),
                "serialize_ms": round(serialize_ms, 3),
                "total_ms": round(total_ms, 3),
            },
            budget=budget.as_dict(),
        )
    finally:
        if timer is not None:
            timer.cancel()
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
