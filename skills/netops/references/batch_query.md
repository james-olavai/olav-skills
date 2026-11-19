# Bounded netops query contract (v1)

Host binds `OLAV_HOME` to an authorized project root; database is `.olav/databases/main.duckdb`. No db_path, SQL, arbitrary table, authorization or project parameter is accepted. This is a host scope binding, not production per-user ACL enforcement. All data access is read-only with external access disabled and OW-043 resource configuration. Empty optional device/vendor/command lists discover within the bound scope.

Call `query_batch.py` with one JSON object. Common keys: operation, snapshot_id (default latest; resolved conservatively by collection provenance), devices, vendors, commands (exact case-sensitive strings; max 50 values of 256 chars), limit (integer 1..50, default 10). Use an actual snapshot ID; a bare YYYY-MM-DD ID is treated as an inferred archive date label, never a verified capture timestamp. Unknown or inapplicable keys are errors, never silently dropped. Row samples are SQL limited, cell values clipped at 256 characters, response <=12000 UTF-8 bytes; exact counts remain full-scope. has_more/byte_truncated expose incomplete samples. NULLS LAST is fixed.

| operation | scope / output |
|---|---|
| context | No data filters. Actual whitelisted source/column presence only, no sample/count answer data. |
| snapshot_summary | inventory_device_count, observed_device_count, distinct non-NULL command_count, record_count |
| device_coverage | vendor, device_count, observed_device_count; inventory vs captured membership distinguished |
| command_coverage | command, device_count, record_count |
| devices | hostname, ip_address, platform, vendor, model, role; inventory rows, not inferred historical membership |
| commands | device_name, command, record_count; captured-record filtering only, no command body; not the executable/safe command catalog (`search_commands`) |
| multi_device_summary | device_name, vendor, record_count, command_count, collection_status; zero matches explicit per requested device |
| top_n | group_by=device/command/vendor, metric=device_count/command_count/record_count; key plus all three counts; default metric descending |
| diff_summary | snapshot_id_1/source and snapshot_id_2/target required; diff_kind=parsed_keys(default)/command_keys/topology_keys/oc_keys. At most one device, no vendor/command filter. Identity-key set EXCEPT, added=target-source; bounded at most25 per direction and NULLS LAST. Not a semantic/content diff. |
| interface_health | Canonical IOS/Junos interface views only, IOS status/line proto, Junos admin/link; family proto is context. Known non-up => nonhealthy, both up => healthy, other values unknown; exact counts include unknown_count and ordered nonhealthy samples. Does not infer missing canonical records from base tables. |
| bgp_health | Only supported canonical summary views; numeric prefix count only in the prefix-count profile or known Established spellings => established; known nonestablished states => nonhealthy, unrecognized/missing states unknown. Native Junos peer_ip/state are supported. Not adjacency (use query_topology). OSPF health is unsupported. |

Metadata rows operations accept `fields` as a nonempty subset of the operation's output fields (empty chooses defaults), `order_by` from the same fields and `order=asc/desc`. Ordering adds remaining columns ascending as stable tie-breaks. Top N uses metric as order_by; supply metric and order, not conflicting order_by. Other operations reject fields/order_by. Protocol commands filters are rejected. Explicit missing devices are classified; zero matches is success and absent sources/invalid columns are typed source failures. Existing parsed/raw source presence never proves a command was collected in a particular snapshot.

Templates first. Unsupported intent stays unsupported; only then inspect relevant compact schema and use bounded read-only SQL. Do not force-map arbitrary questions or discard date/snapshot/vendor constraints. Reports reuse `query_evidence` for limited source fragments; this contract does not add body export. No topology/adjacency duplication.

Time contract: `temporal` returns `snapshot_id`, `collected_at`, `ingested_at`, `record_updated_at`, `event_time`, and `coverage`. Each clock has `value`, `precision`, `source`, `status`; complete multi-record windows add `range_start`/`range_end`. Missing and partial provenance is explicit. `updated_at` is not capture time; event time is unknown for CLI text. Diff carries `temporal.source` and `.target`. Unfiltered inventory has unknown time. Latest prioritizes authorized bundle collection metadata, then explicit raw/parsed collection fields, then only bare YYYY-MM-DD archive labels. Auto-generated snap_* IDs carry no collection guarantee. collector-dump midnight placeholders have date precision. Incomparable, overlapping, partial, unknown or conflicting candidates return `ambiguous_snapshot_time`; a sole candidate may retain unknown time. Explicit snapshots work without known time. Metadata uses the same connection and request deadline, bounded to 512 snapshots for latest; explicit selection does not scan all historical metadata. Restricted callers pass trusted `temporal_sources=()` to omit bundle metadata without granting new table access. Standalone stdin uses full bound-project access and accepts this optional restriction; not an authorization boundary. Pi host enforces allowlist and rejects this model argument. Arbitrary SQL time scope remains unknown.

Read [native contracts](native_contracts.md) for candidate parser coverage, state spellings, source profiles and comparison limitations.

## Preserve user scope and clock qualification

Resolve human capture labels from the authorized project's catalog or actual
metadata before choosing an exact SID. A recent record is not necessarily the
user's named record. If no mapping is available, ask rather than substitute
latest or silently drop the label. Inventory and observed counts are different.
Only include clocks needed to answer the question. `timestamp_without_timezone`
is a local wall-clock string with unknown timezone; never present it as an
exact instant or convert it to UTC without independent provenance. A known
field value/status does not remove this precision limitation.

## Native snapshot-summary count scopes

`result_contract.counts` labels each unchanged numeric count separately.
`inventory_device_count` is filtered current project inventory, independent of
the chosen snapshot and command filter. It is not the number captured then.
`observed_device_count` counts distinct parsed device names in the selected
snapshot that also match the filtered current inventory. Commands restrict this
parsed scope, not the inventory count. Uncatalogued parsed devices are excluded
by this operation's join, so it does not certify all collected devices.
Each field declares physical sources, applicable filters and whether a snapshot
applies. For a selected recorded-device count, report the observed measure and
its limits; do not substitute the project inventory number. These native
qualifications do not certify retained warehouse consumers.

Clock precision applies to the same clock and source only. An explicit Z or
offset in a catalog is not `timestamp_without_timezone`; a naive database clock
does not borrow that catalog's offset. Keep sources separate and omit unneeded
clocks rather than merging them into a more certain statement.
