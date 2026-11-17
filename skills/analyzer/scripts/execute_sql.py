#!/usr/bin/env python3
"""
Database Tool - Intelligent SQL query execution with auto schema exploration.

Core Features:
1. Auto schema discovery (no manual inspect_schema calls)
2. SQL generation with context
3. Error self-correction (via Agent ReAct loop)
4. DuckDB-specific optimizations
5. SchemaContext singleton caching (5 min TTL)
"""

import copy
import json
import sys
import time
from datetime import date, datetime
from datetime import time as time_type
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, validator


# Add src to Python Path
def _find_project_root():
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()



import duckdb as _duckdb

from olav_skills.core.config import MAIN_DB_PATH
from olav_skills.core.query_result import (
    byte_truncation,
    cell_truncation,
    clamp_cause,
    contract,
    count,
    limits,
    provenance,
    returned,
    row_truncation,
    unknown,
)
from olav_skills.tools.sql_budget import (
    HARD_MAX_CELL_CHARS,
    HARD_MAX_RESPONSE_BYTES,
    HARD_MAX_ROWS,
    HARD_MAX_SQL_BYTES,
    TRUNCATION_MARKER,
    SqlBudgetError,
    resolve_budget,
    run_bounded_query,
    utf8_json_size,
    validate_no_external_access,
)


def _classify_sql(sql: str) -> str:
    """Classify a SQL statement as SELECT, INSERT, MUTATE, DDL, or OTHER."""
    normalized = sql.strip().upper().lstrip("(")
    if normalized.startswith(("SELECT", "WITH")):
        return "SELECT"
    if normalized.startswith(("INSERT", "UPSERT", "COPY")):
        return "INSERT"
    if normalized.startswith(("UPDATE", "DELETE", "TRUNCATE")):
        return "MUTATE"
    if normalized.startswith(("CREATE", "DROP", "ALTER")):
        return "DDL"
    return "OTHER"


def db_query(sql: str, params: list | None = None) -> list[dict]:
    """Execute a SQL query against the main DuckDB database.

    SELECT/WITH queries run through the shared bounded reader: external file
    access is disabled, string cells are clipped in SQL, and the fetch stops
    at the hard row ceiling.  Schema discovery uses a high internal ceiling so
    its metadata is not silently reduced to the LLM preview budget.  Mutating
    SQL still requires explicit approval and never reaches the reader.
    """
    sql_type = _classify_sql(sql)

    if sql_type == "SELECT":
        budget = resolve_budget(
            default_rows=HARD_MAX_ROWS,
            default_cell_chars=HARD_MAX_CELL_CHARS,
        )
        return run_bounded_query(MAIN_DB_PATH, sql, params, budget).rows

    # Mutating SQL — require approval
    return [{"requires_approval": True, "sql_type": sql_type, "sql": sql,
             "reason": f"{sql_type} operation requires explicit approval before execution"}]




# ============================================================================
# Pydantic Models for Type-Safe Parameter Validation
# ============================================================================


class DatabaseQueryInput(BaseModel):
    """Database query input parameters - type-safe validation"""

    query: str = Field(default="", description="Natural language query")
    sql: str = Field(default="", description="Direct SQL query (optional)")
    params: list | None = Field(default=None, description="Optional bound SQL parameters")
    explain_only: bool = Field(
        default=False, description="Return only schema context without executing query"
    )
    # Optional reductions; every value is clamped to the server hard ceiling.
    max_rows: int | None = Field(default=None, description="Preview row cap (cannot exceed hard max)")
    max_cell_chars: int | None = Field(default=None, description="Per-cell character cap (cannot exceed hard max)")
    max_total_bytes: int | None = Field(default=None, description="Serialized preview byte cap (cannot exceed hard max)")
    timeout_ms: int | None = Field(default=None, description="Wall-clock query budget in ms (cannot exceed hard max)")
    offset: int | None = Field(default=None, description="Pagination offset for a bounded repeated query")
    export_bounded: bool = Field(default=False, description="Write the returned bounded preview to CSV")

    @validator("query", "sql", pre=True)
    def validate_not_none(cls, v):
        """Convert None to empty string"""
        if v is None:
            return ""
        return v


_CONTEXT_ROWS_FALLBACK = 20  # ARCH-16 fallback when tier config unavailable


def _resolve_context_rows() -> int:
    """Return how many result rows to surface to the LLM context (ARCH-16).

    Tier-aware cap via ``TIER_DEFAULTS[<tier>].execute_sql_context_rows``
    (small=10 / medium=20 / large=50). Falls back to
    ``_CONTEXT_ROWS_FALLBACK`` when config is unavailable so smoke tests
    without a configured tier still get a bounded result set.
    """
    try:
        from olav_skills.core.config import get_llm_config, tier_default
        tier = get_llm_config().model_tier
        val = tier_default(tier, "execute_sql_context_rows", _CONTEXT_ROWS_FALLBACK)
        return max(1, int(val))
    except Exception:  # noqa: BLE001
        return _CONTEXT_ROWS_FALLBACK


def _resolve_max_cell_chars() -> int:
    """Tier-aware per-cell character cap (small=800 / medium=2000 / large=8000)."""
    try:
        from olav_skills.core.config import get_llm_config, tier_default
        tier = get_llm_config().model_tier
        val = tier_default(tier, "execute_sql_max_cell_chars", 2000)
        return max(200, int(val))
    except Exception:  # noqa: BLE001
        return 2000


