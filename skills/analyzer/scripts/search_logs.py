#!/usr/bin/env python3
"""Search Logs — Query syslog Parquet data for agents.

Agents use this to answer questions like:
  - "What errors occurred in the last hour?"
  - "Which devices sent the most syslog messages today?"
  - "Were there any critical events on R1 yesterday?"

Data source: .olav/databases/syslogs/**/*.parquet
Written by: src/olav/services/syslog_receiver.py

OW-046: metadata first.  The time window, severity and host narrow the complete
candidate set (counted from metadata only), then the message match is counted
over that whole set and one page is returned.  The header reports the complete
match count and the page range, so a short table is never mistaken for "only
these records".  ``page`` selects further pages; ``limit`` remains the page size.

This is the platform core script, so it stays self-contained: it does not import
``olav_netops``.  Missing files, permission failures, SQL/budget failures and a
genuine zero match are reported differently.
"""

import logging
import sys
from pathlib import Path


def _find_project_root():
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


sys.path.insert(0, str(_find_project_root() / "src"))

_logger = logging.getLogger(__name__)

_SEVERITY_LEVELS = {
    "emergency", "alert", "critical", "error",
    "warning", "notice", "info", "debug",
}

# ARCH-16 (Round 41) — hard ceiling that even the large tier respects;
# prevents a buggy ``limit=10000`` caller from filling the model context.
_SEARCH_LOGS_HARD_MAX = 500
_SEARCH_LOGS_FALLBACK = 50  # when tier config unavailable

#: Metadata candidate sets above this size still get an exact count; the count
#: is the complete full-scan fallback, not a sample.  It exists so the answer
#: can say which path ran.
_SEARCH_LOGS_FULL_SCAN_THRESHOLD = 20_000

#: Required syslog Parquet columns.  Validated per file (footer only) before any
#: query references them: ``union_by_name=true`` would NULL-fill a file that
#: lacks one, and its rows would then silently match nothing.
_SEARCH_LOGS_REQUIRED_COLUMNS = (
    "timestamp", "host", "severity", "facility", "message",
)

#: Bounded message excerpt computed by SQL around the match.  The full message
#: never leaves DuckDB; its char length and full-message digest still do, so a
#: short page is distinguishable from a complete one.
_MESSAGE_EXCERPT_LEAD = 120
_MESSAGE_EXCERPT_CHARS = 400
_MESSAGE_FALLBACK_CHARS = 240


def _resolve_search_limit(explicit: int | None) -> int:
    """Resolve ``limit`` for search_logs respecting tier defaults.

    Explicit value wins (clamped to 1..``_SEARCH_LOGS_HARD_MAX``).
    ``None`` → ``tier_default(tier, "search_logs_default_limit")`` —
    small=20 / medium=50 / large=100. Fallback when config unavailable
    keeps the pre-Round-41 default of 50.
    """
    if explicit is not None:
        return max(1, min(int(explicit), _SEARCH_LOGS_HARD_MAX))
    try:
        from olav.core.config import get_llm_config, tier_default
        tier = get_llm_config().model_tier
        val = tier_default(tier, "search_logs_default_limit", _SEARCH_LOGS_FALLBACK)
        return max(1, min(int(val), _SEARCH_LOGS_HARD_MAX))
    except Exception:  # noqa: BLE001
        return _SEARCH_LOGS_FALLBACK


def _get_log_dir() -> Path:
    """Resolve the syslog Parquet log directory."""
    try:
        from olav.core.config import DATABASES_DIR
        return Path(DATABASES_DIR) / "syslogs"
    except Exception:
        return Path(".olav/databases/syslogs")


def _classify_failure(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, PermissionError) or "permission denied" in text or "access denied" in text:
        return "permission"
    if "out of memory" in text or "interrupted" in text or "timeout" in text or "timed out" in text:
        return "budget"
    # A missing column inside a present file is schema drift, not a missing
    # store: DuckDB says "not found in FROM clause" for both, so schema signals
    # must be checked before the generic not-found branch.
    if any(token in text for token in (
        "binder error", "parser error", "catalog error", "conversion error",
        "invalid input", "referenced column", "not found in from clause",
        "column with name", "does not have a column", "table with name",
        "schema mismatch", "schema of",
    )):
        return "schema"
    if "no files found" in text or "no such file" in text or "does not exist" in text:
        return "missing"
    if "not found" in text:
        return "missing"
    return "sql"


