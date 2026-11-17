"""View builder — DuckDB-native zero-ETL.

R83.2 (P1) cleanup
==================

Pre-R83.2 this module hosted **two** view layers:

* **L1 — recipe-based semantic views** (``v_bgp_neighbors_auto`` /
  ``v_ospf_neighbors_auto`` / ``v_l2_links_auto``) built from
  ``view_recipes`` rows that hard-mapped ``(concept, command, vendor)``
  → ``(canonical_field_names, state_canonicalisation)``.  9 entries
  hand-curated; each new vendor / parser version required a recipe edit.
* **L2 — per-command zero-ETL views** (``v_show_<safe_command>_auto``)
  auto-generated from any non-empty ``parsed_outputs.command`` via
  DuckDB's ``unnest(from_json(parsed_data, json_structure(...)),
  recursive := true)``.

The L1 layer was the same hardcoded mapping that R78 deleted from
Python's ``_DISCOVERY_INTENTS`` / ``_PLATFORM_CONFIG_CMDS``,
re-spelled in YAML.  The user pushback in R83.2 ("A reverts to
hardcoding") was correct and applies to **all** L1 recipes — keeping
BGP/OSPF/L2 was a comfortable historical exception, not a principled
choice.

This file now keeps only:

* :func:`build_per_command_views`  — every command in parsed_outputs
  becomes a typed view via JSON-structure inference.  Pure
  introspection, zero recipes.
* :func:`build_l2_topology_view`   — minimal projection of
  ``netops.topology_links`` as ``netops.v_l2_links_auto`` (hand-coded
  but trivial — no field mappings, no vendor split, just column
  rename for backwards compatibility with consumers).
* :func:`finalise_ingest`          — the single public entry-point
  called by ``/netops_init`` Stage 3.7 and ``take_snapshot``
  post-bulk-load.

State canonicalisation (``Estab`` → ``Established``, ``FULL/DR`` →
``Full/DR``) moved to :mod:`olav_skills.netops.tools.field_normalizer`
applied at ingest time; ``parsed_outputs.parsed_data`` already holds
the RFC names by the time any view reads it.

What was removed
----------------

* ``_BGP_STATE_CASE`` / ``_OSPF_STATE_CASE`` (SQL CASE expressions)
* ``_bgp_branch`` / ``_ospf_branch`` / ``_custom_branch`` (per-vendor
  branch builders)
* ``_BRANCH_BUILDERS`` dispatch + ``_generate_sql_branch``
* ``_fetch_recipes`` / ``ensure_view_recipes_table``
* ``_PROTOCOL_VIEW_NAMES`` / ``view_name_for``
* ``build_one_view`` / ``build_all_views``
* ``rebuild_views_for_command`` (no recipes to match against)

The orphan ``view_recipes`` table is left in the schema for now —
empty after the migration — and can be DROPped on the next round
once any external readers (audit profiles, devops scripts) have
been audited to confirm they don't reference it.
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from typing import Any

logger = logging.getLogger(__name__)


_VIEW_NAME_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _safe_view_name(command: str) -> str:
    """Turn a CLI command into a safe view-name suffix.

    ``"show ip interface brief"`` → ``"show_ip_interface_brief"``;
    ``"show bgp summary | display set"`` → ``"show_bgp_summary_display_set"``.
    Strips leading/trailing underscores; lower-cases.
    """
    return _VIEW_NAME_RE.sub("_", command.strip().lower()).strip("_")


# ── L2 topology view (minimal projection) ───────────────────────────────

_CAPTURE_FIELDS = ("captured_at", "collected_at", "snapshot_date")
_DATE_RE = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
_AWARE_RE = (
    "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?(Z|[+-][0-9]{2}(:?[0-9]{2})?)"
)
_NAIVE_RE = (
    "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?"
)
# ``snapshot_date`` is date-only provenance.  A timestamp is accepted only
# when it is the documented midnight placeholder; a non-midnight value would
# otherwise silently lose time, which is exactly what OW-058 forbids.
_MIDNIGHT_AWARE_RE = "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]00:00:00(Z|[+]00:00)"
_MIDNIGHT_NAIVE_RE = "[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]00:00:00"


def _table_columns(con: Any, table: str) -> list[str]:
    """Columns of ``netops.<table>``, or [] when the table is absent.

    Column introspection keeps this adapter tolerant of old/new databases:
    only collection fields that actually exist are exposed, and a missing
    table never implies a fabricated time.  No metadata table is assumed.
    """
    try:
        rows = con.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'netops' AND table_name = ?
             ORDER BY ordinal_position
            """,
            [table],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("view_builder: column introspection for %s failed: %s", table, exc)
        return []
    return [str(r[0]) for r in rows]


def _capture_union(parsed_cols: list[str], raw_cols: list[str]) -> str:
    """UNION ALL of every explicit collection column that exists.

    ``bundle_ingests`` is deliberately never read: it is a separate,
    explicitly authorized source.  Raw output is a current-state table whose
    ``snapshot_id`` is a last-writer label, so its capture values are marked
    with that source name and only used by its own snapshot label.
    """
    parts: list[str] = []
    for table, cols in (("parsed_outputs", parsed_cols),
                        ("raw_output_store", raw_cols)):
        # raw_output_store without a snapshot_id cannot be keyed to a snapshot.
        # Do not emit invalid SQL for such an old/partial schema.
        if "snapshot_id" not in cols:
            continue
        for field in _CAPTURE_FIELDS:
            if field not in cols:
                continue
            parts.append(
                f"SELECT snapshot_id, '{table}' AS capture_table, "
                f"'{table}.{field}' AS capture_source, "
                f"TRIM(CAST({field} AS VARCHAR)) AS value "
                f"FROM netops.{table} "
                f"WHERE snapshot_id IS NOT NULL AND {field} IS NOT NULL"
            )
    if not parts:
        return (
            "SELECT CAST(NULL AS VARCHAR) AS snapshot_id, "
            "CAST(NULL AS VARCHAR) AS capture_table, "
            "CAST(NULL AS VARCHAR) AS capture_source, "
            "CAST(NULL AS VARCHAR) AS value WHERE FALSE"
        )
    return "\nUNION ALL\n".join(parts)


