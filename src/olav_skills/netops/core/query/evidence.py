"""Metadata-first evidence lookup over recorded raw output.

Two phases, one contract:

1. Metadata only — device, command, snapshot IDs and collection date narrow the
   *complete* candidate set.  ``count(*)`` over that predicate is an exact
   candidate count; no body text is read and no row is sampled.
2. Bounded body match — only for that candidate set, the body predicate runs
   once, its exact match count is returned, and a page is cut at ``offset``.
   Source ID, content hash and real excerpt line numbers are computed by the
   database from the same rows.

A candidate set larger than ``candidate_budget`` is *not* sampled: it falls
back to one complete SQL count + page over the same metadata predicate
(``path='full_scan'``).  Deadline/memory exhaustion is reported as
``budget_exceeded``, never as an empty or partial success.

Identity and ordering are NULL-safe because the store may drift:

* ``source_id`` is the store's real ``id`` when the table has one
  (``identity_scope='record_id'``); otherwise it is a hash of the
  ``(snapshot_id, device_name, command)`` key encoded as JSON, so ``NULL`` and
  the empty string stay different keys and a separator character cannot forge
  a collision (``identity_scope='record_key'``).  A key hash covers every body
  stored under that key; ``content_hash`` separates those bodies.
* Key comparison uses ``IS NOT DISTINCT FROM`` for every key column, so a
  ``NULL`` key still joins to its candidate row instead of silently dropping
  the body (plain ``=`` drops it).
* Paging orders by the key, then ``content_hash`` and ``source_id``, so
  duplicate keys with different bodies cannot shift across page boundaries.

Date filters use an explicit collection column when the schema has one.  When
it does not, the only accepted basis is a bare ``YYYY-MM-DD`` ``snapshot_id``
label, the same derived date label ``query.temporal.snapshot_time`` already
exposes (``collection_basis_status='derived'``).  Rows whose label is not a
plain date are *unknown*, not matches, and empty results say so with
``zero_reason='no_dated_candidates'``.  Ingest/update clocks are never used.

Failures are distinct: ``invalid_argument``, ``source_missing``,
``permission_denied``, ``schema_drift``, ``sql_error``, ``budget_exceeded``.
A successful lookup with no rows carries ``zero_reason`` — ``store_empty``,
``no_candidates`` (metadata filters matched nothing), ``no_body_match`` or
``source_missing`` (syslog store absent) — which is different from an error.

This module never grants access: it executes SQL on the connection the caller
already opened and authorized.  It never writes, never touches ingest/update
clocks as capture time, and never selects a full config dump.
"""
from __future__ import annotations

from datetime import date
import threading
import time as _time

MAX_PAGE_ROWS = 50
CANDIDATE_BUDGET = 256
DEFAULT_DEADLINE_MS = 15_000
EXCERPT_CHARS = 1_200
EXCERPT_LEAD = 200
FALLBACK_EXCERPT_CHARS = 800

#: Only an explicit collection field may scope a date filter.  ``updated_at``
#: and ``ingested_at`` are clocks about the record, not about the capture.
COLLECTION_COLUMNS = ("collected_at", "captured_at", "snapshot_date")
OPTIONAL_COLUMNS = COLLECTION_COLUMNS + ("ingested_at", "updated_at")

#: Real record identity, most specific first.  When the store carries one of
#: these, ``source_id`` wraps it instead of hashing the logical key.
IDENTITY_COLUMNS = ("id", "raw_output_id", "record_id")

#: Derived date label source: a bare ``YYYY-MM-DD`` snapshot id.  This is the
#: OW-058 ``snapshot_id.date_label`` semantics, never a capture timestamp.
DATE_LABEL_EXPR = (
    "CASE WHEN regexp_full_match(cast({alias}.snapshot_id AS VARCHAR), "
    "'[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}') "
    "THEN TRY_CAST(cast({alias}.snapshot_id AS VARCHAR) AS DATE) END"
)
SNAPSHOT_DATE_LABEL = "snapshot_id.date_label"

#: ISO shapes mirrored from ``query.temporal.time_value``.  The date filter must
#: normalize exactly like OW-058: a bare date stays a date, an offset-aware
#: timestamp is canonicalised to UTC, and a naive timestamp keeps its wall clock
#: without inventing a timezone.
_DATE_SHAPE = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
_MIDNIGHT_SHAPE = _DATE_SHAPE + "[T ]00:00:00(Z|[+]00:00)?"
_TIMESTAMP_SHAPE = (
    _DATE_SHAPE + "[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    "([.][0-9]{1,6})?(Z|[+-][0-9]{2}(:[0-9]{2})?)?"
)
_OFFSET_TAIL = "(Z|[+-][0-9]{2}(:[0-9]{2})?)$"

#: Why a row cannot be attributed to a day under a date filter.  A row with one
#: of these is unknown time and is never counted as a date match.
INVALID_VALUE = "invalid_value"
CONFLICTING_CAPTURE_TIME = "conflicting_capture_time"
NO_CAPTURE_TIME = "no_capture_time"
DATE_EXCLUSION_REASONS = (INVALID_VALUE, CONFLICTING_CAPTURE_TIME, NO_CAPTURE_TIME)

ERROR_KINDS = (
    "invalid_argument",
    "source_missing",
    "permission_denied",
    "schema_drift",
    "sql_error",
    "budget_exceeded",
)


class EvidenceError(Exception):
    """Typed evidence failure; ``kind`` is one of ``ERROR_KINDS``."""

    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = dict(detail or {})


