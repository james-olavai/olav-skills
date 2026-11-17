"""Single-writer keyed writes for catalogs without enforced unique indexes.

The caller owns the transaction and warehouse serialization. These checks are
not a cross-process uniqueness constraint; concurrent writers need a host gate.
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass


class IngestKeyError(ValueError):
    """Invalid, ambiguous or conflicting business key; no payload is disclosed."""


def quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("invalid storage identifier")
    return '"' + value + '"'


def current_backend(conn) -> str:
    row = conn.execute(
        "SELECT type FROM duckdb_databases() WHERE database_name=current_database()"
    ).fetchone()
    if row is None or row[0] not in ("duckdb", "ducklake"):
        raise ValueError("unsupported ingest catalog")
    return row[0]


def is_ducklake(conn) -> bool:
    return current_backend(conn) == "ducklake"


@dataclass(frozen=True)
class WriteCounts:
    inserted: int = 0
    updated: int = 0
    deduplicated: int = 0


def _canonical(value, is_json):
    if is_json and value is not None:
        if isinstance(value, str):
            value = json.loads(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return value


_temp_names = itertools.count()
# ponytail: one ingest batch is ~10k source rows (a single week of ~282
# devices), so TEMP-relation joins and multi-row VALUES stay comfortably
# inside DuckDB's in-memory and parameter limits when chunked below.
_INSERT_CHUNK = 500


def _temp_table(purpose):
    return f"_kwr_{purpose}_{next(_temp_names)}"


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _write_inserts(conn, relation, inserts):
    """One (chunked) multi-row INSERT per distinct column signature."""
    if not inserts:
        return
    groups = {}
    for row in inserts:
        groups.setdefault(tuple(row), []).append(row)
    for columns, group in groups.items():
        columns = list(columns)
        column_sql = ",".join(map(quote_identifier, columns))
        placeholders = ",".join("?" for _ in columns)
        for chunk in _chunks(group, _INSERT_CHUNK):
            rows_sql = ",".join("(" + placeholders + ")" for _ in chunk)
            params = [row[c] for row in chunk for c in columns]
            conn.execute(
                f"INSERT INTO {relation} ({column_sql}) VALUES {rows_sql}", params
            )


def _write_updates(conn, relation, updates, key_columns):
    """One UPDATE ... FROM per distinct value-column signature.

    Source values are staged in a typed TEMP table so a single set-based
    UPDATE replaces one point UPDATE (and its full-table scan) per source key.
    Key columns are staged as VARCHAR because write_keyed_rows already
    validates every key as a non-blank string; the join casts to the target
    key type exactly like the former ``WHERE key = ?`` parameter binding did.
    Value columns use the target's own DESCRIBE types so NULL / timestamp /
    JSON values bind identically to the old per-row UPDATE.
    """
    if not updates:
        return
    groups = {}
    for values, key in updates:
        groups.setdefault(tuple(values), []).append((values, key))
    column_types = {r[0]: r[1] for r in conn.execute(f"DESCRIBE {relation}").fetchall()}
    for update_columns, group in groups.items():
        update_columns = list(update_columns)
        if not update_columns:
            continue
        tmp = _temp_table("upd")
        key_ddl = [f"_k{i} VARCHAR" for i in range(len(key_columns))]
        value_ddl = [f"_v{i} {column_types[c]}" for i, c in enumerate(update_columns)]
        conn.execute(f"CREATE TEMP TABLE {tmp} ({', '.join(key_ddl + value_ddl)})")
        try:
            width = len(key_columns) + len(update_columns)
            placeholders = ",".join("?" for _ in range(width))
            for chunk in _chunks(group, _INSERT_CHUNK):
                rows_sql = ",".join("(" + placeholders + ")" for _ in chunk)
                params = [
                    v
                    for values, key in chunk
                    for v in list(key) + [values[c] for c in update_columns]
                ]
                conn.execute(f"INSERT INTO {tmp} VALUES {rows_sql}", params)
            set_sql = ",".join(
                f"{quote_identifier(c)} = {tmp}._v{i}"
                for i, c in enumerate(update_columns)
            )
            where_sql = " AND ".join(
                f"{quote_identifier(c)} = {tmp}._k{i}"
                for i, c in enumerate(key_columns)
            )
            conn.execute(
                f"UPDATE {relation} SET {set_sql} FROM {tmp} WHERE {where_sql}"
            )
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {tmp}")


def write_keyed_rows(conn, *, table, rows, key_columns, mode,
                     payload_columns, update_columns=(), preserve_columns=(),
                     coalesce_columns=(), json_columns=(), snapshot_column=None,
                     dry_run=False):
    """Preflight a complete batch, then insert/update one row per full key.

    ``immutable`` rejects changed payload; ``current`` replaces only declared
    fields. ``snapshot_column`` additionally rejects changed same-snapshot
    payload (raw current-state). NULL and blank keys are rejected, including
    existing rows. Volatile clocks must be excluded from payload_columns.
    """
    if mode not in ("immutable", "current") or not key_columns:
        raise ValueError("invalid keyed write policy")
    relation = ".".join(quote_identifier(part) for part in table.split("."))
    source = {}
    duplicates = 0
    for original in rows:
        row = {k: _canonical(v, k in json_columns) for k, v in original.items()}
        for column in row:
            quote_identifier(column)
        key = tuple(row.get(c) for c in key_columns)
        if any(not isinstance(v, str) or not v.strip() for v in key):
            raise IngestKeyError("invalid source key")
        payload = tuple(row.get(c) for c in payload_columns)
        if key in source:
            previous = source[key]
            if payload != tuple(previous.get(c) for c in payload_columns):
                raise IngestKeyError("source key payload conflict")
            if snapshot_column and row.get(snapshot_column) != previous.get(snapshot_column):
                raise IngestKeyError("ambiguous source snapshot")
            duplicates += 1
        else:
            source[key] = row
    # Reject dirty targets explicitly rather than silently repairing historical
    # data or treating NULL as a new independent key on every retry.
    keys_sql = ",".join(quote_identifier(c) for c in key_columns)
    invalid = " OR ".join(
        f"{quote_identifier(c)} IS NULL OR trim(cast({quote_identifier(c)} AS VARCHAR))=''"
        for c in key_columns
    )
    if conn.execute(f"SELECT 1 FROM {relation} WHERE {invalid} LIMIT 1").fetchone():
        raise IngestKeyError("invalid existing key")
    if conn.execute(f"SELECT 1 FROM {relation} GROUP BY {keys_sql} HAVING count(*)>1 LIMIT 1").fetchone():
        raise IngestKeyError("duplicate existing key")
    if not source:
        return WriteCounts(0, 0, 0)

    # Batch preflight: stage the source key set as a TEMP relation and join it
    # against the target exactly once. This replaces one ``SELECT *`` per
    # source key and only fetches the columns the policy compares (payload,
    # snapshot, coalesce fallbacks) — never the full row.
    probe_columns = list(dict.fromkeys(
        list(payload_columns)
        + ([snapshot_column] if snapshot_column else [])
        + list(coalesce_columns)
    ))
    key_table = _temp_table("keys")
    key_ddl = ",".join(quote_identifier(c) + " VARCHAR" for c in key_columns)
    conn.execute(f"CREATE TEMP TABLE {key_table} ({key_ddl})")
    try:
        placeholders = ",".join("?" for _ in key_columns)
        for chunk in _chunks(list(source), _INSERT_CHUNK):
            rows_sql = ",".join("(" + placeholders + ")" for _ in chunk)
            params = [v for key in chunk for v in key]
            conn.execute(f"INSERT INTO {key_table} VALUES {rows_sql}", params)
        old_by_key = {}
        src_cols = ",".join(f"{key_table}.{quote_identifier(c)}" for c in key_columns)
        tgt_cols = ",".join(f"{relation}.{quote_identifier(c)}" for c in probe_columns)
        select_cols = ",".join(filter(None, [src_cols, tgt_cols]))
        join_on = " AND ".join(
            f"{key_table}.{quote_identifier(c)} = {relation}.{quote_identifier(c)}"
            for c in key_columns
        )
        for found in conn.execute(
            f"SELECT {select_cols} FROM {key_table} JOIN {relation} ON {join_on}"
        ).fetchall():
            found_key = tuple(found[:len(key_columns)])
            old = {
                c: _canonical(found[len(key_columns) + i], c in json_columns)
                for i, c in enumerate(probe_columns)
            }
            old_by_key[found_key] = old
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {key_table}")

    inserted = updated = 0
    inserts = []
    updates = []
    for key in sorted(source, key=lambda k: json.dumps(k, ensure_ascii=False)):
        row = source[key]
        old = old_by_key.get(key)
        if old is None:
            inserts.append(row)
            inserted += 1
            continue
        effective = dict(row)
        for c in coalesce_columns:
            if effective.get(c) is None:
                effective[c] = old.get(c)
        same = all(old.get(c) == effective.get(c) for c in payload_columns)
        if same and (not snapshot_column or old.get(snapshot_column) == row.get(snapshot_column)):
            duplicates += 1
            continue
        if mode == "immutable" or (snapshot_column and old.get(snapshot_column) == row.get(snapshot_column)):
            raise IngestKeyError("existing key payload conflict")
        values = {c: row[c] for c in update_columns if c in row and c not in preserve_columns}
        for c in coalesce_columns:
            if c in values and values[c] is None:
                values[c] = old.get(c)
        updates.append((values, key))
        updated += 1

    if not dry_run:
        _write_inserts(conn, relation, inserts)
        _write_updates(conn, relation, updates, key_columns)
    return WriteCounts(inserted, updated, duplicates)