def build_snapshots_view(con: Any) -> dict[str, int]:
    """Create / refresh ``netops.v_snapshots_auto``.

    Capture and ingestion are separate concepts.  A snapshot's capture time
    is only what an explicit collection column actually recorded; it is not
    ``MIN(ingested_at)`` and it is never parsed out of a ``snap_*`` import ID.
    If no collection field proves a capture, ``captured_at`` is NULL.  The
    old columns stay for compatibility, but their semantics changed:

    * ``captured_at`` / ``capture_start`` — earliest explicit capture value;
      NULL when unavailable.
    * ``finished_at`` / ``capture_end`` — latest explicit capture value;
      NULL when unavailable (it is the capture window end, not ingest end).
    * ``duration_s`` — observed capture window length, not import duration.
    * ``capture_status`` — known / partial / invalid / conflict / unknown.
      Multi-source or mixed date/timestamp/clock provenance is conservative:
      conflicting windows are not silently resolved by source priority.
    * ``capture_precision`` — date / timestamp / timestamp_without_timezone /
      unknown.  Dates remain dates; they are not promoted to midnight timestamps.
    * ``capture_source`` — the explicit field that supplied the window.
    * ``ingested_at`` / ``ingest_start`` / ``ingest_end`` — parsed-output
      ingestion window only.  IDs never supply a time.  An offset-aware
      value is normalised to the same instant in UTC before min/max, so
      ``02:00+02:00`` and ``01:00Z`` produce ``00:00``..``01:00``.  Naive
      values keep their ``timestamp_without_timezone`` meaning; a snapshot
      mixing naive and aware ingestion is ``conflict`` with no window rather
      than a falsely ``known`` comparison.
    * ``ingest_precision`` / ``ingest_clock`` / ``ingest_source`` /
      ``ingest_status`` describe the ingestion window: precision is date /
      timestamp / timestamp_without_timezone / mixed / unknown, clock is
      date / utc / naive / mixed / unknown, status is known / partial /
      invalid / conflict / unknown.  ``ingest_start_text`` /
      ``ingest_end_text`` keep the date or UTC-normalised text form so date
      precision is not re-read as a midnight instant.
    * ``record_updated_at`` / ``record_update_text`` — the **latest record
      update** where the table has one, i.e. ``MAX(updated_at)`` under the
      normalised precision/clock, not the earliest row's start.  Offset-aware
      values are normalised to the same instant in UTC before MAX, dates stay
      dates.  Precision, clock, source and status follow the same rules as
      ingestion; a MAX over mixed clocks is not reported as a known update
      time (NULL instead).  Ingestion keeps its start/end window; only the
      single-valued record update uses the latest/end side.
    * ``event_time`` — always NULL: these tables carry no event-time source.
    * ``capture_date_label`` — a bare ``YYYY-MM-DD`` snapshot ID is kept as a
      *derived* compatibility label; it is not capture proof and never fills
      ``captured_at``.  ``snap_*`` labels are not parsed at all.

    ``bundle_ingests`` is not read implicitly.  Callers that are explicitly
    authorized for that table must use their existing trusted capability;
    the default view is raw/parsed-only and safe without it.
    """
    try:
        parsed_cols = _table_columns(con, "parsed_outputs")
        if not parsed_cols or "snapshot_id" not in parsed_cols:
            logger.debug("build_snapshots_view: netops.parsed_outputs/snapshot_id is absent")
            return {}
        raw_cols = _table_columns(con, "raw_output_store")
        capture_union = _capture_union(parsed_cols, raw_cols)
        source_totals_parts = [
            "SELECT snapshot_id, 'parsed_outputs' AS capture_table, "
            "COUNT(*) AS source_rows FROM netops.parsed_outputs "
            "WHERE snapshot_id IS NOT NULL GROUP BY snapshot_id"
        ]
        if "snapshot_id" in raw_cols:
            source_totals_parts.append(
                "SELECT snapshot_id, 'raw_output_store' AS capture_table, "
                "COUNT(*) AS source_rows FROM netops.raw_output_store "
                "WHERE snapshot_id IS NOT NULL GROUP BY snapshot_id"
            )
        source_totals_sql = "\nUNION ALL\n".join(source_totals_parts)
        ingest_expr = "CAST(ingested_at AS VARCHAR)" if "ingested_at" in parsed_cols else "CAST(NULL AS VARCHAR)"
        updated_expr = "CAST(updated_at AS VARCHAR)" if "updated_at" in parsed_cols else "CAST(NULL AS VARCHAR)"
        priority = """
            CASE capture_source
                WHEN 'parsed_outputs.captured_at' THEN 1
                WHEN 'parsed_outputs.collected_at' THEN 2
                WHEN 'parsed_outputs.snapshot_date' THEN 3
                WHEN 'raw_output_store.captured_at' THEN 4
                WHEN 'raw_output_store.collected_at' THEN 5
                WHEN 'raw_output_store.snapshot_date' THEN 6
                ELSE 9
            END
        """
        con.execute(
            f"""
            CREATE OR REPLACE VIEW netops.v_snapshots_auto AS
            WITH parsed AS (
                SELECT
                    snapshot_id,
                    device_name,
                    {ingest_expr} AS ingested_text,
                    {updated_expr} AS updated_text
                FROM netops.parsed_outputs
                WHERE snapshot_id IS NOT NULL
            ),
            capture_rows AS (
                {capture_union}
            ),
            source_totals AS (
                {source_totals_sql}
            ),
            capture_checked AS (
                SELECT
                    snapshot_id,
                    capture_table,
                    capture_source,
                    value,
                    regexp_full_match(value, '{_DATE_RE}') AS is_date_re,
                    regexp_full_match(value, '{_AWARE_RE}') AS is_aware_re,
                    regexp_full_match(value, '{_NAIVE_RE}') AS is_naive_re,
                    regexp_full_match(value, '{_MIDNIGHT_AWARE_RE}') AS is_midnight_aware,
                    regexp_full_match(value, '{_MIDNIGHT_NAIVE_RE}') AS is_midnight_naive,
                    TRY_CAST(value AS DATE) IS NOT NULL AS date_cast_ok,
                    TRY_CAST(value AS TIMESTAMPTZ) IS NOT NULL AS aware_cast_ok,
                    TRY_CAST(value AS TIMESTAMP) IS NOT NULL AS naive_cast_ok,
                    capture_source LIKE '%.snapshot_date' AS is_snapshot_date_field
                FROM capture_rows
                WHERE value IS NOT NULL AND value <> ''
            ),
            capture_measured AS (
                SELECT
                    snapshot_id,
                    capture_table,
                    capture_source,
                    value,
                    -- A date-named collection field carries date provenance.  A
                    -- timestamp is read as a date only when it is the exact
                    -- documented midnight placeholder (a TIMESTAMP column
                    -- stores a date that way); any other time would lose
                    -- information and is classified invalid below.
                    CASE
                        WHEN is_snapshot_date_field
                         AND (is_midnight_naive OR is_midnight_aware)
                         AND TRY_CAST(substr(value, 1, 10) AS DATE) IS NOT NULL
                        THEN substr(value, 1, 10)
                        ELSE value
                    END AS norm_value,
                    (
                        (is_date_re AND date_cast_ok)
                        OR (is_snapshot_date_field
                            AND (is_midnight_naive OR is_midnight_aware)
                            AND TRY_CAST(substr(value, 1, 10) AS DATE) IS NOT NULL)
                    ) AS is_date,
                    -- A timestamp from a date-named field is never a capture
                    -- instant: the midnight placeholder was already read as a
                    -- date above, so any remaining time here is invalid.
                    (is_aware_re AND aware_cast_ok
                     AND NOT is_snapshot_date_field) AS is_aware,
                    (is_naive_re AND naive_cast_ok
                     AND NOT is_snapshot_date_field) AS is_naive
                FROM capture_checked
            ),
            capture_agg AS (
                SELECT
                    snapshot_id,
                    capture_table,
                    capture_source,
                    COUNT(*) AS value_count,
                    COUNT(*) FILTER (WHERE NOT (is_date OR is_aware OR is_naive)) AS invalid_count,
                    COUNT(*) FILTER (WHERE is_date) AS date_count,
                    COUNT(*) FILTER (WHERE is_aware) AS aware_count,
                    COUNT(*) FILTER (WHERE is_naive) AS naive_count,
                    arg_min(norm_value, TRY_CAST(norm_value AS TIMESTAMP)) FILTER (WHERE is_date) AS date_start,
                    arg_max(norm_value, TRY_CAST(norm_value AS TIMESTAMP)) FILTER (WHERE is_date) AS date_end,
                    arg_min(value, TRY_CAST(value AS TIMESTAMPTZ)) FILTER (WHERE is_aware) AS aware_start,
                    arg_max(value, TRY_CAST(value AS TIMESTAMPTZ)) FILTER (WHERE is_aware) AS aware_end,
                    arg_min(value, TRY_CAST(value AS TIMESTAMP)) FILTER (WHERE is_naive) AS naive_start,
                    arg_max(value, TRY_CAST(value AS TIMESTAMP)) FILTER (WHERE is_naive) AS naive_end
                FROM capture_measured
                GROUP BY snapshot_id, capture_table, capture_source
            ),
            capture_source_class AS (
                SELECT
                    c.*,
                    COALESCE(t.source_rows, c.value_count) AS source_rows,
                    CASE
                        WHEN invalid_count > 0 THEN 'invalid'
                        WHEN (CASE WHEN date_count > 0 THEN 1 ELSE 0 END
                              + CASE WHEN aware_count > 0 THEN 1 ELSE 0 END
                              + CASE WHEN naive_count > 0 THEN 1 ELSE 0 END) > 1 THEN 'mixed'
                        WHEN date_count > 0 THEN 'date'
                        WHEN aware_count > 0 THEN 'timestamp'
                        WHEN naive_count > 0 THEN 'timestamp_without_timezone'
                        ELSE 'unknown'
                    END AS measured_precision,
                    CASE
                        WHEN date_count > 0 THEN date_start
                        WHEN aware_count > 0 THEN aware_start
                        WHEN naive_count > 0 THEN naive_start
                        ELSE NULL
                    END AS start_text,
                    CASE
                        WHEN date_count > 0 THEN date_end
                        WHEN aware_count > 0 THEN aware_end
                        WHEN naive_count > 0 THEN naive_end
                        ELSE NULL
                    END AS end_text
                FROM capture_agg c
                LEFT JOIN source_totals t
                  ON t.snapshot_id = c.snapshot_id
                 AND t.capture_table = c.capture_table
            ),
            capture_summary AS (
                SELECT
                    snapshot_id,
                    SUM(value_count) AS capture_value_count,
                    MAX(value_count) AS max_source_values,
                    COUNT(DISTINCT capture_source) AS capture_source_count,
                    MAX(CASE WHEN measured_precision IN
                        ('date','timestamp','timestamp_without_timezone')
                        THEN 0 ELSE 1 END) AS has_bad_source,
                    MAX(CASE WHEN measured_precision IN
                        ('date','timestamp','timestamp_without_timezone')
                        AND invalid_count = 0
                        AND value_count >= source_rows
                        THEN 1 ELSE 0 END) AS has_complete_source,
                    COUNT(DISTINCT CASE WHEN measured_precision IN
                        ('date','timestamp','timestamp_without_timezone')
                        THEN measured_precision || '|' || start_text || '|' || end_text
                        ELSE NULL END) AS interval_count,
                    arg_min(capture_source, {priority}) AS chosen_source,
                    arg_min(start_text, {priority}) AS chosen_start,
                    arg_min(end_text, {priority}) AS chosen_end,
                    arg_min(measured_precision, {priority}) AS chosen_precision
                FROM capture_source_class
                GROUP BY snapshot_id
            ),
            parsed_summary AS (
                SELECT
                    snapshot_id,
                    COUNT(DISTINCT device_name) AS device_count,
                    COUNT(*) AS row_count
                FROM parsed
                GROUP BY snapshot_id
            ),
            clock_rows AS (
                SELECT snapshot_id, 'ingest' AS clock_field,
                       ingested_text AS raw_text
                FROM parsed
                UNION ALL
                SELECT snapshot_id, 'update' AS clock_field,
                       updated_text AS raw_text
                FROM parsed
            ),
            clock_kind AS (
                SELECT
                    snapshot_id,
                    clock_field,
                    raw_text,
                    CASE
                        WHEN raw_text IS NULL OR TRIM(raw_text) = '' THEN NULL
                        WHEN regexp_full_match(raw_text, '{_DATE_RE}')
                         AND TRY_CAST(raw_text AS DATE) IS NOT NULL THEN 'date'
                        WHEN regexp_full_match(raw_text, '{_AWARE_RE}')
                         AND TRY_CAST(raw_text AS TIMESTAMPTZ) IS NOT NULL THEN 'aware'
                        WHEN regexp_full_match(raw_text, '{_NAIVE_RE}')
                         AND TRY_CAST(raw_text AS TIMESTAMP) IS NOT NULL THEN 'naive'
                        ELSE 'invalid'
                    END AS kind
                FROM clock_rows
            ),
            clock_measured AS (
                SELECT
                    snapshot_id,
                    clock_field,
                    kind,
                    -- Dates keep date text; offset-aware values are rendered as
                    -- the same instant in UTC instead of losing the offset.
                    -- The cast keeps any valid fractional seconds, so the text
                    -- form stays consistent with ``order_ts``.
                    CASE kind
                        WHEN 'date' THEN substr(raw_text, 1, 10)
                        WHEN 'aware' THEN replace(CAST(
                            CAST(raw_text AS TIMESTAMPTZ) AT TIME ZONE 'UTC'
                            AS VARCHAR), ' ', 'T')
                        ELSE replace(raw_text, ' ', 'T')
                    END AS norm_text,
                    CASE kind
                        WHEN 'date' THEN TRY_CAST(substr(raw_text, 1, 10) AS TIMESTAMP)
                        WHEN 'aware' THEN CAST(
                            CAST(raw_text AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS TIMESTAMP)
                        WHEN 'naive' THEN TRY_CAST(raw_text AS TIMESTAMP)
                        ELSE NULL
                    END AS order_ts
                FROM clock_kind
            ),
            clock_summary AS (
                SELECT
                    snapshot_id,
                    clock_field,
                    COUNT(*) FILTER (WHERE kind IS NOT NULL) AS present,
                    COUNT(*) FILTER (WHERE kind = 'invalid') AS invalid_count,
                    COUNT(*) FILTER (WHERE kind = 'date') AS date_count,
                    COUNT(*) FILTER (WHERE kind = 'aware') AS aware_count,
                    COUNT(*) FILTER (WHERE kind = 'naive') AS naive_count,
                    COUNT(DISTINCT kind) FILTER (
                        WHERE kind IN ('date','aware','naive')) AS kind_count,
                    MIN(order_ts) FILTER (
                        WHERE kind IN ('date','aware','naive')) AS start_ts,
                    MAX(order_ts) FILTER (
                        WHERE kind IN ('date','aware','naive')) AS end_ts,
                    arg_min(norm_text, order_ts) FILTER (
                        WHERE kind IN ('date','aware','naive')) AS start_text,
                    arg_max(norm_text, order_ts) FILTER (
                        WHERE kind IN ('date','aware','naive')) AS end_text
                FROM clock_measured
                GROUP BY snapshot_id, clock_field
            ),
            clock_status AS (
                SELECT
                    c.snapshot_id,
                    c.clock_field,
                    c.present,
                    CASE
                        WHEN c.present = 0 THEN 'unknown'
                        WHEN c.invalid_count > 0 THEN 'invalid'
                        WHEN c.kind_count > 1 THEN 'conflict'
                        WHEN c.present < p.row_count THEN 'partial'
                        ELSE 'known'
                    END AS clock_status,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0 THEN 'unknown'
                        WHEN c.kind_count > 1 THEN 'mixed'
                        WHEN c.date_count > 0 THEN 'date'
                        WHEN c.aware_count > 0 THEN 'timestamp'
                        WHEN c.naive_count > 0 THEN 'timestamp_without_timezone'
                        ELSE 'unknown'
                    END AS clock_precision,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0 THEN 'unknown'
                        WHEN c.kind_count > 1 THEN 'mixed'
                        WHEN c.date_count > 0 THEN 'date'
                        WHEN c.aware_count > 0 THEN 'utc'
                        WHEN c.naive_count > 0 THEN 'naive'
                        ELSE 'unknown'
                    END AS clock_clock,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0
                          OR c.kind_count > 1 THEN NULL
                        ELSE c.start_ts
                    END AS clock_start,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0
                          OR c.kind_count > 1 THEN NULL
                        ELSE c.end_ts
                    END AS clock_end,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0
                          OR c.kind_count > 1 THEN NULL
                        ELSE c.start_text
                    END AS clock_start_text,
                    CASE
                        WHEN c.present = 0 OR c.invalid_count > 0
                          OR c.kind_count > 1 THEN NULL
                        ELSE c.end_text
                    END AS clock_end_text
                FROM clock_summary c
                JOIN parsed_summary p USING (snapshot_id)
            ),
            enriched AS (
                SELECT
                    p.*,
                    c.capture_value_count,
                    c.max_source_values,
                    c.capture_source_count,
                    c.has_bad_source,
                    c.has_complete_source,
                    c.interval_count,
                    c.chosen_source,
                    c.chosen_start,
                    c.chosen_end,
                    c.chosen_precision,
                    CASE
                        WHEN c.capture_value_count IS NULL OR c.capture_value_count = 0 THEN 'unknown'
                        WHEN c.has_bad_source > 0 THEN 'invalid'
                        WHEN c.interval_count IS NULL OR c.interval_count <> 1 THEN 'conflict'
                        WHEN c.has_complete_source = 0 THEN 'partial'
                        ELSE 'known'
                    END AS capture_status,
                    CASE WHEN regexp_full_match(p.snapshot_id, '{_DATE_RE}')
                         THEN p.snapshot_id ELSE NULL END AS capture_date_label
                FROM parsed_summary p
                LEFT JOIN capture_summary c USING (snapshot_id)
            )
            SELECT
                e.snapshot_id,
                CASE WHEN capture_status IN ('known','partial')
                     THEN CASE WHEN chosen_precision = 'timestamp'
                          THEN CAST(TRY_CAST(chosen_start AS TIMESTAMPTZ)
                                    AT TIME ZONE 'UTC' AS TIMESTAMP)
                          ELSE TRY_CAST(chosen_start AS TIMESTAMP) END
                     ELSE NULL END AS captured_at,
                CASE WHEN capture_status IN ('known','partial')
                     THEN CASE WHEN chosen_precision = 'timestamp'
                          THEN CAST(TRY_CAST(chosen_end AS TIMESTAMPTZ)
                                    AT TIME ZONE 'UTC' AS TIMESTAMP)
                          ELSE TRY_CAST(chosen_end AS TIMESTAMP) END
                     ELSE NULL END AS finished_at,
                CASE WHEN capture_status IN ('known','partial')
                      AND chosen_start IS NOT NULL AND chosen_end IS NOT NULL
                     THEN CASE WHEN chosen_precision = 'timestamp'
                          THEN EPOCH(TRY_CAST(chosen_end AS TIMESTAMPTZ)
                                     - TRY_CAST(chosen_start AS TIMESTAMPTZ))
                          ELSE EPOCH(TRY_CAST(chosen_end AS TIMESTAMP)
                                     - TRY_CAST(chosen_start AS TIMESTAMP)) END
                     ELSE NULL END AS duration_s,
                device_count,
                row_count,
                CASE WHEN capture_status IN ('known','partial')
                     THEN chosen_start ELSE NULL END AS capture_start,
                CASE WHEN capture_status IN ('known','partial')
                     THEN chosen_end ELSE NULL END AS capture_end,
                CASE WHEN capture_status IN ('known','partial')
                     THEN chosen_precision ELSE 'unknown' END AS capture_precision,
                CASE WHEN capture_status IN ('known','partial')
                     THEN chosen_source ELSE NULL END AS capture_source,
                capture_status,
                capture_date_label,
                CASE WHEN capture_date_label IS NOT NULL
                     THEN 'derived' ELSE 'unknown' END AS capture_date_label_status,
                -- Ingestion and update clocks: UTC-normalised for aware
                -- values, naive kept naive, mixed clocks never "known".
                CASE WHEN i.clock_status IN ('known','partial')
                     THEN i.clock_start END AS ingested_at,
                CASE WHEN i.clock_status IN ('known','partial')
                     THEN i.clock_start END AS ingest_start,
                CASE WHEN i.clock_status IN ('known','partial')
                     THEN i.clock_end END AS ingest_end,
                CASE WHEN i.clock_status IN ('known','partial')
                     THEN i.clock_start_text END AS ingest_start_text,
                CASE WHEN i.clock_status IN ('known','partial')
                     THEN i.clock_end_text END AS ingest_end_text,
                COALESCE(i.clock_precision, 'unknown') AS ingest_precision,
                COALESCE(i.clock_clock, 'unknown') AS ingest_clock,
                COALESCE(i.clock_status, 'unknown') AS ingest_status,
                CASE WHEN i.present > 0
                     THEN 'parsed_outputs.ingested_at' ELSE NULL END AS ingest_source,
                -- Single-valued record update = LATEST update, i.e. MAX
                -- under the normalised precision/clock (u.clock_end / end_text
                -- is MAX(order_ts)/arg_max).  Ingestion above keeps start/end.
                CASE WHEN u.clock_status IN ('known','partial')
                     THEN u.clock_end END AS record_updated_at,
                CASE WHEN u.clock_status IN ('known','partial')
                     THEN u.clock_end_text END AS record_update_text,
                COALESCE(u.clock_precision, 'unknown') AS record_update_precision,
                COALESCE(u.clock_clock, 'unknown') AS record_update_clock,
                COALESCE(u.clock_status, 'unknown') AS record_update_status,
                CASE WHEN u.present > 0
                     THEN 'parsed_outputs.updated_at' ELSE NULL END AS record_update_source,
                CAST(NULL AS TIMESTAMP) AS event_time,
                'unknown' AS event_status
            FROM enriched e
            LEFT JOIN clock_status i
              ON i.snapshot_id = e.snapshot_id AND i.clock_field = 'ingest'
            LEFT JOIN clock_status u
              ON u.snapshot_id = e.snapshot_id AND u.clock_field = 'update'
            ORDER BY captured_at DESC NULLS LAST, e.snapshot_id
            """
        )
        n = con.execute("SELECT COUNT(*) FROM netops.v_snapshots_auto").fetchone()[0]
        return {"v_snapshots_auto": int(n)}
    except Exception as exc:
        logger.warning("build_snapshots_view failed: %s", exc)
        return {}