def classify_exception(exc: Exception) -> EvidenceError:
    """Map a driver/OS exception to a stable, non-leaking failure kind."""
    if isinstance(exc, EvidenceError):
        return exc
    text = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, (PermissionError,)) or "permission denied" in text or "access denied" in text:
        return EvidenceError("permission_denied", "Evidence store is not readable by this process")
    # A missing column/table inside an existing source is schema drift, not a
    # missing source: DuckDB says "not found in FROM clause" for both, so the
    # schema signals must be checked before the generic not-found branch.
    # NB: no bare "schema" token — DuckDB's own function names (``parquet_schema``)
    # contain it, which would turn a missing-file error into schema drift.
    if any(token in text for token in (
        "binder error", "parser error", "catalog error", "conversion error",
        "invalid input", "referenced column", "not found in from clause",
        "column with name", "does not have a column", "table with name",
        "does not exist in from", "schema mismatch", "schema of",
    )):
        return EvidenceError("schema_drift", "Evidence source does not match the expected schema")
    if "no files found" in text or "no such file" in text or "does not exist" in text:
        return EvidenceError("source_missing", "Evidence store, file or table is missing")
    if "out of memory" in text or "interrupted" in text or "timeout" in text or "timed out" in text:
        return EvidenceError("budget_exceeded", "Evidence query exceeded its deadline or memory budget")
    return EvidenceError("sql_error", "Evidence query failed")


