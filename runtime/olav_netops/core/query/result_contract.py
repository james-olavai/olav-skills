"""Domain adapter for the platform-generic result contract (OW055).

The generic block lives in ``olav.core.query_result`` (platform). This module
only supplies *network semantics*: what a returned row means for each typed
batch operation, which count scope a total describes, and which physical
source actually answered. It never re-implements the metadata shape and never
changes a count, a predicate, or business SQL.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from olav.core.query_result import (
    cell_truncation,
    char_truncation,
    clamp_cause,
    contract,
    count,
    limits,
    provenance,
    returned,
    row_truncation,
    byte_truncation,
    unknown,
)

#: operation -> (unit of one returned row, total scope)
BATCH_UNITS: dict[str, tuple[str | None, str]] = {
    "devices": ("devices", "inventory_filtered"),
    "multi_device_summary": ("devices", "snapshot_observed_inventory_with_record_counts"),
    "device_coverage": ("vendor_groups", "vendor_group_aggregate"),
    "commands": ("device_command_groups", "snapshot_observed_parsed_records"),
    "command_coverage": ("commands", "snapshot_observed_parsed_records"),
    "top_n": ("groups", "snapshot_observed_parsed_records"),
    "snapshot_summary": (None, "snapshot_counts"),
    "interface_health": ("canonical_records", "canonical_view_rows_for_snapshot"),
    "bgp_health": ("canonical_records", "canonical_view_rows_for_snapshot"),
}

#: snapshot_summary count key -> unit
SUMMARY_UNITS = {
    "inventory_device_count": "devices",
    "observed_device_count": "devices",
    "command_count": "commands",
    "record_count": "records",
}


def _sum_group_counts(rows: Sequence[dict], *, complete: bool) -> dict:
    """Sum ``device_count`` over returned aggregation rows, or report unknown."""
    total = 0
    for row in rows:
        value = row.get("device_count")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            complete = False
            break
        total += int(value)
    if not complete:
        return unknown(
            unit="devices",
            scope="sum_over_returned_vendor_groups",
            reason="aggregation page is partial; the grouped device total is not measured",
        )
    return count(
        value=total,
        unit="devices",
        scope="sum_over_returned_vendor_groups",
        exact=True,
    )


def cell_block(rows: Sequence[dict], *, cap: int) -> dict:
    """Cell clipping: capability is fixed, clipping itself is not measurable here.

    The typed query clips text in SQL before the fetch, so a cell that reached
    the cap *may* have been cut. ``at_cap`` counts those cells; ``truncated``
    stays unknown because a cell of exactly ``cap`` characters can be intact.
    """
    at_cap = 0
    for row in rows:
        for value in row.values():
            if isinstance(value, str) and len(value) >= cap:
                at_cap += 1
    return cell_truncation(capability=True, truncated=None, at_cap=at_cap, cap=cap)


#: Typed diff has a fixed per-direction sample cap independent of the generic
#: 1..50 request domain. A host may lower it further, never raise it.
DIFF_DIRECTION_CAP = 25
#: Typed diff declares its own page default; it is not the injected request.
DIFF_DEFAULT_LIMIT = 10


def diff_limits(
    *,
    requested_limit: int | None,
    host_cap: int,
    default_limit: int = DIFF_DEFAULT_LIMIT,
    direction_cap: int = DIFF_DIRECTION_CAP,
) -> tuple[dict, int]:
    """One canonical construction of typed-diff limit facts.

    The model request (or its absence) is kept apart from the fixed per-direction
    cap and the trusted host cap. ``server_caps.limit`` is the actual applied
    cap ``min(host, fixed)``; ``fixed`` and ``host`` remain visible separately so
    the fixed 25 can never be read back as a host or request value.

    Returns ``(limits_block, effective_sample_limit)``. The same block is used by
    the executor and the metadata adapter, so a branch cannot drift.
    """
    cap = min(int(host_cap), int(direction_cap))
    requested = {} if requested_limit is None else {"limit": int(requested_limit)}
    block = limits(
        requested=requested,
        defaults={"limit": int(default_limit)},
        server_caps={"limit": cap},
    )
    block["server_caps"]["fixed"] = int(direction_cap)
    block["server_caps"]["host"] = int(host_cap)
    return block, int(block["effective"]["limit"])


def adapt_diff(
    diff: dict,
    *,
    requested_limit: int | None = None,
    host_cap: int | None = None,
    default_limit: int = DIFF_DEFAULT_LIMIT,
    direction_cap: int = DIFF_DIRECTION_CAP,
    limit_block: dict | None = None,
    physical_sources: Iterable[str] = (),
    cell_cap: int | None = None,
) -> dict:
    """Two-direction identity-key diff metadata.

    ``added`` is target minus source, ``removed`` is source minus target; each
    direction keeps its full exact count next to the bounded sample, and both
    counts are identity keys — never devices, never rows per command.

    A caller that already built the canonical block through :func:`diff_limits`
    passes it in; otherwise this function builds that same block. The truncation
    cause is the real deciding layer (request/default/server cap), never a fixed
    ``sample_limit`` label that hides which limit actually cut the sample.
    """
    if limit_block is None:
        if host_cap is None:
            raise ValueError("host_cap is required when limit_block is not supplied")
        limit_block, _ = diff_limits(
            requested_limit=requested_limit,
            host_cap=host_cap,
            default_limit=default_limit,
            direction_cap=direction_cap,
        )
    cause = clamp_cause(limit_block)
    directions: dict[str, Any] = {}
    truncated = False
    for name in ("added", "removed"):
        items = diff.get(name) or []
        total = diff.get(f"{name}_count")
        has_more = bool(diff.get(f"{name}_has_more")) or (
            isinstance(total, int) and total > len(items)
        )
        truncated = truncated or has_more
        direction: dict[str, Any] = {
            "total": count(value=total, unit="identity_keys",
                           scope=f"{name}_identity_keys", exact=True),
            "returned": returned(value=len(items), unit="identity_keys"),
            "truncation": {
                "rows": row_truncation(
                    truncated=has_more,
                    cause=cause,
                    measured=True,
                    returned=len(items),
                    total=total,
                )
            },
        }
        if cell_cap is not None:
            direction["truncation"]["cells"] = cell_block(items, cap=cell_cap)
        directions[name] = direction
    return contract(
        level="diff",
        limits=limit_block,
        returned=returned(
            value=sum(len(diff.get(name) or []) for name in ("added", "removed")),
            unit="identity_keys",
        ),
        truncation={"rows": row_truncation(truncated=truncated,
                                           cause=cause, measured=True)},
        provenance=provenance(executed=True, physical_sources=physical_sources),
        counts={"directions": directions},
        semantics=(
            "added=target minus source, removed=source minus target; counts are "
            "identity keys in both directions, not devices and not content changes"
        ),
    )


def adapt_batch(
    args: dict,
    result: dict,
    *,
    max_rows: int,
    default_limit: int,
    cell_cap: int,
    byte_cap: int,
    physical_sources: Iterable[str] = (),
) -> dict:
    """Attach semantic scope to one successful typed batch operation."""
    operation = args.get("operation")
    if operation == "context":
        # Capability inventory, not an executed answer: no counts, no sources.
        return contract(
            level="schema",
            limits=limits(
                requested={},
                defaults={"limit": default_limit},
                server_caps={"limit": max_rows},
            ),
            provenance=provenance(executed=False),
            semantics="source/column capability only; not an executed result",
        )

    limit_block = limits(
        requested={"limit": args.get("limit")},
        defaults={"limit": default_limit},
        server_caps={"limit": max_rows},
    )
    rows = result.get("rows") or []
    unit, total_scope = BATCH_UNITS.get(operation, ("rows", "operation_result"))
    byte_cut = bool(result.get("byte_truncated"))
    row_cut = bool(result.get("has_more")) or byte_cut
    total_value = result.get("total_rows")
    total_block = (
        count(value=total_value, unit=unit or "rows", scope=total_scope, exact=True)
        if isinstance(total_value, int) and unit
        else unknown(
            unit=unit or "rows",
            scope=total_scope,
            reason="operation returns named counts instead of a row total",
        )
    )
    cause = "response_byte_budget" if byte_cut else clamp_cause(limit_block)
    truncation = {
        "rows": row_truncation(
            truncated=row_cut,
            cause=cause,
            measured=True,
            returned=len(rows) if unit else None,
            total=total_value if isinstance(total_value, int) and unit else None,
        ),
        "bytes": byte_truncation(truncated=byte_cut),
    }
    if unit:
        truncation["cells"] = cell_block(rows, cap=cell_cap)
    counts: dict[str, Any] = {}
    if operation == "device_coverage" and unit:
        counts["grouped_device_count"] = _sum_group_counts(
            rows, complete=not row_cut
        )
    elif operation == "snapshot_summary":
        for key, value in (result.get("counts") or {}).items():
            counts[key] = count(
                value=value,
                unit=SUMMARY_UNITS.get(key, "records"),
                scope="snapshot_scope_counts",
                exact=True,
            )
    block = contract(
        level="operation",
        limits=limit_block,
        returned=returned(value=len(rows), unit=unit) if unit else None,
        total=total_block if unit else None,
        truncation=truncation,
        provenance=provenance(executed=True, physical_sources=physical_sources),
        counts=counts or None,
        semantics=(
            "returned rows are " + (unit or "counts") + f"; total scope={total_scope}"
        ),
    )
    return block


def adapt_evidence(
    args: dict,
    result: dict,
    *,
    server_cap: int,
    default_limit: int,
    physical_sources: Iterable[str] = ("netops.raw_output_store",),
) -> dict:
    """Evidence page: candidate counts, date exclusions and matches stay apart."""
    limit_block = limits(
        requested={"limit": args.get("limit")},
        defaults={"limit": default_limit},
        server_caps={"limit": server_cap},
    )
    matches = result.get("matches") or []
    total = result.get("total")
    has_more = bool(result.get("has_more"))
    excerpt_rows = int(result.get("excerpt_truncated_rows") or 0)
    omitted_by_char_budget = int(
        (result.get("budget") or {}).get("rows_omitted_by_char_budget") or 0
    )
    counts: dict[str, Any] = {}
    candidate_total = result.get("candidate_total")
    if isinstance(candidate_total, int):
        counts["candidate_records"] = count(
            value=candidate_total,
            unit="records",
            scope="metadata_candidates_before_body_match",
            exact=True,
        )
    exclusions = result.get("date_exclusions") or {}
    if exclusions or result.get("date_unknown_candidates") is not None:
        counts["date_unknown_candidates"] = count(
            value=int(result.get("date_unknown_candidates") or 0),
            unit="records",
            scope="candidates_outside_known_date_scope",
            exact=True,
        )
        counts["date_excluded_candidates"] = {
            "count": int(sum(int(v or 0) for v in exclusions.values())),
            "unit": "records",
            "scope": "candidates_with_no_known_date_or_invalid_or_conflicting_time",
            "exact": True,
            "reasons": dict(exclusions),
            "note": "excluded candidates are not matches and are not added to a known denominator",
        }
    row_cause = (
        "response_char_budget"
        if omitted_by_char_budget
        else (clamp_cause(limit_block) if has_more else None)
    )
    truncation = {
        "rows": row_truncation(
            truncated=has_more,
            cause=row_cause,
            measured=True,
            returned=len(matches),
            total=total if isinstance(total, int) else None,
        ),
        "chars": char_truncation(
            truncated=bool(omitted_by_char_budget),
            cause="response_char_budget",
            budget_chars=(result.get("budget") or {}).get("max_result_chars"),
        ),
        "cells": cell_truncation(
            capability=True,
            truncated=bool(excerpt_rows),
            at_cap=None,
        ),
    }
    return contract(
        level="page",
        limits=limit_block,
        returned=returned(value=len(matches), unit="matches"),
        total=(
            count(value=total, unit="matches", scope="filtered_matches", exact=True)
            if isinstance(total, int)
            else unknown(
                unit="matches",
                scope="filtered_matches",
                reason="match count not reported by this evidence path",
            )
        ),
        truncation=truncation,
        provenance=provenance(
            executed=result.get("status") == "success",
            physical_sources=physical_sources,
            reason=(
                None
                if physical_sources is not None
                else "this evidence path does not determine a physical source; "
                     "the logical evidence kind is not a table name"
            ),
        ),
        counts=counts or None,
        semantics=(
            "body matches are counted separately from metadata candidates; "
            "unknown/invalid/conflicting capture times make a candidate "
            "undated, never a zero-match record"
        ),
    )