def _snapshot_row_temporal(row: dict) -> dict:
    """Translate one ``v_snapshots_auto`` row into the OW-058 time shape."""
    from .query.temporal import unknown

    status = row.get("capture_status")
    if status in ("known", "partial") and row.get("capture_start"):
        value = {
            "value": row["capture_start"],
            "precision": row.get("capture_precision") or "unknown",
            "source": row.get("capture_source"),
            "status": status,
        }
        if row.get("capture_end") and row["capture_end"] != row["capture_start"]:
            value["range_start"] = row["capture_start"]
            value["range_end"] = row["capture_end"]
        return value
    # Explicit provenance that is invalid/conflicting (or inconsistent with a
    # usable window) must block the derived date-label fallback.  A bad
    # collected_at is evidence of a problem, not permission to infer a date
    # from the snapshot_id.
    if status and status != "unknown":
        return unknown(row.get("capture_source"), status)
    if row.get("capture_date_label"):
        return {
            "value": row["capture_date_label"],
            "precision": "date",
            "source": "snapshot_id.date_label",
            "status": "derived",
        }
    return unknown("v_snapshots_auto")


def _read_snapshot_rows(con: Any, snapshot_ids: list[str] | None = None) -> list[dict]:
    sql = "SELECT * FROM netops.v_snapshots_auto"
    params: list[str] = []
    if snapshot_ids is not None:
        ids = list(dict.fromkeys(snapshot_ids))
        if not ids:
            return []
        sql += " WHERE snapshot_id IN (" + ",".join("?" for _ in ids) + ")"
        params.extend(ids)
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_snapshot_temporal(con: Any, snapshot_id: str) -> dict:
    """Capture provenance for an explicitly selected snapshot."""
    from .query.temporal import unknown

    rows = _read_snapshot_rows(con, [snapshot_id])
    if not rows:
        return {
            "snapshot_id": snapshot_id,
            "status": "missing",
            "temporal": unknown("v_snapshots_auto", "missing"),
        }
    row = rows[0]
    return {
        "snapshot_id": snapshot_id,
        "status": row.get("capture_status") or "unknown",
        "temporal": _snapshot_row_temporal(row),
    }