class _Deadline:
    """Wall-clock budget per call; interrupts a real DuckDB connection."""

    def __init__(self, con, deadline_ms: int | None):
        self.deadline_ms = deadline_ms
        self.fired = False
        self._end = None
        self._timer = None
        if deadline_ms is None:
            return
        if not isinstance(deadline_ms, int) or isinstance(deadline_ms, bool) or deadline_ms < 0:
            raise EvidenceError("invalid_argument", "deadline_ms must be a non-negative integer")
        self._end = _time.monotonic() + deadline_ms / 1000.0
        interrupt = getattr(con, "interrupt", None)
        if callable(interrupt):
            def _fire():
                self.fired = True
                try:
                    interrupt()
                except Exception:  # noqa: BLE001 - closing/closed connection
                    pass
            self._timer = threading.Timer(max(deadline_ms, 0) / 1000.0, _fire)
            self._timer.daemon = True
            self._timer.start()

    def check(self):
        if self.fired or (self._end is not None and _time.monotonic() > self._end):
            self.fired = True
            raise EvidenceError(
                "budget_exceeded",
                "Evidence query exceeded its deadline",
                {"deadline_ms": self.deadline_ms},
            )

    def stop(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _run(con, sql: str, params: list, deadline: _Deadline):
    deadline.check()
    try:
        cursor = con.execute(sql, params) if params else con.execute(sql)
        rows = cursor.fetchall()
    except EvidenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalized below
        error = classify_exception(exc)
        if deadline.fired and error.kind in ("sql_error", "budget_exceeded"):
            error = EvidenceError(
                "budget_exceeded",
                "Evidence query exceeded its deadline",
                {"deadline_ms": deadline.deadline_ms},
            )
        raise error from None
    deadline.check()
    return rows


def identity_column(columns) -> str | None:
    """Return the store's real record-id column, if the schema has one."""
    return next((name for name in IDENTITY_COLUMNS if name in columns), None)


def _key_identity_expr(alias: str = "") -> str:
    """NULL-safe key identity; ``null`` and ``""`` stay different keys."""
    prefix = f"{alias}." if alias else ""
    return (
        "'rec_key_' || md5(cast(to_json(list_value("
        "cast(" + prefix + "snapshot_id AS VARCHAR), "
        "cast(" + prefix + "device_name AS VARCHAR), "
        "cast(" + prefix + "command AS VARCHAR))) AS VARCHAR))"
    )


def _source_id_expr(alias: str = "", id_column: str | None = None) -> str:
    """Stable, NULL-safe record identity; never derived from row order.

    With a real id column the identity is that record's id.  A NULL real id
    falls back to the key identity instead of returning NULL, so every returned
    row can be re-read through ``ids``.  Prefixes keep the two scopes apart, so
    an id that happens to spell a key hash cannot forge another record.
    """
    key = _key_identity_expr(alias)
    if not id_column:
        return key
    prefix = f"{alias}." if alias else ""
    return (
        "CASE WHEN " + prefix + id_column + " IS NULL THEN " + key +
        " ELSE 'rec_id_' || cast(" + prefix + id_column + " AS VARCHAR) END"
    )


def _identity_basis_expr(alias: str = "", id_column: str | None = None) -> str:
    """Per-row identity scope, so a NULL-id fallback is visible in the row."""
    if not id_column:
        return "'record_key'"
    prefix = f"{alias}." if alias else ""
    return f"CASE WHEN {prefix}{id_column} IS NULL THEN 'record_key' ELSE 'record_id' END"


def _identity_scope(id_column: str | None, null_id_candidates: int = 0):
    """Result-level identity scope, decided from the candidate rows.

    ``record_id`` — the store has an id column and every candidate row has one,
    so ``source_id`` is that real record id.
    ``record_id_with_record_key_fallback`` — the store has an id column but at
    least one candidate row's id is NULL; those rows return the record-key hash
    instead, so a returned ``source_id`` is never NULL and ``ids`` can re-read
    every row.  ``identity_fallback`` names that rule.
    ``record_key`` — no id column at all.
    """
    if not id_column:
        return "record_key", None
    if null_id_candidates:
        return "record_id_with_record_key_fallback", "record_key_when_id_is_null"
    return "record_id", None


def _join_on_keys_sql(alias_c: str = "c") -> str:
    """NULL-safe key join: plain ``=`` would drop every row whose key is NULL."""
    return (
        " " + alias_c + " ON r.device_name IS NOT DISTINCT FROM " + alias_c + ".device_name"
        " AND r.command IS NOT DISTINCT FROM " + alias_c + ".command"
        " AND r.snapshot_id IS NOT DISTINCT FROM " + alias_c + ".snapshot_id"
    )


def table_columns(con, deadline: _Deadline) -> set[str]:
    rows = _run(
        con,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'netops' AND table_name = 'raw_output_store'",
        [],
        deadline,
    )
    return {row[0] for row in rows}


def _valid_date(value, name: str):
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvidenceError("invalid_argument", f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise EvidenceError("invalid_argument", f"{name} must be YYYY-MM-DD") from None


def _normalize_list(value, name: str, limit: int = 50) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise EvidenceError("invalid_argument", f"{name} must be a list of strings")
    if len(value) > limit:
        raise EvidenceError("invalid_argument", f"{name} accepts at most {limit} values")
    result = []
    for item in value:
        if not isinstance(item, str) or not item or len(item.encode()) > 1024:
            raise EvidenceError("invalid_argument", f"{name} values must be non-empty bounded strings")
        result.append(item)
    return result


def _date_basis(columns) -> tuple[str | None, str | None]:
    """Date-filter basis and its provenance.

    Explicit collection columns are used per row, not per schema: a row whose
    ``collected_at`` is NULL still filters by its valid ``captured_at``.  A bare
    ``YYYY-MM-DD`` snapshot label is the accepted *derived* fallback.
    """
    explicit = [column for column in COLLECTION_COLUMNS if column in columns]
    has_label = "snapshot_id" in columns
    if explicit and has_label:
        return "|".join(explicit + [SNAPSHOT_DATE_LABEL]), "known_or_derived"
    if explicit:
        return "|".join(explicit), "known"
    if has_label:
        return SNAPSHOT_DATE_LABEL, "derived"
    return None, None


def _stamp_sql(column: str, alias: str, *, date_only: bool) -> tuple[str, str]:
    """SQL ``(canonical_key, filter_date)`` for one collection column.

    ``canonical_key`` groups equal OW-058 stamps.  Two offset strings for the
    same instant share one key (both are normalized to UTC), while a different
    instant, a different precision (date vs timestamp, naive vs aware) or an
    invalid value all differ.  ``filter_date`` is the day the stamp scopes: the
    UTC day for an aware timestamp, the stored day for a date or a naive
    timestamp.  Both are NULL exactly when ``time_value`` calls the value
    unknown.
    """
    raw = f"trim(cast({alias}.{column} AS VARCHAR))"
    null = f"{alias}.{column} IS NULL"
    if date_only:
        key = (
            f"CASE WHEN {null} THEN NULL "
            f"WHEN regexp_full_match({raw}, '{_DATE_SHAPE}') THEN "
            f"'d|' || cast(TRY_CAST({raw} AS DATE) AS VARCHAR) "
            f"WHEN regexp_full_match({raw}, '{_MIDNIGHT_SHAPE}') THEN "
            f"'d|' || cast(TRY_CAST(substr({raw}, 1, 10) AS DATE) AS VARCHAR) "
            "ELSE NULL END"
        )
        day = (
            f"CASE WHEN {null} THEN NULL "
            f"WHEN regexp_full_match({raw}, '{_DATE_SHAPE}') THEN TRY_CAST({raw} AS DATE) "
            f"WHEN regexp_full_match({raw}, '{_MIDNIGHT_SHAPE}') THEN "
            f"TRY_CAST(substr({raw}, 1, 10) AS DATE) ELSE NULL END"
        )
        return key, day
    key = (
        f"CASE WHEN {null} THEN NULL "
        f"WHEN regexp_full_match({raw}, '{_DATE_SHAPE}') THEN "
        f"'d|' || cast(TRY_CAST({raw} AS DATE) AS VARCHAR) "
        f"WHEN regexp_full_match({raw}, '{_TIMESTAMP_SHAPE}') "
        f"AND regexp_matches({raw}, '{_OFFSET_TAIL}') THEN "
        "'ts|' || strftime(timezone('UTC', TRY_CAST(" + raw + " AS TIMESTAMPTZ)), "
        "'%Y-%m-%dT%H:%M:%S.%f') "
        f"WHEN regexp_full_match({raw}, '{_TIMESTAMP_SHAPE}') THEN "
        "'tsn|' || strftime(TRY_CAST(" + raw + " AS TIMESTAMP), "
        "'%Y-%m-%dT%H:%M:%S.%f') "
        "ELSE NULL END"
    )
    day = (
        f"CASE WHEN {null} THEN NULL "
        f"WHEN regexp_full_match({raw}, '{_DATE_SHAPE}') THEN TRY_CAST({raw} AS DATE) "
        f"WHEN regexp_full_match({raw}, '{_TIMESTAMP_SHAPE}') "
        f"AND regexp_matches({raw}, '{_OFFSET_TAIL}') THEN "
        "CAST(timezone('UTC', TRY_CAST(" + raw + " AS TIMESTAMPTZ)) AS DATE) "
        f"WHEN regexp_full_match({raw}, '{_TIMESTAMP_SHAPE}') THEN "
        "CAST(TRY_CAST(" + raw + " AS TIMESTAMP) AS DATE) "
        "ELSE NULL END"
    )
    return key, day


def _date_stamps(columns, alias: str):
    """Per-column stamp keys/days and the validity/conflict expressions."""
    explicit = [column for column in COLLECTION_COLUMNS if column in columns]
    stamps = [
        (column, _stamp_sql(column, alias, date_only=column == "snapshot_date"))
        for column in explicit
    ]
    keys = [key for _, (key, _) in stamps]
    days = [day for _, (_, day) in stamps]
    invalid = " OR ".join(
        f"({alias}.{column} IS NOT NULL AND ({key}) IS NULL)"
        for column, (key, _) in stamps
    )
    valid_keys = "list_filter([" + ", ".join(keys) + "], x -> x IS NOT NULL)"
    valid_days = "list_filter([" + ", ".join(days) + "], x -> x IS NOT NULL)"
    return explicit, invalid, valid_keys, valid_days


def _snapshot_label_sql(columns, alias: str) -> str:
    return DATE_LABEL_EXPR.format(alias=alias) if "snapshot_id" in columns else "NULL"


def _date_basis_sql(columns, alias: str = "r") -> str:
    """Per-row collection day under OW-058 precision/status semantics.

    Explicit collection columns are evaluated per row with the same
    normalization as ``query.temporal.record_temporal``:

    * a non-NULL value that is not a date/timestamp (including the empty
      string) makes the row's capture time *unknown*.  It is never coerced to
      NULL and replaced by another column's date;
    * two *different* canonical stamps on one row are a conflict, and a conflict
      is unknown (``NULL``) — including two instants on the same calendar day,
      or the same day written as a date in one clock and a timestamp in the
      other.  A range is never derived from them;
    * an aware timestamp is normalized to UTC before its day is taken, so two
      offset strings for the same instant agree; a naive timestamp keeps its
      wall-clock day without inventing a timezone; a date stays a date.

    The bare ``YYYY-MM-DD`` snapshot label is only used when every explicit
    column on that row is NULL, so a derived label never overrides a recorded
    capture.
    """
    explicit = [column for column in COLLECTION_COLUMNS if column in columns]
    label = _snapshot_label_sql(columns, alias)
    if not explicit:
        return label
    _, invalid, valid_keys, valid_days = _date_stamps(columns, alias)
    return (
        f"CASE WHEN ({invalid}) THEN NULL "
        f"WHEN len(list_distinct({valid_keys})) > 1 THEN NULL "
        f"WHEN len({valid_keys}) > 0 THEN list_extract({valid_days}, 1) "
        f"ELSE {label} END"
    )


def _date_reason_sql(columns, alias: str = "r") -> str:
    """Why this row has no trustworthy collection day (NULL when it has one)."""
    explicit = [column for column in COLLECTION_COLUMNS if column in columns]
    label = _snapshot_label_sql(columns, alias)
    if not explicit:
        return f"CASE WHEN ({label}) IS NULL THEN '{NO_CAPTURE_TIME}' END"
    _, invalid, valid_keys, _ = _date_stamps(columns, alias)
    return (
        f"CASE WHEN ({invalid}) THEN '{INVALID_VALUE}' "
        f"WHEN len(list_distinct({valid_keys})) > 1 THEN '{CONFLICTING_CAPTURE_TIME}' "
        f"WHEN len({valid_keys}) > 0 THEN NULL "
        f"WHEN ({label}) IS NOT NULL THEN NULL "
        f"ELSE '{NO_CAPTURE_TIME}' END"
    )


def _meta_filters(columns, *, kind, device, snapshot, commands, ids, date_from, date_to,
                  alias="r", id_column=None):
    where: list[str] = []
    params: list = []
    if kind == "config":
        where.append(
            f"({alias}.command ILIKE '%running-config%' OR {alias}.command ILIKE '%startup-config%')"
        )
    if device:
        where.append(f"{alias}.device_name ILIKE ?")
        params.append(f"%{device}%")
    if snapshot:
        where.append(f"{alias}.snapshot_id = ?")
        params.append(snapshot)
    if commands:
        where.append(f"{alias}.command IN ({','.join('?' * len(commands))})")
        params.extend(commands)
    if ids:
        where.append(f"({_source_id_expr(alias, id_column)} IN ({','.join('?' * len(ids))}))")
        params.extend(ids)
    basis, basis_status = _date_basis(columns) if (date_from or date_to) else (None, None)
    if (date_from or date_to) and basis is None:
        raise EvidenceError(
            "schema_drift",
            "No collection-time basis exists for the requested date filter; "
            "ingest/update clocks are not capture time",
            {"required_any": list(COLLECTION_COLUMNS) + ["snapshot_id"],
             "columns": sorted(columns)},
        )
    if basis is not None:
        basis_sql = _date_basis_sql(columns, alias)
        if date_from:
            where.append(f"{basis_sql} >= ?")
            params.append(date_from)
        if date_to:
            where.append(f"{basis_sql} <= ?")
            params.append(date_to)
    return where, params, basis, basis_status


def _optional_columns(columns) -> list[str]:
    return [name for name in OPTIONAL_COLUMNS if name in columns]


def _where(parts: list[str]) -> str:
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def _empty_result(*, kind, page, page_size, offset, candidate_total, path, zero_reason,
                  filters, basis, budget, source_state="present",
                  basis_status=None, identity_scope=None, identity_fallback=None,
                  body_scope=None, excerpt_truncated_rows=0,
                  date_unknown_candidates=None, date_exclusions=None):
    return {
        "status": "success",
        "source": kind,
        "source_state": source_state,
        "matches": [],
        "returned": 0,
        "total": 0,
        "candidate_total": candidate_total,
        "page": page,
        "page_size": page_size,
        "offset": offset,
        "has_more": False,
        "truncated": False,
        "path": path,
        "zero_reason": zero_reason,
        "filters": filters,
        "collection_basis": basis,
        "collection_basis_status": basis_status,
        "identity_scope": identity_scope,
        "identity_fallback": identity_fallback,
        "excerpt_truncated_rows": excerpt_truncated_rows,
        "budget": budget,
        "temporal": {
            "coverage": {
                "scope": "returned_records",
                "status": "zero_matches",
                "note": "No returned matches; counts describe the metadata candidate scope only.",
            }
        },
        **({"body_scope": body_scope} if body_scope else {}),
        **(dict(date_unknown_candidates=date_unknown_candidates)
           if date_unknown_candidates is not None else {}),
        **(dict(date_exclusions=date_exclusions)
           if date_exclusions is not None else {}),
    }


def search(*, con, kind="command_output", pattern, device=None, snapshot=None,
           commands=None, ids=None, date_from=None, date_to=None,
           page=1, offset=None, limit=None, candidate_budget=CANDIDATE_BUDGET,
           deadline_ms=DEFAULT_DEADLINE_MS):
    """Two-phase evidence search over ``netops.raw_output_store``.

    Returns a dict whose ``total`` is the complete match count for the full
    filtered scope and whose ``matches`` is one page.  ``path`` reports whether
    the body phase was scoped to bounded metadata candidates
    (``candidate_body``), skipped (``metadata_only``) or widened to one complete
    SQL count/page (``full_scan``).  Counts are never derived from the page.

    ``identity_scope`` says what ``source_id`` identifies: ``record_id`` when
    the store carries a real id column, else ``record_key`` — the
    ``(snapshot_id, device_name, command)`` key, which covers every body stored
    under that key (``content_hash`` separates those bodies).  ``has_more`` and
    the page order are stable for both scopes: rows are ordered by the key,
    then ``content_hash``, then ``source_id``.
    """
    if kind not in ("command_output", "config"):
        raise EvidenceError("invalid_argument", "kind must be command_output or config")
    if not isinstance(pattern, str) or not pattern.strip():
        raise EvidenceError("invalid_argument", "pattern must be a non-empty substring")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise EvidenceError("invalid_argument", "page must be an integer >= 1")
    if limit is None:
        limit = MAX_PAGE_ROWS
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_ROWS:
        raise EvidenceError("invalid_argument", f"limit must be an integer 1..{MAX_PAGE_ROWS}")
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
        raise EvidenceError("invalid_argument", "offset must be a non-negative integer")
    if isinstance(candidate_budget, bool) or not isinstance(candidate_budget, int) or candidate_budget < 1:
        raise EvidenceError("invalid_argument", "candidate_budget must be a positive integer")
    date_from = _valid_date(date_from, "date_from")
    date_to = _valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise EvidenceError("invalid_argument", "date_from must not be after date_to")
    commands = _normalize_list(commands, "commands")
    ids = _normalize_list(ids, "ids")

    if offset is None:
        offset = (page - 1) * limit
    else:
        page = offset // limit + 1
    filters = {
        "device": device,
        "snapshot": snapshot,
        "snapshot_semantics": "last-writer label in raw_output_store, not a partition key",
        "commands": commands,
        "ids": ids,
        "date_from": date_from,
        "date_to": date_to,
        "kind": kind,
    }
    budget = {
        "page_size": limit,
        "candidate_budget": candidate_budget,
        "deadline_ms": deadline_ms,
        "fallback": None,
    }

    deadline = _Deadline(con, deadline_ms)
    try:
        columns = table_columns(con, deadline)
        required = {"device_name", "command", "snapshot_id", "raw_output"}
        if not columns:
            raise EvidenceError(
                "source_missing",
                "netops.raw_output_store is absent from this database",
                {"table": "netops.raw_output_store"},
            )
        if not required <= columns:
            raise EvidenceError(
                "schema_drift",
                "raw_output_store is missing required columns",
                {"missing": sorted(required - columns), "columns": sorted(columns)},
            )
        id_column = identity_column(columns)
        meta_where, meta_params, basis, basis_status = _meta_filters(
            columns, kind=kind, device=device, snapshot=snapshot, commands=commands,
            ids=ids, date_from=date_from, date_to=date_to, id_column=id_column,
        )
        # One metadata aggregate decides both counts: the candidate total and
        # how many candidates carry a NULL real id (same scan, no body text).
        candidate_counts = _run(
            con,
            "SELECT count(*), count(*) FILTER (WHERE "
            + (f"r.{id_column} IS NULL" if id_column else "FALSE")
            + ") FROM netops.raw_output_store r" + _where(meta_where),
            meta_params,
            deadline,
        )[0]
        candidate_total = int(candidate_counts[0])
        null_id_candidates = int(candidate_counts[1] or 0) if id_column else 0
        identity_scope, identity_fallback = _identity_scope(id_column, null_id_candidates)

        date_exclusions = None
        date_unknown_candidates = None
        date_scope_total = None
        if date_from or date_to:
            # Explain every row the date semantics cannot attribute to a day,
            # even when other rows do match.  A zero (or a short page) must not
            # be read as proof that no such record exists.  Metadata only: this
            # never reads a body.
            undated_where, undated_params, _, _ = _meta_filters(
                columns, kind=kind, device=device, snapshot=snapshot,
                commands=commands, ids=ids, date_from=None, date_to=None,
                id_column=id_column,
            )
            reason_sql = _date_reason_sql(columns, "r")
            counts = _run(
                con,
                "WITH scope AS (SELECT " + reason_sql + " AS reason"
                " FROM netops.raw_output_store r" + _where(undated_where) + ") "
                "SELECT count(*), "
                f"count(*) FILTER (WHERE reason = '{INVALID_VALUE}'), "
                f"count(*) FILTER (WHERE reason = '{CONFLICTING_CAPTURE_TIME}'), "
                f"count(*) FILTER (WHERE reason = '{NO_CAPTURE_TIME}')"
                " FROM scope",
                undated_params,
                deadline,
            )[0]
            date_scope_total = int(counts[0])
            date_exclusions = {
                INVALID_VALUE: int(counts[1] or 0),
                CONFLICTING_CAPTURE_TIME: int(counts[2] or 0),
                NO_CAPTURE_TIME: int(counts[3] or 0),
            }
            date_unknown_candidates = sum(date_exclusions.values())

        if candidate_total == 0:
            store_total = candidate_total if not meta_where else _run(
                con, "SELECT count(*) FROM netops.raw_output_store r", [], deadline
            )[0][0]
            zero_reason = "store_empty" if store_total == 0 else "no_candidates"
            if store_total and date_scope_total:
                # The scope has rows but none has a known date inside the
                # requested range; the exclusion counts above say why.
                zero_reason = "no_dated_candidates"
            return _empty_result(
                kind=kind, page=page, page_size=limit, offset=offset,
                candidate_total=0, path="metadata_only", zero_reason=zero_reason,
                filters=filters, basis=basis, budget=budget,
                basis_status=basis_status, identity_scope=identity_scope,
                identity_fallback=identity_fallback,
                date_unknown_candidates=date_unknown_candidates,
                date_exclusions=date_exclusions,
            )

        fallback = candidate_total > candidate_budget
        budget["fallback"] = "full_scan" if fallback else None
        if fallback:
            join_sql = ""
            join_params: list = []
            path = "full_scan"
        else:
            # One complete key set, materialised by SQL: no NULL key can be
            # dropped by a VALUES/``=`` join, and no per-key parameter list can
            # grow with the budget.  candidate_total <= budget here, so the set
            # is complete, not sampled.
            join_sql = (
                " JOIN (SELECT DISTINCT device_name, command, snapshot_id "
                "FROM netops.raw_output_store r" + _where(meta_where) +
                f" LIMIT {int(candidate_budget)})" + _join_on_keys_sql()
            )
            join_params = list(meta_params)
            path = "candidate_body"

        body_where = ["r.raw_output ILIKE ?"]
        match_total = _run(
            con,
            "SELECT count(*) FROM netops.raw_output_store r" + join_sql +
            _where(body_where + meta_where),
            join_params + [f"%{pattern}%"] + meta_params,
            deadline,
        )[0][0]

        if match_total == 0:
            return _empty_result(
                kind=kind, page=page, page_size=limit, offset=offset,
                candidate_total=candidate_total, path=path,
                zero_reason="no_body_match", filters=filters, basis=basis, budget=budget,
                basis_status=basis_status, identity_scope=identity_scope,
                identity_fallback=identity_fallback,
                date_unknown_candidates=date_unknown_candidates,
                date_exclusions=date_exclusions,
            )

        # content_hash is computed once here: it is both the per-body digest and
        # the tie-break that keeps duplicate keys from shifting between pages.
        hits_columns = [
            "r.device_name AS device_name",
            "r.command AS command",
            "r.snapshot_id AS snapshot_id",
            "r.raw_output AS raw_output",
            "strpos(lower(r.raw_output), lower(?)) AS mp",
            "md5(r.raw_output) AS content_hash",
            "length(r.raw_output) AS body_chars",
            _source_id_expr("r", id_column) + " AS source_id",
            _identity_basis_expr("r", id_column) + " AS identity_basis",
        ] + [f"r.{name} AS {name}" for name in _optional_columns(columns)]
        outer_optional = ", ".join(_optional_columns(columns))
        page_sql = (
            "WITH hits AS (SELECT " + ", ".join(hits_columns) +
            " FROM netops.raw_output_store r" + join_sql +
            _where(body_where + meta_where) + ") "
            "SELECT device_name, command, snapshot_id, source_id, content_hash, "
            "CASE WHEN mp > 0 THEN array_length(string_split("
            f"substring(raw_output, 1, mp - 1), chr(10)), 1) END AS match_line, "
            "CASE WHEN mp > 0 THEN array_length(string_split("
            f"substring(raw_output, 1, GREATEST(1, mp - {EXCERPT_LEAD}) - 1), chr(10)), 1) "
            "ELSE 1 END AS excerpt_line_start, "
            "CASE WHEN mp > 0 THEN array_length(string_split("
            f"substring(raw_output, 1, GREATEST(1, mp - {EXCERPT_LEAD}) + {EXCERPT_CHARS} - 1), "
            "chr(10)), 1) ELSE array_length(string_split("
            f"substring(raw_output, 1, {FALLBACK_EXCERPT_CHARS}), chr(10)), 1) END AS excerpt_line_end, "
            "CASE WHEN mp > 0 THEN substring("
            f"raw_output, GREATEST(1, mp - {EXCERPT_LEAD}), {EXCERPT_CHARS}) "
            f"ELSE substring(raw_output, 1, {FALLBACK_EXCERPT_CHARS}) END AS excerpt, "
            # The excerpt is bounded by the SQL above, so truncation is a
            # property of the row, not a guess made after fetching the body.
            "CASE WHEN mp > 0 THEN (GREATEST(1, mp - " + str(EXCERPT_LEAD) + ") > 1 OR "
            f"(GREATEST(1, mp - {EXCERPT_LEAD}) + {EXCERPT_CHARS} - 1) < body_chars) "
            f"ELSE ({FALLBACK_EXCERPT_CHARS} < body_chars) END AS excerpt_truncated, "
            "body_chars, identity_basis"
            + (", " + outer_optional if outer_optional else "") +
            " FROM hits ORDER BY snapshot_id NULLS LAST, device_name NULLS LAST, "
            "command NULLS LAST, content_hash NULLS LAST, source_id NULLS LAST "
            "LIMIT ? OFFSET ?"
        )
        raw_rows = _run(
            con,
            page_sql,
            [pattern] + join_params + [f"%{pattern}%"] + meta_params + [limit, offset],
            deadline,
        )
        names = [
            "device", "command", "snapshot_id", "source_id", "content_hash",
            "match_line", "excerpt_line_start", "excerpt_line_end", "excerpt",
            "excerpt_truncated", "body_chars", "identity_basis",
        ] + _optional_columns(columns)
        matches = [dict(zip(names, row)) for row in raw_rows]
        returned = len(matches)
        excerpt_truncated_rows = sum(1 for row in matches if row.get("excerpt_truncated"))
        has_more = offset + returned < match_total
        return {
            "status": "success",
            "source": kind,
            "source_state": "present",
            "matches": matches,
            "returned": returned,
            "total": match_total,
            "candidate_total": candidate_total,
            "page": page,
            "page_size": limit,
            "offset": offset,
            "has_more": has_more,
            # A shortened excerpt is real truncation even when the page is the
            # last one: never report success/truncated=false over cut text.
            "truncated": has_more or bool(excerpt_truncated_rows),
            "path": path,
            "zero_reason": None,
            "filters": filters,
            "collection_basis": basis,
            "collection_basis_status": basis_status,
            "date_exclusions": date_exclusions,
            "date_unknown_candidates": date_unknown_candidates,
            "identity_scope": identity_scope,
            "identity_fallback": identity_fallback,
            "excerpt_truncated_rows": excerpt_truncated_rows,
            "body_scope": "bounded_excerpt",
            "budget": budget,
            "temporal": {
                "coverage": {
                    "scope": "returned_records",
                    "status": "per_record",
                    "note": "Time metadata covers returned matches only; total/candidate counts are scope counts, not a time range.",
                }
            },
        }
    finally:
        deadline.stop()


SYSLOG_REQUIRED_COLUMNS = ("timestamp", "host", "severity", "facility", "message")


def search_syslog(*, con, parquet_glob, pattern, device=None, date_from=None,
                  date_to=None, hours=24, page=1, offset=None, limit=None,
                  deadline_ms=DEFAULT_DEADLINE_MS):
    """Same metadata-first contract over the Parquet syslog store.

    Missing Parquet files are reported as a zero-row ``source_missing`` result
    (not as a confident "no logs"), while a *present* store whose schema lacks a
    required column is ``schema_drift`` — a Binder error for a missing column
    must not be mistaken for an absent source.  Line numbers are always 1: one
    message is one record.

    The body is never fetched whole: ``message`` is a SQL-side ``substring``
    around the match (``EXCERPT_CHARS``, ``EXCERPT_LEAD`` chars of lead-in), so
    a 200 KB single-line record cannot land on the wire.  Each row reports
    ``message_chars`` (full length) and ``excerpt_truncated``; ``content_hash``
    is the digest of the *full* message, computed in the database.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise EvidenceError("invalid_argument", "pattern must be a non-empty substring")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise EvidenceError("invalid_argument", "page must be an integer >= 1")
    if limit is None:
        limit = MAX_PAGE_ROWS
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_ROWS:
        raise EvidenceError("invalid_argument", f"limit must be an integer 1..{MAX_PAGE_ROWS}")
    date_from = _valid_date(date_from, "date_from")
    date_to = _valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise EvidenceError("invalid_argument", "date_from must not be after date_to")
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1:
        raise EvidenceError("invalid_argument", "hours must be a positive integer")
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
        raise EvidenceError("invalid_argument", "offset must be a non-negative integer")

    if offset is None:
        offset = (page - 1) * limit
    else:
        page = offset // limit + 1
    filters = {
        "device": device, "date_from": date_from, "date_to": date_to,
        "hours": None if (date_from or date_to) else hours,
    }
    budget = {"page_size": limit, "candidate_budget": None, "deadline_ms": deadline_ms, "fallback": None}
    where: list[str] = []
    params: list = []
    if date_from:
        where.append("TRY_CAST(timestamp AS TIMESTAMP) >= CAST(? AS DATE)")
        params.append(date_from)
    if date_to:
        where.append("TRY_CAST(timestamp AS TIMESTAMP) < CAST(? AS DATE) + INTERVAL 1 DAY")
        params.append(date_to)
    if not (date_from or date_to):
        where.append(
            f"TRY_CAST(timestamp AS TIMESTAMP) >= CAST(now() AS TIMESTAMP) - INTERVAL '{hours} hours'"
        )
    if device:
        where.append("host ILIKE ?")
        params.append(f"%{device}%")

    source = "read_parquet(?, union_by_name=true)"
    deadline = _Deadline(con, deadline_ms)
    try:
        # Validate the schema before any predicate references a column, so a
        # present Parquet store with a missing column is schema drift and a
        # store that is not there at all stays source_missing.
        #
        # Per file, not per glob: ``union_by_name=true`` makes a glob with mixed
        # schemas look complete by NULL-filling the file that lacks a column,
        # and those rows then match nothing without saying why.  ``parquet_schema``
        # reads footers only (no row scan) and reports one row per column per file.
        try:
            described = _run(
                con,
                "SELECT file_name, name FROM parquet_schema(?) WHERE type IS NOT NULL",
                [parquet_glob], deadline,
            )
        except EvidenceError as exc:
            if exc.kind == "source_missing":
                return _empty_result(
                    kind="syslog", page=page, page_size=limit, offset=offset,
                    candidate_total=0, path="metadata_only",
                    zero_reason="source_missing", filters=filters,
                    basis=None, budget=budget, source_state="missing",
                    identity_scope="syslog_content_hash", body_scope="bounded_excerpt",
                )
            raise
        files: dict[str, set[str]] = {}
        for file_name, name in described:
            files.setdefault(str(file_name), set()).add(str(name).lower())
        if not files:
            return _empty_result(
                kind="syslog", page=page, page_size=limit, offset=offset,
                candidate_total=0, path="metadata_only",
                zero_reason="source_missing", filters=filters,
                basis=None, budget=budget, source_state="missing",
                identity_scope="syslog_content_hash", body_scope="bounded_excerpt",
            )
        incomplete = {
            path: sorted(set(SYSLOG_REQUIRED_COLUMNS) - columns)
            for path, columns in sorted(files.items())
            if set(SYSLOG_REQUIRED_COLUMNS) - columns
        }
        if incomplete:
            raise EvidenceError(
                "schema_drift",
                "syslog Parquet store has files missing required columns; "
                "a union would NULL-fill them and silently match nothing",
                {
                    "source": "syslog",
                    "required": list(SYSLOG_REQUIRED_COLUMNS),
                    "incomplete_files": dict(list(incomplete.items())[:20]),
                    "incomplete_file_count": len(incomplete),
                    "file_count": len(files),
                },
            )

        candidate_total = _run(
            con, f"SELECT count(*) FROM {source}" + _where(where),
            [parquet_glob] + params, deadline,
        )[0][0]
        body_where = ["message ILIKE ?"]
        match_total = _run(
            con, f"SELECT count(*) FROM {source}" + _where(body_where + where),
            [parquet_glob, f"%{pattern}%"] + params, deadline,
        )[0][0]
        if match_total == 0:
            return _empty_result(
                kind="syslog", page=page, page_size=limit, offset=offset,
                candidate_total=candidate_total, path="full_scan" if candidate_total else "metadata_only",
                zero_reason="no_body_match" if candidate_total else "no_candidates",
                filters=filters, basis=None, budget=budget,
                identity_scope="syslog_content_hash", body_scope="bounded_excerpt",
            )
        # The excerpt, the digest and the truncation flag are all computed by
        # the database from the same row: the full message never leaves DuckDB.
        excerpt_sql = (
            "CASE WHEN mp > 0 THEN substring("
            f"message, GREATEST(1, mp - {EXCERPT_LEAD}), {EXCERPT_CHARS}) "
            f"ELSE substring(message, 1, {FALLBACK_EXCERPT_CHARS}) END"
        )
        truncated_sql = (
            "CASE WHEN mp > 0 THEN (GREATEST(1, mp - " + str(EXCERPT_LEAD) + ") > 1 OR "
            f"(GREATEST(1, mp - {EXCERPT_LEAD}) + {EXCERPT_CHARS} - 1) < message_chars) "
            f"ELSE ({FALLBACK_EXCERPT_CHARS} < message_chars) END"
        )
        page_rows = _run(
            con,
            "WITH hits AS (SELECT timestamp, host, severity, facility, message, "
            "strpos(lower(message), lower(?)) AS mp, "
            "md5(coalesce(cast(host AS VARCHAR), '') || chr(31) || "
            "coalesce(cast(timestamp AS VARCHAR), '') || chr(31) || "
            "coalesce(cast(facility AS VARCHAR), '') || chr(31) || message) AS content_hash, "
            "length(message) AS message_chars "
            f"FROM {source}" + _where(body_where + where) + ") "
            "SELECT timestamp, host, severity, facility, "
            f"{excerpt_sql} AS message, {truncated_sql} AS excerpt_truncated, "
            "message_chars, content_hash "
            "FROM hits ORDER BY timestamp DESC NULLS LAST, host NULLS LAST, "
            "content_hash NULLS LAST LIMIT ? OFFSET ?",
            # Textual placeholder order: the CTE's SELECT list comes before its
            # FROM/WHERE, so the match-position parameter binds first.  The
            # position search takes the raw pattern; only ILIKE takes wildcards.
            [pattern, parquet_glob, f"%{pattern}%"] + params + [limit, offset],
            deadline,
        )
        matches = [
            {
                "timestamp": row[0], "host": row[1], "severity": row[2],
                "facility": row[3], "message": row[4],
                "excerpt_truncated": bool(row[5]), "message_chars": row[6],
                "source_id": "log_" + str(row[7]),
                "content_hash": row[7], "match_line": 1,
                "excerpt_line_start": 1, "excerpt_line_end": 1,
            }
            for row in page_rows
        ]
        returned = len(matches)
        excerpt_truncated_rows = sum(1 for row in matches if row["excerpt_truncated"])
        has_more = offset + returned < match_total
        return {
            "status": "success",
            "source": "syslog",
            "source_state": "present",
            "matches": matches,
            "returned": returned,
            "total": match_total,
            "candidate_total": candidate_total,
            "page": page,
            "page_size": limit,
            "offset": offset,
            "has_more": has_more,
            "truncated": has_more or bool(excerpt_truncated_rows),
            "path": "full_scan",
            "zero_reason": None,
            "filters": filters,
            "collection_basis": None,
            "collection_basis_status": None,
            "identity_scope": "syslog_content_hash",
            "identity_fallback": None,
            "excerpt_truncated_rows": excerpt_truncated_rows,
            "body_scope": "bounded_excerpt",
            "budget": budget,
            "temporal": {
                "coverage": {
                    "scope": "returned_records",
                    "status": "per_record",
                    "note": "Time metadata covers returned matches only; total/candidate counts are scope counts.",
                }
            },
        }
    finally:
        deadline.stop()