class DatabaseQueryOutput(BaseModel):
    """Database query output format - unified response"""

    data: list[dict] | None = Field(default=None, description="Query results")
    complex_columns: list[str] | None = Field(
        default=None, description="Complex columns present; over-budget values are bounded text"
    )
    table: str | None = Field(
        default=None, description="Markdown table representation (for small results)"
    )
    schema_context: str | None = Field(default=None, description="Database schema information")
    sql: str | None = Field(default=None, description="SQL query executed")
    count: int | None = Field(default=None, description="Complete result count, None when unknown")
    total: int | None = Field(default=None, description="Total rows, None when not fully observed")
    has_more: bool | None = Field(default=None, description="More rows exist beyond the returned preview")
    truncated: bool | None = Field(default=None, description="Preview was reduced by a budget")
    next_offset: int | None = Field(default=None, description="Offset for the next bounded page")
    fetched_rows: int | None = Field(default=None, description="Rows fetched from DuckDB")
    fetched_bytes: int | None = Field(default=None, description="Approximate serialized bytes fetched")
    returned_rows: int | None = Field(default=None, description="Rows returned in data")
    returned_bytes: int | None = Field(default=None, description="Approximate serialized bytes returned")
    budget_exceeded: bool | None = Field(default=None, description="Byte budget stopped the preview")
    cell_truncated: bool | None = Field(default=None, description="One or more cells were clipped in SQL")
    budget: dict | None = Field(default=None, description="Resolved limits and hard ceilings")
    timing: dict | None = Field(default=None, description="prepare/execute/fetch/serialize timings")
    status: str = Field(
        ...,
        description="success | empty | error | needs_sql_generation | requires_approval",
    )
    error: str | None = Field(default=None, description="Error message if status=error")
    error_type: str | None = Field(default=None, description="Type of error")
    message: str | None = Field(default=None, description="Additional message")
    user_query: str | None = Field(default=None, description="Original user query")
    tables: list[str] | None = Field(default=None, description="Available tables")
    attempted_sql: str | None = Field(default=None, description="SQL that failed")
    suggestions: list[str] | None = Field(default=None, description="Suggestions for retry when count=0")