def select_latest_snapshot(con: Any, *, snapshot_ids: list[str] | None = None) -> dict:
    """Conservatively choose the newest snapshot by capture provenance.

    Never orders by snapshot ID or import recency.  With more than one
    candidate, every candidate needs a complete, comparable, non-overlapping
    capture interval; otherwise the result is ``status='ambiguous'`` and the
    caller must ask for an explicit ``snapshot_id``.  A single candidate may
    still be used when its time is unknown/partial, but callers must not call
    it "current".  ``snapshot_ids`` optionally restricts the candidates (for
    example to snapshots that carry running-config rows).
    """
    from .query.temporal import interval, unknown

    rows = _read_snapshot_rows(con, snapshot_ids)
    requested = list(dict.fromkeys(snapshot_ids)) if snapshot_ids is not None else None
    present = {r["snapshot_id"] for r in rows}
    missing = [sid for sid in (requested or []) if sid not in present]
    if missing:
        # A candidate with no parsed/view row could be newer than every
        # candidate we can see.  Dropping it would turn the remaining row into
        # a false "single snapshot" fallback, so keep it as unknown and refuse
        # to choose an implicit latest.
        return {
            "snapshot_id": None,
            "status": "ambiguous",
            "reason": "missing_candidate_metadata",
            "temporal": unknown("v_snapshots_auto", "ambiguous"),
            "candidates": sorted(set(requested or [])),
            "missing_candidates": missing,
        }
    if not rows:
        return {
            "snapshot_id": None,
            "status": "none",
            "reason": "no_snapshot",
            "temporal": unknown("v_snapshots_auto", "missing"),
            "candidates": [],
        }
    if len(rows) == 1:
        row = rows[0]
        return {
            "snapshot_id": row["snapshot_id"],
            "status": "single",
            "reason": "single_snapshot",
            "temporal": _snapshot_row_temporal(row),
            "candidates": [row["snapshot_id"]],
        }

    temporals = {r["snapshot_id"]: _snapshot_row_temporal(r) for r in rows}
    intervals = {sid: interval(t) for sid, t in temporals.items()}
    if all(intervals.values()):
        for sid, candidate in intervals.items():
            others = [v for k, v in intervals.items() if k != sid]
            compatible = all(candidate[2] == v[2] for v in others)
            if compatible and all(
                (candidate[0] >= v[1] if candidate[2] == "date" else candidate[0] > v[1])
                for v in others
            ):
                return {
                    "snapshot_id": sid,
                    "status": "ok",
                    "reason": "latest_by_capture_time",
                    "temporal": temporals[sid],
                    "candidates": sorted(temporals),
                }
    return {
        "snapshot_id": None,
        "status": "ambiguous",
        "reason": "ambiguous_snapshot_time",
        "temporal": unknown("v_snapshots_auto", "ambiguous"),
        "candidates": sorted(temporals),
    }


