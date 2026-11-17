#!/usr/bin/env python3
"""query_evidence — unified evidence drilldown with complete, paged counts.

R-VERTICAL-SLICE Step 1 (2026-05-09, dev_docs/70); OW-046 (2026-10-20).

One function, three sources via Literal arg routing.  Designed for the
investigate sub-agent — narrow capability ("find recorded events /
outputs / config matching pattern P on device D"), single entry point,
no selection ambiguity for small-model LLMs.

Sources:
* ``syslog``         — read syslog parquet partitions in
                       ``.olav/databases/syslogs/YYYY-MM-DD/syslog-HH.parquet``
* ``command_output`` — read ``netops.raw_output_store`` (per-snapshot
                       per-device CLI command stdout)
* ``config``         — read ``netops.raw_output_store`` filtered to
                       ``running-config`` / ``startup-config`` commands

Metadata first: collection date, device, command and record IDs narrow the
*complete* candidate set before any body text is matched.  ``total`` is the
exact number of matches in that full filtered scope; ``matches`` is one page
bounded by ``limit`` and ``offset``/``page``.  Each row carries a stable
``source_id``, a content ``content_hash`` and real excerpt line numbers.
``identity_scope`` states whether ``source_id`` is the store's real record id
(``record_id``), the record-key hash (``record_key``), or a real-id store where
some rows have a NULL id and therefore fall back to the key hash
(``record_id_with_record_key_fallback``, named in ``identity_fallback``).
Duplicate keys with different bodies page stably because ordering adds
``content_hash`` and ``source_id`` as ties.  A returned body is always a
bounded excerpt: ``body_scope`` / ``excerpt_truncated`` / ``message_chars`` say
so per call and per row, and ``truncated`` is never false over cut text.

Failure kinds are distinct: ``invalid_argument``, ``source_missing``,
``permission_denied``, ``schema_drift``, ``sql_error`` and
``budget_exceeded``.  A successful empty lookup carries ``zero_reason``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import duckdb

from olav_skills.core.config import MAIN_DB_PATH
from olav_skills.netops.core.query.evidence import (
    DEFAULT_DEADLINE_MS,
    MAX_PAGE_ROWS,
    EvidenceError,
    classify_exception,
    search,
    search_syslog,
)
from olav_skills.core.query_result import char_truncation
from olav_skills.netops.core.query.result_contract import adapt_evidence
from olav_skills.netops.core.query.temporal import record_temporal, time_value


_LOG_GLOB = ".olav/databases/syslogs/*/syslog-*.parquet"

# Aggregate char budget per call — prevents a broad pattern (no device filter)
# from dumping 50 × 1200-char excerpts (~60 K chars) into context.  Forces the
# agent to be specific: add device= or narrow the pattern to get more rows.
_MAX_RESULT_CHARS = 12_000  # ≈ 3 K tokens; fits ~10 good excerpts comfortably
# A cut row smaller than this is useless to a reader, so it is omitted (and
# reported) instead of being kept as a stub.
_MIN_ROW_CHARS = 512
# Fields that carry record text; only these are ever cut by the char budget.
_ROW_TEXT_FIELDS = ("message", "excerpt", "raw_output")


def _row_chars(row: dict) -> int:
    return sum(len(str(value)) for value in row.values())


def _shrink_row(row: dict, max_chars: int) -> tuple[dict, bool]:
    """Cut one over-budget row's text until it fits; never drop the row.

    The first row used to bypass the budget entirely, so a single 200 KB syslog
    message reached the wire as ``success`` / ``truncated=false``.  The row is
    kept (it is a real match) and only tail text is lost, which is reported in
    the row and in the call-level ``truncated``.
    """
    row = dict(row)
    shrunk: list[str] = []
    while _row_chars(row) > max_chars:
        field = max(
            (name for name in _ROW_TEXT_FIELDS
             if isinstance(row.get(name), str) and row[name]),
            key=lambda name: len(row[name]),
            default=None,
        )
        if field is None:  # nothing left to cut: only metadata remains
            break
        overflow = _row_chars(row) - max_chars
        keep = len(row[field]) - overflow
        row[field] = row[field][:keep] if keep > 0 else ""
        shrunk.append(field)
        # Markers cost chars too, so they are added before the size is re-checked.
        row["char_budget_truncated"] = True
        row["char_budget_fields"] = sorted(set(shrunk))
        row["excerpt_truncated"] = True
    return row, bool(shrunk)


_DATE_EXCLUSION_TEXT = {
    "invalid_value": "invalid collection time",
    "conflicting_capture_time": "conflicting collection time",
    "no_capture_time": "no collection time",
}


def _exclusion_breakdown(exclusions: dict | None) -> str:
    if not isinstance(exclusions, dict):
        return ""
    parts = [
        f"{int(exclusions.get(reason) or 0)} {label}"
        for reason, label in _DATE_EXCLUSION_TEXT.items()
    ]
    return ", ".join(parts)


def _date_scope(out: dict, *, source: str, date_from, date_to) -> dict:
    """Describe the date predicate using only helper fields and call inputs.

    ``date_unknown_candidates`` counts *candidate records* excluded after the
    other metadata filters and before the date-range filter because their
    collection time is unknown/invalid/conflicting.  They are never body-match
    counts.  The helper computes ``date_scope_total`` internally but does not
    return it, so no denominator is invented here: it is NOT
    ``candidate_total + date_unknown_candidates`` (records with a known date
    outside the requested range are also excluded).

    Only a raw-store call with a date filter has that pre-date denominator
    contract.  The ``not_applicable`` branches (``source=syslog``, or no
    ``date_from``/``date_to``) must not inherit the raw descriptions: nothing
    was computed or withheld there, so their scope/status/notes say so and the
    denominator stays null (not zero).
    """
    applied = bool(date_from or date_to)
    date_filter = {"from": date_from, "to": date_to} if applied else None
    base = {
        "applied": applied,
        "filter": date_filter,
        "excluded_are_body_matches": False,
        "unknown_candidates_are_body_matches": False,
        "excluded_candidates_total": None,
    }
    if source == "syslog":
        base.update({
            "excluded_scope": "not_applicable",
            "known_dated_candidates": None,
            "excluded_candidates_total_status": "not_applicable",
            "excluded_candidates_total_note": (
                "The raw_output_store pre-date candidate total is not part of the "
                "syslog contract, so no such total is computed, withheld or "
                "returned here. Do not present a denominator."
            ),
            "total_scope": "not_applicable",
            "total_is_complete_note": (
                "total_is_complete covers the syslog source filters; the "
                "raw_output_store collection-date exclusion contract and its "
                "pre-date denominator do not apply to syslog."
            ),
            "status": "not_applicable",
            "reason": "syslog_time_semantics",
            "unknown_candidates": None,
            "exclusions": None,
            "note": (
                "This collection-date exclusion contract covers raw_output_store "
                "only; syslog event timestamps are filtered by the syslog source. "
                "No unknown/excluded candidate count is reported here."
            ),
        })
        return base
    if not applied:
        base.update({
            "excluded_scope": "not_applicable",
            "known_dated_candidates": None,
            "excluded_candidates_total_status": "not_applicable",
            "excluded_candidates_total_note": (
                "No date_from/date_to was supplied, so no pre-date exclusion and "
                "no pre-date denominator was computed or withheld. Do not present "
                "a denominator."
            ),
            "total_scope": "all_metadata_filtered_candidates",
            "total_is_complete_note": (
                "total_is_complete covers all metadata-filtered candidates, "
                "including records whose collection time is unknown, invalid or "
                "conflicting, because no date predicate was applied."
            ),
            "status": "not_applicable",
            "reason": "no_date_filter",
            "unknown_candidates": None,
            "exclusions": None,
            "note": (
                "No date_from/date_to was supplied, so no date exclusion was "
                "computed; unknown_candidates stays null (unknown), not zero."
            ),
        })
        return base

    base.update({
        "excluded_scope": "metadata_candidates_after_other_filters_before_date_filter",
        "known_dated_candidates": out.get("candidate_total"),
        "excluded_candidates_total_status": "unknown_not_exposed_by_helper",
        "excluded_candidates_total_note": (
            "The helper computes the pre-date candidate total internally but does "
            "not return it. It is not candidate_total + date_unknown_candidates: "
            "records with a known date outside the requested range are excluded "
            "too. Do not infer or present a denominator."
        ),
        "total_scope": "known_date_predicate_only",
        "total_is_complete_note": (
            "total_is_complete describes only the known-date predicate for these "
            "filters; it never claims full time coverage of the store."
        ),
    })
    unknown = out.get("date_unknown_candidates")
    exclusions = out.get("date_exclusions")
    known = out.get("candidate_total") or 0
    zero_reason = out.get("zero_reason")
    if unknown is None:
        status = "unknown_not_exposed"
    elif unknown > 0 and known > 0:
        status = "partial_unknown"
    elif unknown > 0:
        status = "unknown_only"
    elif zero_reason == "no_dated_candidates":
        status = "known_outside_range"
    else:
        status = "known_only"
    base.update({
        "status": status,
        "reason": zero_reason,
        "unknown_candidates": unknown,
        "exclusions": exclusions,
    })
    if unknown is None:
        base["note"] = (
            "The helper did not expose date exclusion counts for this filtered "
            "call; they stay unknown (null), not zero."
        )
    elif unknown > 0:
        details = _exclusion_breakdown(exclusions)
        base["disclosure"] = (
            f"{unknown} candidate record(s) were excluded after the other metadata "
            f"filters and before the date filter because their collection time is "
            f"unusable ({details}). Whether they fall inside the requested date, "
            "and whether their bodies match the pattern, was not evaluated. They "
            "are not body-match counts. total covers only known-date matches."
        )
    elif status == "known_outside_range":
        base["disclosure"] = (
            "Candidate records remain after the other metadata filters, but none "
            "has a known collection date inside the requested range (no excluded "
            "unknown-time record was counted). Widen the date range; this is not "
            "proof that no matching record exists."
        )
    return base


def _trim_to_char_budget(rows: list[dict], max_chars: int) -> tuple[list[dict], int, int]:
    """Bound the page text; returns ``(rows, omitted, char_truncated_rows)``.

    Rows are kept whole while they fit.  The row that would overflow keeps its
    match metadata with cut text (counted in ``char_truncated_rows``); rows after
    it are dropped (counted in ``omitted``) so ``next_offset`` can resume.
    """
    total = 0
    kept: list[dict] = []
    char_truncated_rows = 0
    for row in rows:
        size = _row_chars(row)
        remaining = max_chars - total
        if size <= remaining:
            kept.append(row)
            total += size
            continue
        if remaining >= _MIN_ROW_CHARS:
            cut, shrunk = _shrink_row(row, remaining)
            kept.append(cut)
            total += _row_chars(cut)
            char_truncated_rows += 1 if shrunk else 0
        break
    return kept, len(rows) - len(kept), char_truncated_rows


# In-process call dedup — boundary test 2 saw 16 query_evidence calls
# trying pattern variations.  The hint message in empty results steers
# the LLM, but for repeat-identical-args this is the hard backstop.
import threading as _threading
_call_lock = _threading.Lock()
_call_counts: dict[tuple, int] = {}
_DUP_LIMIT = 2  # third identical call returns cached "stop trying"


def _budget_check(args_key: tuple) -> dict | None:
    with _call_lock:
        n = _call_counts.get(args_key, 0)
        _call_counts[args_key] = n + 1
    if n + 1 > _DUP_LIMIT:
        return {
            "status": "error",
            "error_kind": "duplicate_call_budget",
            "message": (
                f"This (source, pattern, device, time_range, snapshot, filters) "
                f"combination has already been queried {n+1} times in "
                f"this session.  The result is the same.  Move on — "
                f"either pivot to a different source/pattern, or accept "
                f"the result you already have.  Repeated identical "
                f"queries waste context budget."
            ),
            "args_key": [str(part) for part in args_key],
        }
    return None


def _error(source: str, error: EvidenceError) -> dict[str, Any]:
    return {
        "status": "error",
        "error_kind": error.kind,
        "message": error.message,
        "source": source,
        "detail": error.detail,
        "recoverable": error.kind in ("invalid_argument", "budget_exceeded", "schema_drift"),
    }


def _shape_raw_rows(rows: list[dict]) -> None:
    for row in rows:
        row["temporal"] = record_temporal(row)
        # Compatibility field: only an explicit raw capture timestamp qualifies.
        # Inferred date labels and record update clocks are never captures.
        row["captured_at"] = row.get("captured_at") or row.get("collected_at")
        row["captured_at_note"] = (
            "Explicit raw capture only; null when unavailable. "
            "See temporal.collected_at for inferred date labels."
        )


def _shape_syslog_rows(rows: list[dict]) -> None:
    for row in rows:
        row["temporal"] = record_temporal({}, scope="returned_syslog_record")
        row["temporal"]["event_time"] = time_value(row.get("timestamp"), "syslog.timestamp")


def query_evidence(
    source: Literal["syslog", "command_output", "config"],
    pattern: str,
    device: str | None = None,
    time_range: str | None = None,
    snapshot: str | None = None,
    *,
    commands: list[str] | None = None,
    ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    offset: int | None = None,
    limit: int | None = None,
    deadline_ms: int | None = DEFAULT_DEADLINE_MS,
    raw_scope: str = "current_labels",
) -> dict[str, Any]:
    """Drill into recorded evidence for fault analysis.

    One unified evidence query — picks the data source via ``source``.
    Use this when user asks "why" / "show me logs" / "what does R3's
    config say about X" / "what error did we see" — anything where
    LLM needs to look at *recorded text* rather than structured graph
    state.

    Args:
        source: Which evidence store to query.
            * ``"syslog"``         — historical syslog (parquet)
            * ``"command_output"`` — captured CLI stdout
            * ``"config"``         — running-config / startup-config text
        pattern: Required. Substring to match (case-insensitive).
            E.g. ``"BGP"``, ``"OSPF dead"``, ``"NATIVE_VLAN_MISMATCH"``.
        device: Optional hostname filter.  Substring match.
        time_range: Legacy syslog window (``"last_1h"``/``"last_24h"``/
            ``"last_7d"``).  ``date_from``/``date_to`` are explicit and
            preferred.
        snapshot: Optional snapshot scope (command_output/config).
        commands: Optional exact command list (command_output/config).
        ids: Optional record IDs, as returned in ``matches[].source_id``.
        date_from, date_to: Optional inclusive collection-date scope
            (``YYYY-MM-DD``).  Uses an explicit collection column when the
            store has one; otherwise a bare ``YYYY-MM-DD`` ``snapshot_id``
            label is the accepted derived basis (``collection_basis_status=
            'derived'``, the same label ``temporal.collected_at`` exposes).
            Rows whose label is not a plain date stay unknown, so an empty
            result says ``zero_reason='no_dated_candidates'`` rather than
            claiming no match.  Ingest/update clocks never qualify.
        page: 1-based page number.
        offset: Optional row offset; overrides ``page`` when supplied so a page
            shortened by the character budget can be resumed without gaps.
        limit: Page size, 1..50 (default 50).
        deadline_ms: Wall-clock budget; exhaustion is ``budget_exceeded``.

    Returns:
        ``{
            "status": "success",
            "source": str,
            "matches": [...],        # one bounded page
            "total": int,            # complete match count for the filters
            "candidate_total": int,  # complete metadata candidate count
            "returned": int, "page": int, "page_size": int,
            "has_more": bool, "truncated": bool,
            "total_scope": "filtered_scope", "total_is_complete": True,
            "next_offset": int | None,
            "path": "candidate_body" | "full_scan" | "metadata_only",
            "zero_reason": str | None,
            "date_unknown_candidates": int | None,
            "date_exclusions": {...} | None,
            "date_scope": {...},      # status + exact exclusion scope/limits
            "identity_scope": "record_id" | "record_key"
                              | "record_id_with_record_key_fallback",
            "identity_fallback": str | None,
            "body_scope": "bounded_excerpt" | None,
            "excerpt_truncated_rows": int,
            "budget": {...}, "filters": {...},
        }``
        Rows carry ``source_id``, ``content_hash``, ``match_line``,
        ``excerpt_line_start``/``excerpt_line_end`` and the existing
        ``excerpt`` + temporal fields.  ``identity_scope`` says whether
        ``source_id`` is the store's real record id or the record-key hash
        (which can cover several bodies of one key; ``content_hash``
        separates them); a real-id store whose rows include a NULL id says
        ``record_id_with_record_key_fallback`` and names the rule in
        ``identity_fallback``.  Errors carry ``error_kind``.

    Example:
        >>> query_evidence(source="command_output", pattern="BGP",
        ...                device="R3", snapshot="snap-1", page=1)
    """
    from olav_skills.netops.core.query import warehouse as _warehouse
    if _warehouse.configured():
        return _warehouse.query_evidence(source, pattern, device, time_range, snapshot, commands=commands, ids=ids, date_from=date_from, date_to=date_to, page=page, offset=offset, limit=limit, deadline_ms=deadline_ms, raw_scope=raw_scope)
    if raw_scope != "current_labels":
        return _warehouse.failure("INVALID_REQUEST", "raw_scope_requires_candidate")
    # Per-args dedup budget: 3rd identical call returns a fast-fail.
    args_key = (
        source, pattern, device, time_range, snapshot,
        tuple(commands or ()), tuple(ids or ()), date_from, date_to, page, offset, limit,
    )
    budget = _budget_check(args_key)
    if budget is not None:
        return budget

    # Reject empty / whitespace pattern up front — would dump the
    # entire syslog parquet (~14k rows) or full command output table
    # without bound, busting the LLM's context.
    if not pattern or not pattern.strip():
        return {
            "status": "error",
            "error_kind": "invalid_argument",
            "message": (
                "pattern must be a non-empty substring.  Use a real "
                "filter like 'BGP' / 'OSPF dead' / 'NATIVE_VLAN' — "
                "an empty pattern would return tens of thousands of "
                "rows."
            ),
            "source": source,
        }
    if source not in ("syslog", "command_output", "config"):
        return {
            "status": "error",
            "error_kind": "invalid_argument",
            "message": f"unknown source {source!r}; pick from "
                       f"syslog / command_output / config",
            "source": source,
        }
    if source == "syslog" and (commands or ids):
        return {
            "status": "error",
            "error_kind": "invalid_argument",
            "message": "commands/ids scope raw command output; they are not "
                       "available for source=syslog",
            "source": source,
        }

    try:
        if source == "syslog":
            hours = 24
            if time_range:
                unit = time_range[-1]
                number = time_range[5:-1]
                if not time_range.startswith("last_") or unit not in ("h", "d") or not number.isdigit():
                    raise EvidenceError(
                        "invalid_argument",
                        "time_range must look like last_1h / last_24h / last_7d",
                    )
                hours = max(1, int(number) * (24 if unit == "d" else 1))
            with duckdb.connect(":memory:", read_only=False) as con:
                out = search_syslog(
                    con=con,
                    parquet_glob=str(Path.cwd() / _LOG_GLOB),
                    pattern=pattern,
                    device=device,
                    date_from=date_from,
                    date_to=date_to,
                    hours=hours,
                    page=page,
                    offset=offset,
                    limit=limit,
                    deadline_ms=deadline_ms,
                )
        else:
            try:
                con = duckdb.connect(str(MAIN_DB_PATH), read_only=True)
            except Exception as exc:  # noqa: BLE001 - normalized by kind
                raise classify_exception(exc) from None
            try:
                out = search(
                    con=con,
                    kind=source,
                    pattern=pattern,
                    device=device,
                    snapshot=snapshot,
                    commands=commands,
                    ids=ids,
                    date_from=date_from,
                    date_to=date_to,
                    page=page,
                    offset=offset,
                    limit=limit,
                    deadline_ms=deadline_ms,
                )
            finally:
                close = getattr(con, "close", None)
                if callable(close):
                    close()
    except EvidenceError as exc:
        return _error(source, exc)
    except Exception as exc:  # noqa: BLE001 - never leak driver text
        return _error(source, classify_exception(exc))

    rows = out["matches"]
    if source == "syslog":
        _shape_syslog_rows(rows)
    else:
        _shape_raw_rows(rows)
    rows, omitted, char_truncated_rows = _trim_to_char_budget(rows, _MAX_RESULT_CHARS)
    budget_info = dict(out.get("budget") or {})
    budget_info["rows_omitted_by_char_budget"] = omitted
    budget_info["rows_char_budget_truncated"] = char_truncated_rows
    budget_info["max_result_chars"] = _MAX_RESULT_CHARS
    returned = len(rows)
    excerpt_truncated_rows = sum(1 for row in rows if row.get("excerpt_truncated"))
    # A dropped row or a cut body is not a complete page: report more data and
    # give the caller the exact offset to resume at.
    more_rows = bool(out["has_more"] or omitted)
    next_offset = (out.get("offset", (out["page"] - 1) * out["page_size"]) + returned) if more_rows else None
    result = {
        "status": "success",
        "source": source,
        "source_state": out.get("source_state", "present"),
        "temporal": out["temporal"],
        "matches": rows,
        "returned": returned,
        "total": out["total"],
        "total_scope": "filtered_scope",
        "total_is_complete": True,
        "candidate_total": out["candidate_total"],
        "has_more": more_rows,
        # Truncation covers both axes: more rows exist, and a returned body is a
        # bounded excerpt or was cut by the char budget.  Never "success" with
        # truncated=false over text the caller cannot see in full.
        "truncated": more_rows or bool(excerpt_truncated_rows),
        "next_offset": next_offset,
        "page": out["page"],
        "page_size": out["page_size"],
        "offset": out.get("offset", (out["page"] - 1) * out["page_size"]),
        "path": out["path"],
        "body_scope": out.get("body_scope"),
        "excerpt_truncated_rows": excerpt_truncated_rows,
        "zero_reason": out.get("zero_reason"),
        # Propagate the helper's date-exclusion facts.  A count that was not
        # computed stays null (unknown), never zero.  date_scope states the
        # exact scope and refuses to invent the pre-date denominator.
        "date_unknown_candidates": out.get("date_unknown_candidates"),
        "date_exclusions": out.get("date_exclusions"),
        "date_scope": _date_scope(out, source=source, date_from=date_from, date_to=date_to),
        "filters": out.get("filters"),
        "collection_basis": out.get("collection_basis"),
        "collection_basis_status": out.get("collection_basis_status"),
        "identity_scope": out.get("identity_scope"),
        "identity_fallback": out.get("identity_fallback"),
        "budget": budget_info,
    }
    if not rows and out["total"]:
        # An offset past the end is a paging artifact, not an empty result set.
        result["hint"] = (
            f"This page is empty because offset={result['offset']} is at or beyond "
            f"total={out['total']}. The filtered result set is NOT empty; do not "
            "report missing evidence. Resume from a page/offset below total."
        )
        unknown = result["date_scope"].get("unknown_candidates")
        if unknown:
            result["hint"] += (
                f" Also, {unknown} candidate record(s) with unusable collection "
                "time were excluded before the date filter; whether their "
                "bodies match is unknown, not absent."
            )
    elif not rows:
        # Empty results trigger pattern-variation rambling on small
        # models — boundary test 2 saw 16 query_evidence calls trying
        # synonyms.  Steer the LLM away from that loop.
        result["hint"] = (
            "Empty result. DO NOT try synonyms or near-variants of "
            "this pattern (e.g. don't go 'BGP' → 'bgp' → 'b.g.p' → "
            "'border gateway' → 'denied' → 'filter' → ...).  Either "
            "the data genuinely doesn't contain it, or the right "
            "pattern is in a DIFFERENT source (try source=command_output "
            "or source=config), or this device wasn't probed for "
            "this command.  After 2 empty calls accept the null "
            "result and move on; cite 'no recorded evidence' to the "
            "user — that's a real answer, not a problem to keep "
            "drilling on."
        )
        if out.get("zero_reason") == "source_missing":
            result["hint"] = (
                "The syslog store is absent at the configured path — this is "
                "missing recorded data, not evidence that no event occurred. "
                "Do not retry pattern variants."
            )
        elif out.get("zero_reason") == "no_candidates":
            result["hint"] += (
                " zero_reason=no_candidates means the metadata filters "
                "(device/command/IDs/date) matched no records at all."
            )
        elif out.get("zero_reason") == "no_dated_candidates":
            scope = result["date_scope"]
            unknown = scope.get("unknown_candidates")
            if unknown is None:
                result["hint"] = (
                    "No candidate has a known collection date inside the requested "
                    "range, and the helper did not expose how many candidates were "
                    "excluded for unknown time. The time coverage is unknown (null, "
                    "not zero), so do not claim there is no recorded evidence; "
                    "widen the date range or report the time limitation."
                )
            elif unknown > 0:
                details = _exclusion_breakdown(scope.get("exclusions"))
                result["hint"] = (
                    f"No candidate has a known collection date inside the requested "
                    f"range. {unknown} candidate record(s) were excluded after the "
                    f"other metadata filters and before the date filter because "
                    f"their collection time is unusable ({details}). Whether they "
                    "fall on the requested date, and whether their bodies match, "
                    "cannot be determined — so do NOT report 'no recorded evidence'. "
                    "Report the unknown/invalid/conflicting collection time as the "
                    "reason the date question is unanswered."
                )
            else:
                result["hint"] = (
                    "No candidate has a known collection date inside the requested "
                    "range, and no unknown-time candidate was excluded. Other "
                    "candidate records exist after the metadata filters but their "
                    "known dates fall outside the requested range. Widen the date "
                    "range; do NOT report that no matching record exists."
                )
        elif (result["date_scope"].get("unknown_candidates") or 0) > 0:
            # Known-dated candidates had no body match, but the date predicate
            # also excluded candidates whose time cannot be attributed.  That is
            # not proof the pattern is absent.
            scope = result["date_scope"]
            unknown = scope["unknown_candidates"]
            details = _exclusion_breakdown(scope.get("exclusions"))
            result["hint"] = (
                "No body match was found among candidates with a known collection "
                f"date inside the requested range. {unknown} candidate record(s) "
                f"were excluded before the date filter for unusable collection "
                f"time ({details}); whether they match is unknown, so do NOT "
                "report 'no recorded evidence'. Report the known-date null result "
                "and the unknown-time exclusions separately."
            )
    # One versioned metadata block: page limit vs the server page cap, body
    # matches vs metadata candidates, and the date exclusions kept separate
    # from the known denominator. The helper's counts are unchanged.
    def attach_contract(extra_omitted: int) -> None:
        block = adapt_evidence(
            {"limit": limit},
            result,
            server_cap=MAX_PAGE_ROWS,
            default_limit=MAX_PAGE_ROWS,
            physical_sources=(
                ("netops.raw_output_store",)
                if source in ("command_output", "config")
                else None
            ),
        )
        if extra_omitted:
            # The per-call budget is measured in CHARACTERS (_MAX_RESULT_CHARS),
            # not UTF-8 bytes, so it must not be reported as a byte stop.
            block["truncation"]["rows"]["cause"] = "response_char_budget"
            block["truncation"]["chars"] = char_truncation(
                truncated=True, cause="response_char_budget",
                budget_chars=_MAX_RESULT_CHARS,
            )
        result["result_contract"] = block

    attach_contract(omitted)
    # _MAX_RESULT_CHARS bounds the whole serialized object in CHARACTERS, not
    # just row text: the contract/temporal/date_scope envelope also costs
    # characters. Trim trailing rows until it fits; counts, date_scope and the
    # contract itself are never dropped, and the budget is never raised.
    while result["matches"] and len(json.dumps(result, default=str)) > _MAX_RESULT_CHARS:
        result["matches"].pop()
        omitted += 1
        result["returned"] = len(result["matches"])
        result["has_more"] = True
        result["truncated"] = True
        result["next_offset"] = result["offset"] + result["returned"]
        result["excerpt_truncated_rows"] = sum(
            1 for row in result["matches"] if row.get("excerpt_truncated")
        )
        result["budget"]["rows_omitted_by_char_budget"] = omitted
        attach_contract(omitted)
    return result


from olav_skills.netops.core.query import warehouse as _candidate_warehouse

if __name__ == "__main__" and _candidate_warehouse.configured():
    _candidate_warehouse.candidate_main(query_evidence)


if __name__ == "__main__":
    import json as _json
    import sys as _sys

    # Every script in this pack promises "one JSON object on stdout". A bare
    # `query_evidence(**args)` broke that promise for the most likely mistake:
    # a missing or misspelled key raised TypeError and printed a traceback, which
    # a reader parsing JSON cannot act on. Naming the accepted keys in the error
    # is what turns a crash into a retry (ADR-0009: fix arg shape at the boundary,
    # not with a prose rule).
    _accepted = (
        "source", "pattern", "device", "time_range", "snapshot",
        "commands", "ids", "date_from", "date_to", "page", "offset", "limit",
        "deadline_ms", "raw_scope",
    )
    try:
        _args = _json.loads(_sys.stdin.read() or "{}")
    except ValueError as exc:
        print(_json.dumps({"status": "error", "error": f"bad JSON stdin: {exc}"}))
        _sys.exit(1)
    if not isinstance(_args, dict):
        print(_json.dumps({"status": "error",
                           "error": "stdin must be one JSON object",
                           "accepted_args": list(_accepted)}))
        _sys.exit(1)
    _missing = [k for k in ("source", "pattern") if not _args.get(k)]
    _unknown = [k for k in _args if k not in _accepted]
    if _missing or _unknown:
        print(_json.dumps({
            "status": "error",
            "error": (_missing and f"missing required: {_missing}" or "")
                     + ("; " if _missing and _unknown else "")
                     + (_unknown and f"not accepted: {_unknown}" or ""),
            "accepted_args": list(_accepted),
            "hint": 'source is one of syslog | command_output | config, '
                    'e.g. {"source": "command_output", "pattern": "BGP"}',
        }))
        _sys.exit(1)
    try:
        result = query_evidence(**_args)
    except Exception as exc:  # noqa: BLE001
        print(_json.dumps({"status": "error",
                           "error": f"{type(exc).__name__}: {exc}"}))
        _sys.exit(1)
    print(_json.dumps(result, default=str))
    if result.get("error", {}).get("code") == "INVALID_REQUEST" if isinstance(result.get("error"), dict) else False:
        _sys.exit(1)