class SchemaContext:
    """Context manager for auto-schema exploration with singleton caching.

    Uses singleton pattern to cache schema across multiple execute_sql calls.
    Cache TTL is 5 minutes by default.
    """

    _instance: "SchemaContext | None" = None
    _last_refresh: float = 0
    _cache_ttl: float = 300.0  # 5 minutes TTL

    def __new__(cls) -> "SchemaContext":
        """Singleton pattern - reuse instance if cache is valid."""
        now = time.time()
        if cls._instance is None or (now - cls._last_refresh) > cls._cache_ttl:
            cls._instance = super().__new__(cls)
            cls._instance._schema_cache: dict[str, Any] = {}
            cls._instance._refresh_schema()
            cls._last_refresh = now
        return cls._instance

    def __init__(self):
        """Initialize with automatic schema caching."""
        # Schema already initialized in __new__
        pass

    def _refresh_schema(self) -> None:
        """Refresh schema cache by querying INFORMATION_SCHEMA."""
        try:
            # Get all tables
            tables_result = db_query(
                """
                SELECT table_schema || '.' || table_name AS table_name, table_type
                FROM information_schema.tables
                WHERE table_schema IN ('main', 'netops', 'presales')
                ORDER BY table_schema, table_name
                """
            )

            self._schema_cache["tables"] = [row["table_name"] for row in tables_result]
            self._schema_cache["table_details"] = {}

            # Get columns for each table
            for table_name in self._schema_cache["tables"]:
                try:
                    columns_result = db_query(f"DESCRIBE {table_name}")
                    self._schema_cache["table_details"][table_name] = {
                        "columns": [
                            {
                                "name": row.get("column_name", row.get("Field", "")),
                                "type": row.get("column_type", row.get("Type", "")),
                            }
                            for row in columns_result
                        ]
                    }
                except Exception:
                    # Skip tables with errors
                    pass

            # Get sample data — prefer views and devices; skip noisy catalog/JSON tables
            SKIP_SAMPLES = {"main.schema_catalog", "main.yang_leaves",
                            "netops.oc_outputs", "netops.parsed_outputs"}
            PREFER_SAMPLES = [
                "main.v_bgp_neighbors_auto", "main.v_interfaces_auto",
                "main.v_ospf_neighbors_auto", "main.v_l2_links_auto",
                "netops.topology_links", "netops.devices",
            ]
            self._schema_cache["samples"] = {}
            # First try preferred tables, then fill from remaining (skip noisy ones)
            candidates = PREFER_SAMPLES + [
                t for t in self._schema_cache["tables"]
                if t not in PREFER_SAMPLES and t not in SKIP_SAMPLES
            ]
            for table_name in candidates[:6]:
                try:
                    sample = db_query(f"SELECT * FROM {table_name} LIMIT 2")
                    if sample:
                        self._schema_cache["samples"][table_name] = sample
                except Exception:
                    pass

            # Query schema_catalog for parsed_outputs JSON field info
            # Enables LLM to generate: SELECT parsed_data->>'field' FROM parsed_outputs
            try:
                catalog_rows = db_query(
                    "SELECT source_name, platform, fields FROM schema_catalog "
                    "WHERE source_type = 'textfsm' ORDER BY source_name, platform"
                )
                if catalog_rows:
                    schema_catalog_info: list[str] = []
                    for row in catalog_rows:
                        fields = row.get("fields", [])
                        if isinstance(fields, str):
                            try:
                                fields = json.loads(fields)
                            except Exception:
                                fields = []
                        field_names = [f["name"] for f in (fields or []) if isinstance(f, dict)]
                        if field_names:
                            schema_catalog_info.append(
                                f"  {row['source_name']} ({row['platform']}): "
                                + ", ".join(field_names)
                            )
                    self._schema_cache["schema_catalog"] = schema_catalog_info
            except Exception:
                pass  # schema_catalog not yet populated

            # Query mapping_table for unified field mapping (v0.11.0)
            try:
                mapping_rows = db_query(
                    "SELECT platform, platform_key, unified_key FROM mapping_table "
                    "ORDER BY platform, unified_key"
                )
                if mapping_rows:
                    mapping_info: list[str] = []
                    for row in mapping_rows:
                        mapping_info.append(
                            f"  {row['platform']}: {row['platform_key']} -> {row['unified_key']}"
                        )
                    self._schema_cache["mapping_table"] = mapping_info
            except Exception:
                pass  # mapping_table not yet populated

        except Exception as e:
            self._schema_cache = {"error": str(e)}

    def get_schema_context(self) -> str:
        """Get formatted schema context for LLM."""
        if "error" in self._schema_cache:
            return f"Schema error: {self._schema_cache['error']}"

        context_parts = ["**Available Database Schema:**\n"]

        # CRITICAL: schema-qualified names required for netops tables
        context_parts.append(
            "⚠️  ALWAYS use schema-qualified names: `netops.devices`, `netops.parsed_outputs`, etc.\n"
            "   Views (v_*) are in main schema and can be queried unqualified.\n"
        )

        # List tables
        context_parts.append(f"Tables: {', '.join(self._schema_cache['tables'])}\n")

        # View semantic hints — help agent pick the right view immediately
        context_parts.append("**View Quick Reference (USE THESE FIRST for network queries):**")
        context_parts.append(
            "  v_interfaces_auto         → interface admin/oper status + IP address per device\n"
            "  v_bgp_neighbors_auto      → BGP neighbor state, remote-AS, prefixes received\n"
            "  v_ospf_neighbors_auto     → OSPF neighbor state + cost (cross-vendor)\n"
            "  v_l2_links_auto           → L2 neighbors (CDP/LLDP): cleaned topology view\n"
            "  netops.topology_links     → CDP/LLDP base table: src/dst device + interface pairs\n"
            "  v_arp_auto                → ARP table: ip_address, mac_address, interface per device\n"
            "  netops.devices            → device inventory: hostname, platform, ip_address, role\n"
            "  netops.parsed_outputs     → raw TextFSM rows: parsed_data (JSON), command, snapshot_id\n"
            "  netops.topology_links     → computed topology links (src/dst device+interface+protocol)\n"
            "  netops.oc_outputs         → OC JSON per module: oc_module, oc_data, device_name\n"
            "  netops.raw_output_store   → latest raw CLI text per (device, command)"
        )

        # Detail each table
        for table_name, details in self._schema_cache["table_details"].items():
            context_parts.append(f"\n**{table_name}:**")
            columns = [f"  - {col['name']}: {col['type']}" for col in details["columns"]]
            context_parts.append("\n".join(columns))

            # Add sample if available
            if table_name in self._schema_cache.get("samples", {}):
                sample = self._schema_cache["samples"][table_name]
                if sample:
                    context_parts.append(f"  Sample: {sample[0]}")

        # Add mapping_table block (Unified Schema Mapping v0.11.0)
        mapping_info = self._schema_cache.get("mapping_table", [])
        if mapping_info:
            context_parts.append("\n**Unified Schema Mappings (platform -> unified):**")
            context_parts.append(
                "  Use these to map platform-specific keys to unified keys in your queries."
            )
            # Cap at 50 most common mappings to avoid context bloat
            context_parts.extend(mapping_info[:50])

        # Add schema_catalog block (JSON fields for parsed_outputs)
        schema_catalog = self._schema_cache.get("schema_catalog", [])
        if schema_catalog:
            context_parts.append("\n**parsed_outputs JSON fields (via schema_catalog):**")
            context_parts.append(
                "  Query pattern: SELECT parsed_data->>'field_name' "
                "FROM parsed_outputs WHERE command='...' AND snapshot_date=CURRENT_DATE"
            )
            context_parts.extend(schema_catalog[:50])  # cap at 50 entries

        return "\n".join(context_parts)

    def query(self, sql: str, budget=None, params: list | None = None):
        """Execute SQL with the shared bounded reader (not unbounded db_query)."""
        if budget is None:
            budget = resolve_budget(
                default_rows=HARD_MAX_ROWS,
                default_cell_chars=HARD_MAX_CELL_CHARS,
            )
        return run_bounded_query(MAIN_DB_PATH, sql, params, budget)


_MAX_FIELD_CHARS = 800  # prevent single large JSON fields from flooding LLM context