def build_l2_topology_view(con: Any) -> dict[str, int]:
    """Create / refresh ``netops.v_l2_links_auto``.

    Pure column-rename projection of ``netops.topology_links`` —  no
    field mappings, no vendor split.  Kept as a stable name so
    downstream consumers (analyze skill SQL examples, audit profiles)
    don't have to know about the ``topology_links`` table directly.
    """
    try:
        con.execute(
            """
            CREATE OR REPLACE VIEW netops.v_l2_links_auto AS
            SELECT
                source_device,
                source_interface,
                destination_device,
                destination_interface,
                discovery_protocol,
                link_status,
                snapshot_id
            FROM netops.topology_links
            """
        )
        n = con.execute("SELECT COUNT(*) FROM netops.v_l2_links_auto").fetchone()[0]
        return {"v_l2_links_auto": int(n)}
    except Exception as exc:
        logger.warning("build_l2_topology_view failed: %s", exc)
        return {}


# ── DuckDB-native zero-ETL: per-command auto-views ──────────────────────
#
# For every command in ``netops.parsed_outputs``, materialise (as a
# VIEW, so no storage cost) a typed table where each column
# corresponds to a key in the parser's JSON output.
#
# Pattern (DuckDB-native, no field mappings, no recipes):
#
#   CREATE OR REPLACE VIEW netops.v_<safe_command>_auto AS
#   SELECT p.device_name, p.snapshot_id,
#          unnest(from_json(p.parsed_data, '<inferred-structure>'),
#                 recursive := true)
#   FROM netops.parsed_outputs p
#   WHERE p.command = '<command>'
#     AND p.snapshot_id = (SELECT MAX(snapshot_id) FROM netops.parsed_outputs s
#                          WHERE s.device_name = p.device_name AND s.command = p.command)
#
# ``json_structure`` infers the JSON shape from a sample row; the
# resulting view has typed columns and DESCRIBE works natively.  Agent
# can ``SELECT col FROM v_show_ip_interface_brief_auto WHERE status LIKE '%down%'``
# without any LATERAL+json_each gymnastics.

