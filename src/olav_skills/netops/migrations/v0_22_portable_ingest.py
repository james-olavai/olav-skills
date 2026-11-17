"""Migration v0.22: portable-snapshot-ingest schema additions (netops DB).

Backs the portable-snapshot-ingest design (``dev_docs/76``).

Adds:

  * ``netops.raw_output_store.bundle_id``       — uuid of the source bundle
  * ``netops.raw_output_store.bundle_sha256``   — content hash from bundle manifest
  * ``netops.raw_output_store.ingested_via``    — 'bundle' / 'rancid' / 'dump' / NULL (live SSH)
  * ``netops.bundle_ingests``                   — one row per ingest event
                                                  (full provenance / chain-of-custody)

Idempotent — safe to apply against fresh DBs and against DBs already at
v0.22+.  The matching ``RawOutputStoreTable.columns`` list in
``olav_skills.netops.core.tables`` carries the same three columns so fresh DBs
land at the right shape via the normal ``ensure_schema()`` path; this
migration covers the in-place evolution case for existing prod DBs.
"""
from __future__ import annotations


def apply_migration(conn) -> None:
    """Idempotently ALTER raw_output_store + CREATE bundle_ingests."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS netops")

    from olav_skills.netops.core.tables import RawOutputStoreTable
    RawOutputStoreTable().ensure_schema(conn)

    # ── raw_output_store: additive columns ──────────────────────────
    for col in ("bundle_id", "bundle_sha256", "ingested_via", "platform"):
        conn.execute(
            f"ALTER TABLE netops.raw_output_store "
            f"ADD COLUMN IF NOT EXISTS {col} VARCHAR"
        )

    # Only absence is optional; genuine ALTER/catalog errors must propagate.
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_catalog=current_database() "
        "AND table_schema='netops' AND table_name='parsed_outputs'"
    ).fetchone()
    if exists:
        conn.execute("ALTER TABLE netops.parsed_outputs ADD COLUMN IF NOT EXISTS platform VARCHAR")

    from olav_skills.netops.core.tables import BundleIngestsTable
    BundleIngestsTable().ensure_schema(conn)
    # Add a future INSERT default without rewriting historical NULL clocks.
    conn.execute("ALTER TABLE netops.bundle_ingests ALTER COLUMN ingested_at SET DEFAULT current_timestamp")