def search_logs(
    query: str,
    hours: int = 24,
    severity: str | None = None,
    host: str | None = None,
    limit: int | None = None,
    page: int = 1,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Search syslog records stored as Parquet files.

    Args:
        query: Keyword to search in the message field (case-insensitive). Use "" for no filter.
        hours: How many hours back to look (default 24).
        severity: Filter by syslog severity level: emergency/alert/critical/error/warning/notice/info/debug.
        host: Filter by device hostname (partial match).
        limit: Page size. Omit for the model-tier default (small=20 /
               medium=50 / large=100 via
               ``TIER_DEFAULTS.search_logs_default_limit``, ARCH-16). Hard
               ceiling is 500 regardless of tier.
        page: 1-based page over the complete matching set.
        date_from: Optional inclusive start date (YYYY-MM-DD) that replaces
               the ``hours`` window.
        date_to: Optional inclusive end date (YYYY-MM-DD).

    Returns:
        Formatted table of matching syslog entries, newest first, with the
        complete match count and the page range shown.  Rows that share a
        timestamp keep one stable order across pages (host, severity, facility,
        message digest), so paging never repeats or skips a record.  Returns
        "No log records found." when the store is missing or nothing matches;
        missing files and zero matches are distinguished by the suffix.
    """
    limit = _resolve_search_limit(limit)
    try:
        page = int(page)
    except (TypeError, ValueError):
        return "Invalid page: page must be an integer >= 1."
    if page < 1:
        return "Invalid page: page must be an integer >= 1."
    offset = (page - 1) * limit
    if date_from is not None or date_to is not None:
        try:
            from datetime import date as _date
            if date_from is not None:
                date_from = _date.fromisoformat(str(date_from)).isoformat()
            if date_to is not None:
                date_to = _date.fromisoformat(str(date_to)).isoformat()
        except ValueError:
            return "Invalid date: date_from/date_to must be YYYY-MM-DD."
        if date_from and date_to and date_from > date_to:
            return "Invalid date range: date_from must not be after date_to."
        hours = 0  # explicit dates replace the relative window

    import duckdb

    log_dir = _get_log_dir()
    if not log_dir.exists():
        return "No log records found. (log directory does not exist)"

    # R100/S5 (2026-04-29 demo7 Ch10 v3): strip leading/trailing literal
    # quotes that small models bake into short string-typed args via
    # their JSON-construction heuristic.  qwen3.6:27b empirically passes
    # severity='"warning"' instead of severity='warning' for OpenAI tool
    # calls, breaking the _SEVERITY_LEVELS enum membership check.  Same
    # defensive coercion as format_and_export (see R100/S2 commit
    # c2ce4f2 _strip_quote_leak).
    def _strip_quotes(s):
        if not isinstance(s, str):
            return s
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            s = s[1:-1].strip()
        return s

    severity = _strip_quotes(severity)
    host = _strip_quotes(host)
    query = _strip_quotes(query)

    # Glob all parquet files recursively
    try:
        parquet_files = list(log_dir.rglob("*.parquet"))
    except Exception as exc:  # noqa: BLE001
        if _classify_failure(exc) == "permission":
            return f"Permission denied reading syslog store: {exc}"
        _logger.warning("search_logs log directory error: %s", exc)
        return f"Error querying logs: {_classify_failure(exc)}: {exc}"
    if not parquet_files:
        return "No log records found. (no Parquet files in log directory)"

    parquet_glob = str(log_dir / "**/*.parquet")
    source = "read_parquet(?, union_by_name=true)"

    meta_where: list[str] = []
    meta_params: list = []
    if date_from:
        meta_where.append("TRY_CAST(timestamp AS TIMESTAMP) >= CAST(? AS DATE)")
        meta_params.append(date_from)
    if date_to:
        meta_where.append("TRY_CAST(timestamp AS TIMESTAMP) < CAST(? AS DATE) + INTERVAL 1 DAY")
        meta_params.append(date_to)
    if not (date_from or date_to):
        meta_where.append(
            "TRY_CAST(timestamp AS TIMESTAMP) >= CAST(now() AS TIMESTAMP) - INTERVAL "
            f"'{int(hours)} hours'"
        )

    if severity:
        sev = severity.lower()
        if sev in _SEVERITY_LEVELS:
            # R100/S5 (2026-04-29): case-insensitive match — Parquet
            # stores "CRITICAL" (uppercase from syslog_receiver), but
            # _SEVERITY_LEVELS is lowercased.  Compare in lowercase so
            # both forms match.
            meta_where.append("LOWER(severity) = ?")
            meta_params.append(sev)
        else:
            return (
                f"Invalid severity '{severity}'. "
                f"Valid values: {', '.join(sorted(_SEVERITY_LEVELS))}"
            )

    if host:
        meta_where.append("host ILIKE ?")
        meta_params.append(f"%{host}%")

    def _where(parts: list[str]) -> str:
        return (" WHERE " + " AND ".join(parts)) if parts else ""

    body_where = ["message ILIKE ?"] if query else []
    body_params = [f"%{query}%"] if query else []

    clock_skew_hint = ""
    path = "metadata_only"
    try:
        con = duckdb.connect(":memory:")
    except Exception as exc:  # noqa: BLE001
        return f"Error querying logs: {_classify_failure(exc)}: {exc}"
    try:
        # Validate every Parquet footer before a predicate references a
        # column.  ``union_by_name=true`` would NULL-fill a file lacking
        # ``message`` and its rows would silently match nothing.  Footer reads
        # do no row scan, and they keep a missing store, a permission failure
        # and schema drift distinct from a genuine zero match.
        try:
            described = con.execute(
                "SELECT file_name, name FROM parquet_schema(?) WHERE type IS NOT NULL",
                [parquet_glob],
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            failure = _classify_failure(exc)
            if failure == "permission":
                return f"Permission denied reading syslog store: {exc}"
            if failure == "missing":
                return "No log records found. (Parquet store unreadable or not found)"
            if failure == "budget":
                return (
                    "Log query budget exceeded: reduce hours/host/severity scope or "
                    f"page size, then retry. ({exc})"
                )
            if failure == "schema":
                return f"Log store schema drift: {exc}"
            _logger.warning("search_logs schema check error: %s", exc)
            return f"Error querying logs: {exc}"
        files: dict[str, set[str]] = {}
        for file_name, name in described:
            files.setdefault(str(file_name), set()).add(str(name).lower())
        if not files:
            return "No log records found. (no Parquet files in log directory)"
        required = set(_SEARCH_LOGS_REQUIRED_COLUMNS)
        incomplete = {
            file_name: sorted(required - columns)
            for file_name, columns in sorted(files.items())
            if required - columns
        }
        if incomplete:
            shown = [
                f"{Path(file_name).name} missing {', '.join(columns)}"
                for file_name, columns in list(incomplete.items())[:10]
            ]
            more = "" if len(incomplete) <= 10 else f"; +{len(incomplete) - 10} more file(s)"
            return (
                f"Log store schema drift: {len(incomplete)} of {len(files)} Parquet "
                f"file(s) lack required column(s) ({'; '.join(shown)}{more}). "
                "Refusing the union query: NULL-filled rows would silently match nothing."
            )

        candidate_total = con.execute(
            f"SELECT count(*) FROM {source}" + _where(meta_where),
            [parquet_glob] + meta_params,
        ).fetchone()[0]
        path = "full_scan" if candidate_total > _SEARCH_LOGS_FULL_SCAN_THRESHOLD else "candidate_body"
        total = con.execute(
            f"SELECT count(*) FROM {source}" + _where(body_where + meta_where),
            [parquet_glob] + body_params + meta_params,
        ).fetchone()[0]
        # ISSUE-CH10-SYSLOG-CLOCK-SKEW-1H-WINDOW (P3, 2026-05-12):
        # When the result set is empty but Parquet rows exist within the
        # file footprint, the user's host clock is probably skewed vs the
        # syslog receiver's clock. Auto-fall-back to a wider 24h window —
        # and recount the complete set instead of returning a short page.
        if total == 0 and not (date_from or date_to) and hours < 24:
            wide_where = [
                w.replace(f"'{int(hours)} hours'", "'24 hours'") for w in meta_where
            ]
            wide_total = con.execute(
                f"SELECT count(*) FROM {source}" + _where(body_where + wide_where),
                [parquet_glob] + body_params + meta_params,
            ).fetchone()[0]
            if wide_total:
                total = wide_total
                meta_where = wide_where
                hours = 24  # report the widened window in the header
                clock_skew_hint = (
                    f"\n\n⚠️ Clock-skew fallback: original window returned 0 "
                    f"rows but the 24h window has {wide_total}. Host clock may "
                    f"be off vs ingestion clock — check NTP."
                )
        if not total:
            return "No log records found matching the criteria."

        # The excerpt, the full-length marker and the full-message digest are
        # all computed inside DuckDB from the same row.  Only the bounded
        # excerpt crosses the client fetch; the digest still identifies the
        # complete message and does not use the excerpt as a match criterion.
        if query:
            hit_select = (
                "timestamp, host, severity, facility, message, "
                "strpos(lower(message), lower(?)) AS mp, "
                "md5(coalesce(message, '')) AS content_hash, "
                "length(message) AS message_chars "
            )
            hit_params: list = [query]
        else:
            hit_select = (
                "timestamp, host, severity, facility, message, 0 AS mp, "
                "md5(coalesce(message, '')) AS content_hash, "
                "length(message) AS message_chars "
            )
            hit_params = []
        excerpt_sql = (
            "CASE WHEN mp > 0 THEN substring("
            f"message, GREATEST(1, mp - {_MESSAGE_EXCERPT_LEAD}), {_MESSAGE_EXCERPT_CHARS}) "
            f"ELSE substring(message, 1, {_MESSAGE_FALLBACK_CHARS}) END"
        )
        truncated_sql = (
            "CASE WHEN mp > 0 THEN (GREATEST(1, mp - " + str(_MESSAGE_EXCERPT_LEAD) + ") > 1 OR "
            f"(GREATEST(1, mp - {_MESSAGE_EXCERPT_LEAD}) + {_MESSAGE_EXCERPT_CHARS} - 1) "
            "< message_chars) "
            f"ELSE ({_MESSAGE_FALLBACK_CHARS} < message_chars) END"
        )
        rows = con.execute(
            "WITH hits AS (SELECT " + hit_select + f"FROM {source}"
            + _where(body_where + meta_where) + ") "
            "SELECT timestamp, host, severity, facility, "
            f"{excerpt_sql} AS message, {truncated_sql} AS excerpt_truncated, "
            "message_chars, content_hash "
            # OW-046: rows share a timestamp to the second, so timestamp alone
            # lets a row cross a page boundary.  The full-message digest keeps
            # the order (and paging) stable without fetching the body.
            "FROM hits ORDER BY timestamp DESC NULLS LAST, host NULLS LAST, "
            "severity NULLS LAST, facility NULLS LAST, content_hash NULLS LAST "
            "LIMIT ? OFFSET ?",
            # Placeholder order follows the SQL text: the CTE select list comes
            # before its FROM/WHERE, so the match position binds first.
            hit_params + [parquet_glob] + body_params + meta_params + [limit, offset],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        failure = _classify_failure(exc)
        if failure == "missing":
            return "No log records found. (Parquet store unreadable or not found)"
        if failure == "permission":
            return f"Permission denied reading syslog store: {exc}"
        if failure == "budget":
            return (
                "Log query budget exceeded: reduce hours/host/severity scope or "
                f"page size, then retry. ({exc})"
            )
        if failure == "schema":
            return f"Log store schema drift: {exc}"
        _logger.warning("search_logs query error: %s", exc)
        return f"Error querying logs: {exc}"
    finally:
        con.close()

    lines = [f"{'TIMESTAMP':<24} {'HOST':<16} {'SEV':<10} {'FACILITY':<10} MESSAGE"]
    lines.append("-" * 100)
    excerpt_rows = 0
    for ts, h, sev, fac, msg, msg_truncated, msg_chars, msg_hash in rows:
        ts_str = str(ts)[:23] if ts else "-"
        h_str = (str(h) or "-")[:14]
        sev_str = (str(sev) or "-")[:8]
        fac_str = (str(fac) or "-")[:8]
        msg_str = str(msg) if msg is not None else ""
        if msg_truncated:
            # Keep the match visible (the excerpt is centered on it) and say
            # that the full body was not fetched, with its DB-side digest.
            excerpt_rows += 1
            msg_str = (
                f"{msg_str} … [bounded excerpt; full_chars={msg_chars}; "
                f"full_md5={msg_hash}]"
            )
        lines.append(f"{ts_str:<24} {h_str:<16} {sev_str:<10} {fac_str:<10} {msg_str}")

    shown_from = offset + 1 if rows else 0
    shown_to = offset + len(rows)
    header = f"Found {total} syslog records (last {hours}h"
    if severity:
        header += f", severity={severity}"
    if host:
        header += f", host={host}"
    if query:
        header += f", query='{query}'"
    header += ")"
    header += (
        f"; showing {shown_from}-{shown_to} of {total} (page {page}, "
        f"limit {limit}, {'more pages' if shown_to < total else 'no more records'}"
        f"{', path=' + path if query else ''}):\n"
    )
    if shown_from == 0:
        header += f"(page {page} is past the end of {total} matches)\n"

    note = ""
    if excerpt_rows:
        note = (
            f"Note: {excerpt_rows} message(s) are bounded excerpts around the match; "
            "the full body was not fetched. Each row shows its full char length "
            "and full-message md5.\n"
        )
    return header + note + "\n".join(lines) + clock_skew_hint


if __name__ == "__main__":
    import json as _json, sys as _sys
    _args = _json.loads(_sys.stdin.read() or "{}")
    result = search_logs(**_args)
    print(_json.dumps(result, default=str))