def build_per_command_views(con: Any) -> dict[str, int]:
    """Auto-create ``v_<safe_command>_auto`` views from parsed_outputs.

    Iterates every distinct command that has at least one parsed row,
    detects the JSON structure with ``json_structure(parsed_data)``,
    and CREATE OR REPLACE VIEW with ``unnest(from_json(...), recursive
    := true)`` — DuckDB exposes typed columns identical to the
    parser's output schema.

    Per-(device, command) latest-snapshot filter is applied inside the
    view so consumers don't need to know about snapshots.

    Idempotent; safe to re-run on every ``/netops_init`` and after any
    new parser is learned.

    Returns ``{view_name: row_count}`` for each successfully-created
    view; commands whose JSON structure couldn't be inferred (empty
    arrays, scalars, etc.) are silently skipped.
    """
    results: dict[str, int] = {}
    try:
        # Pick the most-recent non-empty parsed row per command and
        # ask DuckDB for its JSON structure.  ``json_structure``
        # returns a string like ``'[{"interface":"VARCHAR",...}]'``
        # which becomes the constant we bake into the view DDL.
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT command, parsed_data,
                       ROW_NUMBER() OVER (PARTITION BY command ORDER BY ingested_at DESC NULLS LAST) AS rn
                FROM netops.parsed_outputs
                WHERE parsed_data IS NOT NULL
                  AND parsed_data::VARCHAR NOT IN ('[]', 'null')
            )
            SELECT command, json_structure(parsed_data)::VARCHAR AS struct
            FROM ranked
            WHERE rn = 1
            """
        ).fetchall()
    except Exception as exc:
        logger.warning("build_per_command_views: parsed_outputs scan failed: %s", exc)
        return results

    for command, structure in rows:
        if not command or not structure:
            continue
        # Only object-list shapes work — scalar / mixed shapes can't be
        # unnested into named columns.  Skip silently.
        if not structure.startswith("[{"):
            continue

        view_suffix = _safe_view_name(command)
        if not view_suffix:
            continue
        view_name = f"v_{view_suffix}_auto"

        cmd_lit = command.replace("'", "''")
        struct_lit = structure.replace("'", "''")

        # Latest snapshot per (device, command) is selected with
        # ``MAX(snapshot_id)`` (string order) then an ordinary equality JOIN.
        # This keeps every row that shares the maximum snapshot_id (no
        # UNIQUE guarantee on DuckLake), ignores NULL equality (NULL device
        # or NULL snapshot_id never matches, exactly like the original
        # correlated ``= (SELECT MAX(...))``), and still filters
        # ``parsed_data IS NOT NULL`` *after* the latest snapshot is chosen,
        # so a device whose latest snapshot has NULL parsed_data yields zero
        # rows instead of falling back to an older snapshot.
        ddl = f"""
            CREATE OR REPLACE VIEW netops.{view_name} AS
            WITH latest AS (
                SELECT device_name, MAX(snapshot_id) AS max_snapshot_id
                FROM netops.parsed_outputs
                WHERE command = '{cmd_lit}'
                GROUP BY device_name
            )
            SELECT p.device_name,
                   p.snapshot_id,
                   unnest(from_json(p.parsed_data, '{struct_lit}'), recursive := true)
            FROM netops.parsed_outputs p
            JOIN latest l
              ON l.device_name = p.device_name
             AND l.max_snapshot_id = p.snapshot_id
            WHERE p.command = '{cmd_lit}'
              AND p.parsed_data IS NOT NULL
        """
        try:
            con.execute(ddl)
            count = con.execute(f"SELECT COUNT(*) FROM netops.{view_name}").fetchone()[0]
            results[view_name] = int(count)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "build_per_command_views: skipping %s (cmd=%r): %s",
                view_name, command, exc,
            )
    return results


# ── R83.3: Value profile + introspection cache ──────────────────────────
#
# Two materialised tables built at the end of every finalise_ingest:
#
# * ``netops.value_profile``   — for each per-command auto-view, every
#   low-cardinality (≤ 30 distinct) string column gets its (value, freq,
#   fingerprint) triplet.  Fingerprint = OpenRefine-style canonicalisation
#   (lower → strip punctuation → token-sort → join) — same algorithm the
#   OpenRefine GUI uses for its "Cluster" feature.  No new dependency;
#   pure DuckDB SQL.
#
# * ``netops.introspection_cache`` — a single-row table aggregating fleet
#   diversity + view list + per-view typed columns + value distributions
#   into one JSON bag.  Agent reads it ONCE per NL question and gets the
#   full data dictionary; subsequent SQL is the actual data fetch.
#
# Why: agent NL question previously needed 4-6 SQL round-trips
# (introspect platforms, list views, DESCRIBE per view, then data SELECT).
# With the cache, that drops to 2 (introspect, data).  Small models in
# particular benefit because they don't have to remember to do
# step-by-step introspection — the cache hands them everything in one
# read.  See dev_docs/56. THREE_LAYER_INTROSPECTION_CACHE.md for design.


def _ensure_value_profile_table(con: Any) -> None:
    """DDL for ``netops.value_profile``.  Idempotent."""
    from olav_skills.core.ingest_write import is_ducklake
    constraint = "" if is_ducklake(con) else ", PRIMARY KEY (view_name, column_name, value)"
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS netops.value_profile (
            view_name    VARCHAR NOT NULL,
            column_name  VARCHAR NOT NULL,
            value        VARCHAR,
            freq         INTEGER NOT NULL,
            fingerprint  VARCHAR,
            cluster_id   INTEGER
            {constraint}
        )
        """
    )


# R83.3 Phase A.5: split column scope into "categorical" (worth profiling)
# vs "state-like" (worth surfacing in the introspection_cache JSON).
#
# Identity / dimensional columns add noise to the cache without giving the
# agent useful equivalence-class info — exclude from value_profile entirely.
# Things like device_name, snapshot_id, ip_address, mac_address are
# already addressable via netops.devices / dimensional joins and don't
# benefit from fingerprint clustering.

_VALUE_PROFILE_SKIP_COLS = frozenset({
    "device_name", "hostname", "snapshot_id",
    "ip_address", "address", "neighbor_ip", "router_id",
    "mac_address", "hardware_address", "physical_address",
    "interface", "local_interface", "neighbor_interface",  # interface NAMES
    "port",                              # interface short name
    "name", "neighbor_name", "platform_id", "platform",
    "vlan_id", "neighbor_id",
    "prefix", "network", "next_hop",
})

# Columns whose distinct values are worth surfacing in
# introspection_cache.value_distributions JSON (i.e. concepts the agent
# should consider when writing WHERE clauses).  Pattern-matched
# substring against column name (case-insensitive).
import re as _re_mod
_STATE_COL_PATTERN = _re_mod.compile(
    r"(state|status|proto|protocol|line_protocol|admin|oper)$",
    _re_mod.IGNORECASE,
)


def _is_state_like_col(name: str) -> bool:
    """Heuristic: column name suggests a finite-state concept worth canonicalising."""
    n = (name or "").strip().lower()
    if not n:
        return False
    return bool(_STATE_COL_PATTERN.search(n))


def _column_cardinalities(con: Any, source: str, cols: list[str], controlled: bool) -> dict:
    """Distinct-count for every candidate column, single scan, with fallback.

    ``source`` is an already-quoted FROM relation — either ``netops."v_x_auto"``
    or the raw name of a per-call TEMP table materialised from that view.
    Returns ``{column: distinct_count}``.  A single combined probe is used
    first (one scan instead of one per column); on failure in a non-controlled
    connection it degrades to one probe per column, preserving the legacy
    per-column fault isolation.  In a controlled (ducklake) connection failures
    propagate, matching the rest of the value-profile path.
    """
    def _probe(col: str):
        try:
            return con.execute(
                f'SELECT COUNT(DISTINCT "{col}") FROM {source}'
            ).fetchone()[0]
        except Exception as exc:
            if controlled:
                raise
            logger.debug("value_profile: cardinality probe %s.%s failed: %s",
                         source, col, exc)
            return None

    if len(cols) == 1:
        return {cols[0]: _probe(cols[0])}
    try:
        expr = ", ".join(f'COUNT(DISTINCT "{c}")' for c in cols)
        row = con.execute(f'SELECT {expr} FROM {source}').fetchone()
        return dict(zip(cols, row))
    except Exception as exc:
        if controlled:
            raise
        logger.debug("value_profile: single-pass cardinality for %s failed: %s",
                     source, exc)
        return {c: _probe(c) for c in cols}


def _materialize_profile_columns(con: Any, view_name: str, cols: list[str]) -> str:
    """Materialise the candidate columns of ``netops.<view_name>`` exactly once.

    Executes the view body (``unnest(from_json(...), recursive := true)`` over
    the latest-snapshot join) a single time and stores the projected candidate
    columns into a connection-local TEMP table with a random, safe identifier.
    The cardinality probe and every per-column freq/fingerprint GROUP BY then
    read this plain materialised scan instead of re-parsing the view body per
    column.

    Returns the TEMP table name; the caller MUST ``DROP TABLE IF EXISTS`` it in
    a ``finally``.  Raises on failure — the caller chooses the controlled
    (propagate) vs native (fall back to per-column probing) path, matching the
    value-profile loop's fault model.
    """
    tmp = f"_ow143_vp_{uuid.uuid4().hex}"
    col_list = ", ".join(f'"{c}"' for c in cols)
    con.execute(
        f'CREATE TEMP TABLE {tmp} AS SELECT {col_list} FROM netops."{view_name}"'
    )
    return tmp