def _sanitize_value(val: Any, max_chars: int = _MAX_FIELD_CHARS) -> Any:
    """Convert non-JSON-serializable types to strings, truncating large values."""
    if isinstance(val, (datetime, date, time_type)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    if isinstance(val, dict):
        sanitized = {k: _sanitize_value(v, max_chars) for k, v in val.items()}
        as_str = str(sanitized)
        if len(as_str) > max_chars:
            return as_str[:max_chars] + TRUNCATION_MARKER
        return sanitized
    if isinstance(val, (list, tuple)):
        sanitized = [_sanitize_value(v, max_chars) for v in val]
        as_str = str(sanitized)
        if len(as_str) > max_chars:
            return as_str[:max_chars] + TRUNCATION_MARKER
        return sanitized
    if isinstance(val, str) and len(val) > max_chars:
        return val[:max_chars] + TRUNCATION_MARKER
    return val


def _sanitize_rows(rows: list[dict], max_chars: int = _MAX_FIELD_CHARS) -> list[dict]:
    """Ensure all values in query results are JSON-serializable.

    Sanitizes each FIELD, never the row as a whole. ``_sanitize_value``'s dict
    branch returns a *string* once the rendered form passes
    ``_MAX_FIELD_CHARS``, so a row wide enough to trip that (any query
    selecting stored config text) came back as a str and every consumer
    expecting a mapping died on `'str' object has no attribute 'items'`.
    The budget is per-field; a large cell must not turn a record into text.
    """
    return [
        {k: _sanitize_value(v, max_chars) for k, v in row.items()}
        if isinstance(row, dict) else row
        for row in rows
    ]


def _schema_hint() -> str:
    """A follow-up hint listing views that are actually in this database.

    This was a hardcoded string naming ``v_interfaces_auto``,
    ``v_bgp_neighbors_auto``, ``v_ospf_neighbors_auto`` and ``v_arp_auto``. R83.2
    deleted all four with the recipe-driven view layer; only ``v_l2_links_auto``
    survived. So every successful query answered with an invitation to query four
    views that do not exist, and a model that took it got a CatalogException —
    the same wrong view names that `query_topology.py` carries two fix notes
    about. A hint about the schema has to be read off the schema.

    Failure is silent by design: this is an aid attached to an answer that
    already succeeded, so it must never turn that answer into an error.
    """
    try:
        with _duckdb.connect(str(MAIN_DB_PATH), read_only=True) as conn:
            views = [
                r[0] for r in conn.execute(
                    "SELECT table_name FROM information_schema.views "
                    "WHERE table_schema = 'netops' ORDER BY table_name"
                ).fetchall()
            ]
            tables = [
                r[0] for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'netops' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                ).fetchall()
            ]
    except Exception:  # noqa: BLE001
        return ""

    parts = []
    if tables:
        parts.append("tables: " + ", ".join(f"netops.{t}" for t in tables[:8]))
    if views:
        shown = ", ".join(f"netops.{v}" for v in views[:8])
        more = f" (+{len(views) - 8} more)" if len(views) > 8 else ""
        parts.append(f"views: {shown}{more}")
    if not parts:
        return ""
    return "Schema reminder — " + " | ".join(parts)


_SCHEMA_CONTEXT_MAX_BYTES = 65_536
_MIN_RESPONSE_OVERHEAD = 1_024


def _bounded_schema_context(schema_context: str, max_total_bytes: int) -> str:
    """Hard-bound the schema field without splitting a UTF-8 code point."""
    limit = min(_SCHEMA_CONTEXT_MAX_BYTES, max(0, max_total_bytes - _MIN_RESPONSE_OVERHEAD))
    encoded = schema_context.encode("utf-8")
    if len(encoded) <= limit:
        return schema_context
    if limit <= 0:
        return ""
    marker = "\n…[schema context truncated by response budget]"
    marker_bytes = marker.encode("utf-8")
    budget = max(0, limit - len(marker_bytes))
    head = encoded[:budget].decode("utf-8", errors="ignore")
    return head + marker if budget else ""


def _response_cap(budget) -> int:
    return min(HARD_MAX_RESPONSE_BYTES, budget.max_total_bytes)


def _finalize_response(payload: dict, budget=None) -> dict:
    """Single final exit for every status branch.

    The actual serialized UTF-8 envelope is measured here, including SQL/query
    echo, schema context, data, messages and errors.  A caller's
    ``max_total_bytes`` can only lower the fixed hard cap.  If a success-like
    payload does not fit, it becomes a typed ``budget_exceeded`` failure; if an
    error payload is too large it is compacted.  When even a valid error cannot
    fit the caller's tiny request cap (for example 10 bytes), a compact typed
    error is returned under the fixed hard cap instead of an over-budget
    success.
    """
    cap = _response_cap(budget) if budget is not None else HARD_MAX_RESPONSE_BYTES
    size = utf8_json_size(payload, indent=2)
    if size <= cap:
        return payload
    if payload.get("status") != "error":
        payload = {
            "status": "error",
            "error_type": "budget_exceeded",
            "error": (
                f"complete response would be {size} UTF-8 bytes; the response "
                f"cap is {cap} bytes. Lower max_cell_chars/max_rows, shorten "
                "the SQL/query, or project fewer columns."
            ),
        }
    return _compact_error_payload(payload, cap)


