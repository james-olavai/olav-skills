# Recorded data and the local model

The native offline flow uses a local DuckDB database under the explicit `OLAV_HOME`, normally `.olav/databases/main.duckdb`. Import builds source tables and command-specific views. Their presence depends on the input and parsers; the same package version can produce different available views for different captures.

## From files to findings

| Layer | Typical sources | Meaning |
| --- | --- | --- |
| Bundle provenance | `netops.bundle_ingests` | Bundle identity, hash, import and supplied collection metadata |
| Inventory | `netops.devices` | Known device records and metadata; not automatically historical membership |
| Captured text | `netops.raw_output_store` | Recorded command output, including output without a structured parser |
| Parsed records | `netops.parsed_outputs` | Structured parser output; coverage can be smaller than raw inventory |
| Derived relationships | `netops.topology_links`, command `v_*_auto` views | Observations derived from available inputs |

The public two-host example creates 15 netops tables/views in 0.1.1, including `v_l2_links_auto`, `v_show_ip_bgp_summary_auto`, `v_show_ip_interface_brief_auto` and `v_snapshots_auto`. That is an example result, not a required schema for every database. It has no OSPF source view. Use `describe_table` or the relevant `query_batch` context before writing SQL; do not paste legacy DDL into a new analysis.

## Inventory, membership and roles

An imported but unparsed device still belongs to inventory. A structured protocol table can omit it. `snapshot_summary` distinguishes `inventory_device_count` from `observed_device_count`; command and record counts have different units. Explicit-snapshot device queries can restrict inventory to observed members while unfiltered inventory is a different scope. Preserve those labels.

Check exact membership when a set matters. Two equal counts do not prove the same devices were returned. Null role means unknown: hostname, vendor, ASN, loopback and platform alone do not establish a core/edge role or device class. In the synthetic fixture both device roles are unknown.

## Time has provenance

`query_batch` returns a `temporal` block with separate collection, ingestion, record-update and event clocks. Each clock has value, precision, source and status; windows can add range bounds. A timestamp without timezone must not silently become UTC. A date-shaped snapshot label is an inference, and an auto-generated `snap_*` ID does not prove capture time. CLI event time may remain unknown.

The bounded batch selector resolves `latest` using collection provenance and can refuse with `ambiguous_snapshot_time`. Some other scripts retain their own selection logic: notably `query_topology` prefers an available L2 snapshot and orders IDs. Always pass the real imported snapshot ID to relationship, diagram and comparison operations; never assume all defaults select the same capture.

## Interpreting absence and bounds

Missing source views, successful empty matches, parse gaps and missing timestamps are different outcomes. `absent_layers` on a topology result exposes unavailable protocol sources. Pagination and clipped cells limit what is visible even when aggregate counts are exact. See [script results](reference/scripts.md) and [inventory workflow](workflows/inventory.md).

Keep the original bundle read-only where practical and retain the import/parse report with findings. Historical `.olav` layout continuity does not guarantee every historical database is compatible; [validate an authorized copy](migration.md) before moving an existing consumer.