def _drop_profile_temp(con: Any, tmp: str) -> None:
    """Drop a per-call profile TEMP relation.

    Factored out of ``build_value_profile`` so the fault-path tests can inject a
    controlled DROP failure via a module monkeypatch; the production path keeps
    the explicit cleanup semantics described in that loop.
    """
    con.execute(f"DROP TABLE IF EXISTS {tmp}")


def _profile_view_legacy(
    con: Any,
    view_name: str,
    cols: list[str],
    insert_verb: str,
    max_cardinality: int,
) -> None:
    """Native per-column fallback for a view whose single materialisation failed.

    Replicates the pre-r6 fault isolation: probe and insert one column at a
    time against ``netops."<view_name>"``, skipping only the column whose probe
    or insert fails while keeping the other columns of the same view.  This is
    the path that preserves a still-readable column when a sibling typed column
    errors (e.g. ``CAST('invalid' AS INTEGER)::VARCHAR``) and must never be used
    to degrade the controlled (ducklake) strict path — callers only reach here
    after the controlled branch has already raised on materialisation failure.
    """
    for col in cols:
        try:
            n = con.execute(
                f'SELECT COUNT(DISTINCT "{col}") FROM netops."{view_name}"'
            ).fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            logger.debug("value_profile: cardinality probe %s.%s failed: %s",
                         view_name, col, exc)
            continue
        if not n or n > max_cardinality:
            continue
        try:
            con.execute(
                f"""
                {insert_verb} netops.value_profile
                    (view_name, column_name, value, freq, fingerprint, cluster_id)
                WITH raw AS (
                    SELECT "{col}" AS value, COUNT(*) AS freq,
                           array_to_string(
                               list_distinct(list_sort(
                                   regexp_split_to_array(
                                       lower(regexp_replace(
                                           CAST("{col}" AS VARCHAR),
                                           '[^a-z0-9 ]', '', 'g')),
                                       '\\s+'))),
                               ' ') AS fingerprint
                    FROM netops."{view_name}"
                    WHERE "{col}" IS NOT NULL
                    GROUP BY "{col}"
                )
                SELECT '{view_name}' AS view_name,
                       '{col}'      AS column_name,
                       value, freq, fingerprint,
                       dense_rank() OVER (ORDER BY fingerprint) AS cluster_id
                  FROM raw
                """
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("value_profile: insert %s.%s failed: %s",
                         view_name, col, exc)


def _profile_insert_sql(tmp: str, view_name: str, col: str, insert_verb: str) -> str:
    """Single-column freq/fingerprint/cluster INSERT from a materialised TEMP scan.

    This is the exact per-column statement used before batching and is retained as
    the native fallback when the batched statement fails, so a failing column can
    still be skipped without losing the other columns of the same view.
    """
    return f"""
    {insert_verb} netops.value_profile
        (view_name, column_name, value, freq, fingerprint, cluster_id)
    WITH raw AS (
        SELECT "{col}" AS value, COUNT(*) AS freq,
               array_to_string(
                   list_distinct(list_sort(
                       regexp_split_to_array(
                           lower(regexp_replace(
                               CAST("{col}" AS VARCHAR),
                               '[^a-z0-9 ]', '', 'g')),
                           '\\s+'))),
                   ' ') AS fingerprint
        FROM {tmp}
        WHERE "{col}" IS NOT NULL
        GROUP BY "{col}"
    )
    SELECT '{view_name}' AS view_name,
           '{col}'      AS column_name,
           value, freq, fingerprint,
           dense_rank() OVER (ORDER BY fingerprint) AS cluster_id
      FROM raw
    """


def _profile_insert_batch_sql(
    tmp: str,
    view_name: str,
    cols: list[str],
    insert_verb: str,
) -> str:
    """One INSERT statement for every low-cardinality column of a view.

    Collapses K per-column statements into one, removing DuckLake
    per-statement/file metadata cost.  ``cluster_id`` stays ``dense_rank()``
    partitioned per ``column_name``, so the output is identical to the
    per-column ``dense_rank() OVER (ORDER BY fingerprint)`` (each column ranks
    its own fingerprints from 1).
    """
    branches = [
        f"""
        SELECT '{col}' AS column_name,
               "{col}" AS value,
               array_to_string(
                   list_distinct(list_sort(
                       regexp_split_to_array(
                           lower(regexp_replace(
                               CAST("{col}" AS VARCHAR),
                               '[^a-z0-9 ]', '', 'g')),
                           '\\s+'))),
                   ' ') AS fingerprint,
               COUNT(*) AS freq
        FROM {tmp}
        WHERE "{col}" IS NOT NULL
        GROUP BY "{col}"
        """
        for col in cols
    ]
    union = "\nUNION ALL\n".join(branches)
    return f"""
    {insert_verb} netops.value_profile
        (view_name, column_name, value, freq, fingerprint, cluster_id)
    WITH per_col AS (
        {union}
    )
    SELECT '{view_name}' AS view_name,
           column_name,
           value,
           freq,
           fingerprint,
           dense_rank() OVER (PARTITION BY column_name ORDER BY fingerprint) AS cluster_id
    FROM per_col
    """


def build_value_profile(con: Any, *, max_cardinality: int = 30) -> dict[str, int]:
    """Profile every low-cardinality categorical column in the auto-views.

    Iterates ``information_schema.views`` for ``v_%_auto`` views in
    ``netops``, finds each VARCHAR column whose distinct-count is at
    most ``max_cardinality``, and writes one row per (view, column,
    value) into ``netops.value_profile`` along with an OpenRefine-style
    fingerprint and a cluster id.

    Skips identity / dimensional columns (``_VALUE_PROFILE_SKIP_COLS``)
    so the table holds only categorical/state-like data worth
    fingerprint clustering.

    Cluster id is currently per-fingerprint within a (view, column);
    Levenshtein-merge of similar fingerprints is a Phase-B follow-up
    (see design doc §3 Phase B).
    """
    from olav_skills.core.ingest_write import is_ducklake
    controlled = is_ducklake(con)
    insert_verb = "INSERT INTO" if controlled else "INSERT OR REPLACE INTO"
    _ensure_value_profile_table(con)

    con.execute("DELETE FROM netops.value_profile")  # full refresh per init
    views = con.execute(
        """
        SELECT table_name FROM information_schema.views
         WHERE table_schema='netops' AND table_name LIKE 'v_%_auto'
        """
    ).fetchall()
    view_names = [r[0] for r in views]

    for view_name in view_names:
        try:
            cols = con.execute(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema='netops' AND table_name = ?
                   AND data_type IN ('VARCHAR', 'TEXT')
                """,
                [view_name],
            ).fetchall()
        except Exception as exc:
            if controlled:
                raise
            logger.debug("value_profile: column scan for %s failed: %s", view_name, exc)
            continue

        candidate_cols = [c for (c,) in cols if c.lower() not in _VALUE_PROFILE_SKIP_COLS]
        if not candidate_cols:
            continue

        # OW143 r6: materialise the view body ONCE per call into a per-call
        # TEMP table, then run the cardinality probe and every column's
        # freq/fingerprint GROUP BY against that plain scan.  This removes the
        # repeated execution of the view body (unnest(from_json(...)) over the
        # latest-snapshot join) that r4 still paid once per column probe.
        #
        # OW143 r7: a single-pass materialisation fails atomically when one
        # typed column errors (e.g. CAST('invalid' AS INTEGER)::VARCHAR) even
        # though sibling columns are still readable.  That is NOT equivalent to
        # the old per-column scan failure — r6 wrongly skipped the whole view.
        # On the native path we fall back to the legacy per-column probe/insert
        # so the still-readable columns keep their profiles; controlled keeps
        # its strict fail-fast semantics (never degrade around the error).
        try:
            tmp = _materialize_profile_columns(con, view_name, candidate_cols)
        except Exception as exc:  # noqa: BLE001
            if controlled:
                raise
            logger.debug(
                "value_profile: materialise %s failed, falling back to "
                "per-column probing: %s", view_name, exc,
            )
            _profile_view_legacy(
                con, view_name, candidate_cols, insert_verb, max_cardinality,
            )
            continue
        try:
            cardinality = _column_cardinalities(con, tmp, candidate_cols, controlled)
            profile_cols = [c for c in candidate_cols
                            if cardinality.get(c) and cardinality.get(c) <= max_cardinality]
            if profile_cols:
                try:
                    con.execute(
                        _profile_insert_batch_sql(
                            tmp, view_name, profile_cols, insert_verb,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    if controlled:
                        raise
                    logger.debug(
                        "value_profile: batch insert %s failed, per-column "
                        "fallback: %s", view_name, exc,
                    )
                    for col in profile_cols:
                        try:
                            con.execute(
                                _profile_insert_sql(tmp, view_name, col, insert_verb)
                            )
                        except Exception as col_exc:  # noqa: BLE001
                            logger.debug("value_profile: insert %s.%s failed: %s",
                                         view_name, col, col_exc)
        finally:
            # Per-call TEMP relation must not survive the call (or mask a
            # primary exception).  Cleanup semantics (OW143 r7):
            #   * a primary exception is always preserved — a failed DROP is
            #     only debug-logged, never re-raised over it;
            #   * with no primary exception, a failed DROP is a real failure:
            #     controlled raises (never claims success), native logs a
            #     warning.  The random per-call name means no later call can
            #     reuse the leftover relation, and DuckDB drops TEMP tables on
            #     connection close, so no cross-task object survives.
            primary = sys.exc_info()[0] is not None
            try:
                _drop_profile_temp(con, tmp)
            except Exception as drop_exc:  # noqa: BLE001
                if not primary:
                    if controlled:
                        raise
                    logger.warning(
                        "value_profile: failed to drop temp %s after profiling "
                        "%s: %s", tmp, view_name, drop_exc,
                    )
                else:
                    logger.debug(
                        "value_profile: drop temp %s failed while propagating "
                        "primary error: %s", tmp, drop_exc,
                    )

    rows = con.execute("SELECT COUNT(*) FROM netops.value_profile").fetchone()[0]
    return {"value_profile_rows": int(rows)}


def build_introspection_cache(con: Any) -> dict[str, int]:
    """Single-row JSON-bag table summarising the fleet + views + values.

    Replaces 4-6 separate introspection queries on every NL question
    with one ``SELECT * FROM netops.introspection_cache``.

    The JSON columns are intentionally pre-aggregated (not lazy
    sub-selects) — querying this table is O(1) once it's built.
    Refreshed on every ``finalise_ingest`` call.
    """
    try:
        con.execute(
            """
            CREATE OR REPLACE TABLE netops.introspection_cache AS
            SELECT
                (SELECT json_group_array(json_object(
                            'hostname', hostname,
                            'platform', platform,
                            'role',     role,
                            'site',     site,
                            'ip',       ip_address))
                 FROM netops.devices)                                          AS fleet,

                (SELECT json_group_array(json_object('view', view_name, 'cols', cols))
                 FROM (
                     -- json_group_array is a MACRO (not an aggregate) so it
                     -- doesn't accept ORDER BY in DuckDB.  Pre-order the
                     -- columns in a sub-CTE and aggregate the already-sorted
                     -- list.
                     WITH ordered_cols AS (
                         SELECT v.table_name AS view_name,
                                c.column_name, c.data_type, c.ordinal_position
                         FROM information_schema.views v
                         JOIN information_schema.columns c
                              ON c.table_schema = v.table_schema
                             AND c.table_name = v.table_name
                         WHERE v.table_schema = 'netops'
                           AND v.table_name LIKE 'v_%_auto'
                         ORDER BY v.table_name, c.ordinal_position
                     )
                     SELECT view_name,
                            json_group_array(json_object(
                                'name', column_name,
                                'type', data_type)) AS cols
                     FROM ordered_cols
                     GROUP BY view_name
                 ))                                                            AS views,

                -- R83.3 Phase A.5: only state-like columns make it into
                -- the cache JSON.  Full categorical profile remains
                -- queryable via ``SELECT * FROM netops.value_profile``
                -- when the agent needs interface lists, MAC tables, etc.
                -- This keeps the JSON bag tight (≤ a few KB on a small
                -- fleet) so small models can parse it reliably.
                (SELECT json_group_array(json_object(
                            'view',    view_name,
                            'col',     column_name,
                            'value',   value,
                            'freq',    freq,
                            'cluster', cluster_id))
                 FROM netops.value_profile
                 WHERE regexp_matches(lower(column_name),
                                       '(state|status|proto|protocol|admin|oper)$'))
                                                                              AS value_distributions,

                NOW() AS refreshed_at
            """
        )
        return {"introspection_cache_rows": 1}
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("build_introspection_cache failed: %s", exc)
        return {}


# ── Finalise-ingest entry point ─────────────────────────────────────────

def finalise_ingest(con: Any, *, defer_external: bool = False) -> dict[str, Any]:
    """Rebuild every view consumers query, in the right order.

    Called after any code path that writes to ``netops.parsed_outputs``
    or ``netops.topology_links`` (``/netops_init`` Stage 3.7 and
    ``take_snapshot`` post-bulk-load).  Idempotent — safe to call
    repeatedly; CREATE OR REPLACE handles schema drift in either
    direction.

    Returns a stat dict::

        {
          "snapshots": {"v_snapshots_auto": N},
          "l2": {"v_l2_links_auto": N},
          "per_command": {<v_show_..._auto>: N, ...},
          "value_profile": {"value_profile_rows": N},
          "introspection": {"introspection_cache_rows": 1},
        }

    Failures in either layer log at WARN but don't raise — view
    building is advisory; raw ``parsed_outputs`` queries always work.
    """
    out: dict[str, Any] = {"snapshots": {}, "l2": {}, "per_command": {}, "value_profile": {}, "introspection": {}}
    try:
        out["snapshots"] = build_snapshots_view(con)
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("finalise_ingest: build_snapshots_view failed: %s", exc)
    try:
        out["l2"] = build_l2_topology_view(con)
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("finalise_ingest: build_l2_topology_view failed: %s", exc)
    try:
        out["per_command"] = build_per_command_views(con)
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("finalise_ingest: build_per_command_views failed: %s", exc)
    # R83.3: profile + cache run AFTER per-command views exist (they
    # scan those views for categorical distributions).  Both depend on
    # netops.devices being populated — so the caller must run
    # populate_devices BEFORE finalise_ingest.
    try:
        out["value_profile"] = build_value_profile(con)
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("finalise_ingest: build_value_profile failed: %s", exc)
    try:
        out["introspection"] = build_introspection_cache(con)
    except Exception as exc:
        from olav_skills.core.ingest_write import is_ducklake
        if is_ducklake(con):
            raise
        logger.warning("finalise_ingest: build_introspection_cache failed: %s", exc)
    if defer_external:
        out["external"] = {"state": "deferred"}
        return out

    # Native skills are deterministic: provider configuration must never
    # activate platform embedding, memory or guide-upsert services.
    out["memory_primer"] = {"skipped": "unsupported_in_native_skills"}
    out["usage_guides"] = {"skipped": "unsupported_in_native_skills"}
    return out


__all__ = [
    "build_snapshots_view",
    "build_l2_topology_view",
    "build_per_command_views",
    "finalise_ingest",
    "get_snapshot_temporal",
    "select_latest_snapshot",
]