def _compact_error_payload(payload: dict, cap: int) -> dict:
    """Keep the error identity while dropping advisory/large envelope fields.

    If the typed error is too large for the caller's request cap, the advisory
    message is shortened first so a legal compact error can still fit.  A cap
    too small for any legal JSON error (for example 10 bytes) uses the explicit
    compact-failure protocol: return a small typed error under the fixed hard
    cap instead of pretending success.
    """
    error_type = str(payload.get("error_type") or "budget_exceeded")
    error = str(payload.get("error") or payload.get("message") or "query failed")
    candidate = {
        "status": "error",
        "error_type": error_type,
        "error": error[:4_000],
    }
    if utf8_json_size(candidate, indent=2) <= cap:
        return candidate
    # Preserve status/error_type and shorten only the human-readable message.
    trimmed = candidate["error"]
    while trimmed and utf8_json_size(candidate, indent=2) > cap:
        over = utf8_json_size(candidate, indent=2) - cap
        trimmed = trimmed[: -max(16, over)]
        candidate["error"] = trimmed + ("…" if trimmed else "")
    if utf8_json_size(candidate, indent=2) <= cap:
        return candidate
    # The requested cap cannot hold even a minimal typed JSON error.
    candidate["error"] = error[:1_000]
    while (
        utf8_json_size(candidate, indent=2) > HARD_MAX_RESPONSE_BYTES
        and candidate["error"]
    ):
        candidate["error"] = candidate["error"][: max(0, len(candidate["error"]) // 2)]
    return candidate


def _error_payload(error: str, error_type: str, budget=None, *, schema_context=None,
                   attempted_sql=None) -> dict:
    """Build a compact error whose serialized envelope stays hard-bounded."""
    cap = min(HARD_MAX_RESPONSE_BYTES, budget.max_total_bytes) if budget else HARD_MAX_RESPONSE_BYTES
    output = DatabaseQueryOutput(
        error=(error or "query failed")[:4_000],
        error_type=error_type,
        status="error",
        budget=budget.as_dict() if budget else None,
        schema_context=schema_context,
        attempted_sql=attempted_sql,
    )
    payload = output.model_dump(exclude_none=True)
    if utf8_json_size(payload, indent=2) <= cap:
        return payload
    # Keep the SQL error identity; drop the large advisory fields first.
    payload.pop("schema_context", None)
    if utf8_json_size(payload, indent=2) <= cap:
        payload.pop("attempted_sql", None)
        if utf8_json_size(payload, indent=2) <= max(cap, 2_048):
            return payload
    payload.pop("attempted_sql", None)
    payload["error"] = payload["error"][:1_000]
    if utf8_json_size(payload, indent=2) > cap:
        payload.pop("budget", None)
    return payload


def _statement_contract(args, default_rows, default_cell, result) -> dict:
    """One statement-level metadata block for a successful query result.

    ``total`` is scoped to *this statement's own result*: reading a LIMITed
    result does not establish the size of the underlying data, and no extra
    COUNT is ever executed for metadata.  ``counts.full_total`` therefore stays
    unknown whether or not the statement result was fully observed.
    """
    limit_block = limits(
        requested={"rows": args.max_rows, "cell_chars": args.max_cell_chars},
        defaults={"rows": default_rows, "cell_chars": default_cell},
        server_caps={"rows": default_rows, "cell_chars": default_cell},
    )
    offset = int((result.budget or {}).get("offset") or 0)
    complete = (
        not result.has_more
        and not result.budget_exceeded
        and result.returned_rows == result.fetched_rows
        and offset == 0
    )
    total = (
        count(value=result.returned_rows, unit="rows", scope="statement_result", exact=True)
        if complete
        else unknown(
            unit="rows",
            scope="statement_result",
            reason="statement result was not fully fetched (page, row or byte budget)",
        )
    )
    cause = "response_byte_budget" if result.budget_exceeded else clamp_cause(limit_block, "rows")
    return contract(
        level="statement",
        limits=limit_block,
        returned=returned(value=result.returned_rows, unit="rows"),
        total=total,
        truncation={
            "rows": row_truncation(
                truncated=bool(result.has_more or result.budget_exceeded),
                cause=cause,
                measured=True,
                returned=result.returned_rows,
                total=total.get("count"),
            ),
            "bytes": byte_truncation(truncated=bool(result.budget_exceeded)),
            "cells": cell_truncation(
                capability=True,
                truncated=bool(result.cell_truncated),
                cap=int((result.budget or {}).get("max_cell_chars") or 0) or None,
            ),
        },
        provenance=provenance(
            executed=True,
            physical_sources=None,
            reason="the shared statement reader does not parse SQL for physical sources",
        ),
        counts={
            "full_total": unknown(
                unit="rows",
                scope="underlying_statement_sources",
                reason="no full COUNT was executed and a business LIMIT may cap the statement result",
            )
        },
        semantics=(
            "total is scoped to this statement's result; the underlying full "
            "total stays unknown and reader caps are named in truncation.rows.cause"
        ),
    )


def _with_contract(payload: dict, block, budget) -> dict:
    """Attach additive metadata without ever silently losing it.

    The block counts toward the caller's byte ceiling, but dropping it (or a
    legacy field) to fit is not an option: a response that lies about what it
    measured is worse than a budget error. Instead, rows the statement already
    fetched are trimmed measurably and the block is rewritten to match, so
    ``returned``/``truncation``/``total`` stay true. If even the minimum
    metadata does not fit, the caller gets an explicit budget error.
    """
    if block is None:
        return payload
    cap = _response_cap(budget) if budget is not None else HARD_MAX_RESPONSE_BYTES
    candidate = dict(payload)
    candidate["result_contract"] = block
    if utf8_json_size(candidate, indent=2) <= cap:
        return candidate

    rows = candidate.get("data")
    original_returned = (block.get("returned") or {}).get("count")
    if isinstance(rows, list) and rows:
        trimmed = copy.deepcopy(block)
        while rows and utf8_json_size(candidate, indent=2) > cap:
            rows.pop()
        if len(rows) != original_returned:
            trimmed["returned"] = returned(value=len(rows), unit="rows")
            row_meta = trimmed.setdefault("truncation", {}).setdefault("rows", {})
            row_meta.update({
                "truncated": True,
                "cause": "response_byte_budget",
                "measured": True,
                "returned": len(rows),
            })
            trimmed["truncation"]["bytes"] = byte_truncation(
                truncated=True, cause="response_byte_budget"
            )
            if isinstance(original_returned, int) and len(rows) < original_returned:
                # This page no longer carries the whole statement result, so a
                # previously exact statement-result total is not this
                # response's total anymore.
                trimmed["total"] = unknown(
                    unit="rows",
                    scope="statement_result",
                    reason="the response byte ceiling dropped fetched rows; "
                           "the statement result is larger than this page",
                )
            candidate["result_contract"] = trimmed
            candidate["returned_rows"] = len(rows)
            candidate["has_more"] = True
            candidate["truncated"] = True
        if utf8_json_size(candidate, indent=2) <= cap:
            return candidate

    raise SqlBudgetError(
        "budget_exceeded",
        f"max_total_bytes={budget.max_total_bytes if budget else HARD_MAX_RESPONSE_BYTES} "
        "cannot fit the result, its legacy fields and the required metadata "
        "block. Raise the byte budget, project fewer columns or page with offset; "
        "metadata is never silently omitted.",
    )


def _candidate_main(params: dict) -> dict:
    """Administrator candidate read transport; legacy preview, no stable pages."""
    from dataclasses import replace
    from olav_skills.core.read_connection import open_read_connection, ReadConnectionError
    from olav_skills.tools.sql_policy import (load_query_policy, validate_bindings,
                                       ValidatedSelect, SqlPolicyError)
    try:
        allowed = {"query", "sql", "params", "explain_only", "max_rows", "max_cell_chars",
                   "max_total_bytes", "timeout_ms", "offset"}
        if not isinstance(params, dict) or set(params) - allowed:
            raise SqlPolicyError("FORBIDDEN_FIELD")
        if len(json.dumps(params, ensure_ascii=False).encode("utf-8")) > 65536:
            raise SqlPolicyError("INPUT_BUDGET_EXCEEDED")
        policy = load_query_policy()
        args = DatabaseQueryInput(**params)
        budget = replace(resolve_budget(default_rows=20, default_cell_chars=2000,
            overrides={key: getattr(args, key) for key in
                       ("max_rows", "max_cell_chars", "max_total_bytes", "timeout_ms")},
            offset=args.offset or 0), memory_limit="256MB", threads=1)
        if not args.sql or args.explain_only:
            with open_read_connection() as session:
                validate_bindings(session.connection, ValidatedSelect(policy.relations, frozenset()), policy)
                # No samples, native singleton, model, or untrusted DESCRIBE.
                tables = sorted(f"{schema}.{table}" for schema, table in policy.relations)
                payload = {"status": "success" if args.explain_only else "needs_sql_generation",
                           "tables": tables, "schema_context": "Approved base tables: " + ", ".join(tables),
                           "reader": {"backend": "ducklake", "read_version": session.read_version,
                                      "policy_version": 1, "transport": "bounded_preview"}}
                return _finalize_response(payload, budget)
        result = run_bounded_query(None, args.sql, args.params, budget,
                                  connection_factory=open_read_connection, query_policy=policy)
        if result.budget_exceeded:
            raise SqlBudgetError("budget_exceeded", "query byte budget exceeded")
        payload = result.as_dict()
        payload["data"] = _sanitize_rows(result.rows, max_chars=budget.max_cell_chars + len(TRUNCATION_MARKER) + 32)
        payload["status"] = "success" if result.rows else "empty"
        payload["reader"] = {"backend": "ducklake", "read_version": result.read_version,
                             "policy_version": 1, "transport": "bounded_preview"}
        payload["message"] = "Bounded preview; offset re-executes SQL and is not a stable result page."
        return _finalize_response(payload, budget)
    except (SqlPolicyError, ReadConnectionError) as exc:
        code = exc.code
    except SqlBudgetError as exc:
        code = exc.error_type
    except Exception:
        code = "INVALID_REQUEST"
    return {"status": "error", "error_type": code, "error": code}


def main(params: dict) -> dict:
    """Execute database query with auto schema exploration.

    Use tool_help("execute_sql") for full usage, examples, and tier limits.

    Args:
        params: {
            "query": "Natural language query",
            "sql": "Optional direct SQL",
            "explain_only": Optional bool to only return schema context
        }

    Returns:
        {
            "data": [...],
            "sql": "Executed SQL",
            "schema_context": "Auto-discovered schema",
            "status": "success"|"error"
        }
    """
    # Candidate routing precedes all native SchemaContext/approval paths.
    from olav_skills.core.read_connection import read_host_config, ReadConnectionError
    try:
        configured = read_host_config() is not None
    except ReadConnectionError:
        return {"status": "error", "error_type": "WAREHOUSE_UNAVAILABLE", "error": "WAREHOUSE_UNAVAILABLE"}
    if configured:
        return _candidate_main(params)

    # Validate parameters with Pydantic
    try:
        args = DatabaseQueryInput(**params)
    except Exception as e:
        output = DatabaseQueryOutput(
            status="error", error=f"Invalid parameters: {str(e)}", error_type="validation_error"
        )
        return _finalize_response(output.model_dump(exclude_none=True))

    user_query = args.query
    direct_sql = args.sql
    explain_only = args.explain_only

    # ARCH-16: the host tier's context rows/cell cap are server caps. A request
    # may lower them, never raise them. The same resolved budget is used by
    # every branch so query/explain/approval/error responses obey the request
    # byte ceiling too.
    MAX_ROWS_TO_CONTEXT = _resolve_context_rows()
    MAX_CELL_CHARS_TO_CONTEXT = _resolve_max_cell_chars()
    budget = resolve_budget(
        default_rows=MAX_ROWS_TO_CONTEXT,
        default_cell_chars=MAX_CELL_CHARS_TO_CONTEXT,
        row_cap=MAX_ROWS_TO_CONTEXT,
        cell_cap=MAX_CELL_CHARS_TO_CONTEXT,
        overrides={
            "max_rows": args.max_rows,
            "max_cell_chars": args.max_cell_chars,
            "max_total_bytes": args.max_total_bytes,
            "timeout_ms": args.timeout_ms,
        },
        offset=args.offset or 0,
    )

    # Reject oversized inputs before schema discovery, description, or any echo.
    # The errors deliberately do not include the oversized text.
    if direct_sql:
        sql_bytes = len(direct_sql.encode("utf-8"))
        if sql_bytes > HARD_MAX_SQL_BYTES:
            return _finalize_response(
                _error_payload(
                    f"SQL is {sql_bytes} UTF-8 bytes; the server hard maximum is "
                    f"{HARD_MAX_SQL_BYTES}. Shorten the SQL (comments included).",
                    "budget_exceeded",
                ),
                budget,
            )
    if user_query:
        query_bytes = len(user_query.encode("utf-8"))
        if query_bytes > HARD_MAX_SQL_BYTES:
            return _finalize_response(
                _error_payload(
                    f"query is {query_bytes} UTF-8 bytes; the server hard maximum "
                    f"is {HARD_MAX_SQL_BYTES}. Shorten the natural-language query.",
                    "budget_exceeded",
                ),
                budget,
            )

    # Get schema context (singleton, cached for 5 min)
    context = SchemaContext()
    schema_context = context.get_schema_context()

    # If explain_only, return just schema
    if explain_only:
        output = DatabaseQueryOutput(
            schema_context=_bounded_schema_context(schema_context, budget.max_total_bytes),
            tables=context._schema_cache.get("tables", []),
            status="success",
        )
        schema_block = contract(
            level="schema",
            limits=limits(
                requested={"rows": args.max_rows, "cell_chars": args.max_cell_chars},
                defaults={"rows": MAX_ROWS_TO_CONTEXT, "cell_chars": MAX_CELL_CHARS_TO_CONTEXT},
                server_caps={"rows": MAX_ROWS_TO_CONTEXT, "cell_chars": MAX_CELL_CHARS_TO_CONTEXT},
            ),
            provenance=provenance(executed=False),
            semantics="schema discovery only; not an executed result and not a physical source",
        )
        try:
            payload = _with_contract(
                output.model_dump(exclude_none=True), schema_block, budget
            )
        except SqlBudgetError as e:
            return _finalize_response(
                _error_payload(str(e), e.error_type, budget), budget
            )
        return _finalize_response(payload, budget)

    # If direct SQL provided (agent already generated it), execute it
    if direct_sql:
        sql_type = _classify_sql(direct_sql)
        if sql_type != "SELECT":
            try:
                validate_no_external_access(direct_sql)
            except SqlBudgetError as e:
                output = DatabaseQueryOutput(
                    error=str(e),
                    error_type=e.error_type,
                    attempted_sql=direct_sql,
                    status="error",
                )
                return _finalize_response(output.model_dump(exclude_none=True), budget)
            output = DatabaseQueryOutput(
                sql=direct_sql,
                count=0,
                status="requires_approval",
                message=(
                    f"{sql_type} statement was NOT executed — it needs explicit "
                    "approval. The database is read-only on this path."
                ),
            )
            not_run_block = contract(
                level="statement",
                provenance=provenance(executed=False),
                semantics="statement was not executed; it owns no counts and no sources",
            )
            try:
                payload = _with_contract(
                    output.model_dump(exclude_none=True), not_run_block, budget
                )
            except SqlBudgetError as e:
                return _finalize_response(
                    _error_payload(str(e), e.error_type, budget), budget
                )
            return _finalize_response(payload, budget)

        try:
            response_cap = _response_cap(budget)
            if len(direct_sql.encode("utf-8")) + _MIN_RESPONSE_OVERHEAD > response_cap:
                raise SqlBudgetError(
                    "budget_exceeded",
                    f"max_total_bytes={budget.max_total_bytes} cannot fit the "
                    "SQL echo plus the minimum response metadata. Raise the byte "
                    "budget or shorten the SQL.",
                )
            result = context.query(direct_sql, budget=budget, params=args.params)
            if result.budget_exceeded:
                raise SqlBudgetError(
                    "budget_exceeded",
                    "the preview byte budget stopped the fetch; reduce "
                    "max_cell_chars/max_rows or project fewer columns. Do not "
                    "retry the same offset without a smaller projection.",
                    partial_rows=result.rows,
                    fetched_rows=result.fetched_rows,
                    fetched_bytes=result.fetched_bytes,
                )
            bounded_schema_context = _bounded_schema_context(
                schema_context, budget.max_total_bytes
            )
            # SQL already clipped string/complex cells; sanitize types without
            # re-introducing a smaller, conflicting text cap.
            results = _sanitize_rows(
                result.rows,
                max_chars=budget.max_cell_chars + len(TRUNCATION_MARKER) + 32,
            )

            # Bounded export is explicit: the old unconditional full-query CSV
            # cannot coexist with a bounded preview without re-fetching all rows.
            csv_path = None
            if args.export_bounded and results and result.truncated:
                import csv
                import hashlib
                import io
                from pathlib import Path

                export_dir = Path("exports") / "queries"
                export_dir.mkdir(parents=True, exist_ok=True)

                # Content-addressed: the filename carries a digest of the rows,
                # so an identical result set reuses the file it already wrote
                # instead of leaving a duplicate behind. A model that re-issues
                # the same query does happen — observed 1 run in 3 on core,
                # producing two byte-identical CSVs 5s apart, of which only the
                # second was ever cited. Prose cannot fix that (it changes
                # WHETHER a small model calls a tool, not how many times), so
                # the tool makes the duplicate impossible by construction.
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)
                payload = buf.getvalue()
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]

                existing = sorted(export_dir.glob(f"query_*_{digest}.csv"))
                if existing:
                    csv_path = existing[0]
                else:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    csv_path = export_dir / f"query_{timestamp}_{digest}.csv"
                    with open(csv_path, "w", newline="") as f:
                        f.write(payload)

            message = None
            if csv_path:
                message = (
                    f"BOUNDED results ({result.returned_rows} rows; total unknown) "
                    f"exported to {csv_path}."
                )
            if result.has_more:
                msg = (
                    f"Only first {result.returned_rows} rows returned "
                    f"(has_more=true, total unknown); use offset="
                    f"{result.next_offset} for the next page or "
                    "export_bounded=true for a bounded CSV."
                )
                message = f"{message} {msg}" if message else msg
            elif result.budget_exceeded:
                msg = (
                    "Preview stopped at the byte budget (total unknown); lower "
                    "max_cell_chars/max_rows or page with offset."
                )
                message = f"{message} {msg}" if message else msg
            elif result.cell_truncated:
                msg = (
                    "Long text/nested cell(s) were truncated inside SQL; use "
                    "regexp_extract/substr or a narrower projection for the exact field."
                )
                message = f"{message} {msg}" if message else msg

            # Check if results are empty - provide schema hints for agentic retry
            if result.fetched_rows == 0:
                page_total = 0 if budget.offset == 0 else None
                output = DatabaseQueryOutput(
                    data=[],
                    complex_columns=result.complex_columns,
                    sql=direct_sql,
                    count=page_total,
                    total=page_total,
                    has_more=False,
                    truncated=False,
                    fetched_rows=0,
                    fetched_bytes=0,
                    returned_rows=0,
                    returned_bytes=0,
                    budget_exceeded=False,
                    cell_truncated=False,
                    budget=result.budget,
                    timing=result.timing,
                    status="empty",
                    message="Query returned 0 results. Schema context provided for retry.",
                    schema_context=bounded_schema_context,
                )
                payload = output.model_dump(exclude_none=True)
                payload["total"] = page_total
                payload["count"] = page_total
                payload = _with_contract(
                    payload,
                    _statement_contract(args, MAX_ROWS_TO_CONTEXT, MAX_CELL_CHARS_TO_CONTEXT, result),
                    budget,
                )
                return _finalize_response(payload, budget)

            # Include compact schema hint in success response for follow-up query accuracy
            SCHEMA_HINT = _schema_hint()
            output = DatabaseQueryOutput(
                data=results,
                complex_columns=result.complex_columns,
                sql=direct_sql,
                count=result.total,
                total=result.total,
                has_more=result.has_more,
                truncated=result.truncated,
                next_offset=result.next_offset,
                fetched_rows=result.fetched_rows,
                fetched_bytes=result.fetched_bytes,
                returned_rows=result.returned_rows,
                returned_bytes=result.returned_bytes,
                budget_exceeded=result.budget_exceeded,
                cell_truncated=result.cell_truncated,
                budget=result.budget,
                timing=result.timing,
                status="success",
                message=f"{message} | {SCHEMA_HINT}" if message else SCHEMA_HINT,
                schema_context=bounded_schema_context,
            )
            payload = output.model_dump(exclude_none=True)
            # None is a meaningful "total unknown"; exclude_none would erase it.
            payload["total"] = result.total
            payload["count"] = result.total
            payload = _with_contract(
                payload,
                _statement_contract(args, MAX_ROWS_TO_CONTEXT, MAX_CELL_CHARS_TO_CONTEXT, result),
                budget,
            )
            return _finalize_response(payload, budget)
        except SqlBudgetError as e:
            return _finalize_response(
                _error_payload(
                    str(e), e.error_type, budget,
                    schema_context=None if e.error_type == "budget_exceeded" else schema_context,
                    attempted_sql=None if e.error_type == "budget_exceeded" else direct_sql,
                ),
                budget,
            )
        except Exception as e:
            # Return error with schema context for agent to retry
            return _finalize_response(
                _error_payload(
                    str(e), "execution_error", budget,
                    schema_context=schema_context,
                    attempted_sql=direct_sql,
                ),
                budget,
            )

    # If natural language query, return schema context for agent to generate SQL
    output = DatabaseQueryOutput(
        message="Schema context provided for SQL generation",
        user_query=user_query,
        schema_context=_bounded_schema_context(schema_context, budget.max_total_bytes),
        status="needs_sql_generation",
    )
    return _finalize_response(output.model_dump(exclude_none=True), budget)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


if __name__ == "__main__":
    import json as _json, sys as _sys
    try:
        _args = _json.loads(_sys.stdin.read() or "{}")
        result = main(_args)
    except Exception as _exc:  # noqa: BLE001 - JSON error must be a real failure
        result = _finalize_response(
            {"status": "error", "error_type": "invalid_json", "error": str(_exc)}
        )
        print(_json.dumps(result, ensure_ascii=False, cls=DateTimeEncoder))
        _sys.exit(1)
    print(_json.dumps(result, ensure_ascii=False, indent=2, cls=DateTimeEncoder))
    # execute_skill_script keys its status off the return code. A JSON error on
    # stdout is not enough: a failed query must not look like a successful call.
    if result.get("status") == "error":
        _sys.exit(1)
