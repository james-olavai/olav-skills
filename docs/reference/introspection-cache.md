# Native introspection cache

Native ingest finalisation in the unpublished 0.2.0 candidate refreshes two
DuckDB tables for schema and value discovery.

| Table | Contents |
| --- | --- |
| `netops.value_profile` | One row per selected view, text column and non-NULL value, with its count, fingerprint and cluster ID. |
| `netops.introspection_cache` | One row with the device inventory projection, typed view columns, state-like value distributions and `refreshed_at`. |

## Scope

The cache covers the snapshot and L2 projections and the per-command
auto-views successfully rebuilt in the current native finalisation. A command
without supported parser output does not enter the refresh. A previous view can
remain queryable after its command or eligible data disappears. The next
successful finalisation excludes it from the materialised directory and
profile. An empty source table can still produce a one-row cache for the empty
projections. A NULL aggregate means there were no eligible rows in that part
of the last successful refresh.

The profile counts non-NULL text values from columns with 1–30 distinct
values. It skips identity and dimension columns such as device, interface,
snapshot ID, and address. Repeated records contribute to frequency, including
multiple observations of the same entity. The JSON distribution includes the
subset of profiled columns whose names end in state-like terms such as
`status`, `state`, `proto` or `oper`. Cluster IDs rank fingerprints within
a view and column. Use them to discover spelling variants and verify the
domain semantics before merging values.

## Refresh and failure

Native finalisation invalidates the previous cache before refreshing views
and profiles. A failed required read or build leaves the directory
unavailable after an autocommit refresh attempt on a writable native
connection. Query the authorized sources when the directory is absent.
When an existing transaction owns the refresh, the caller owns commit or
rollback. DuckDB can abort that transaction on a SQL error. Rolling back
restores the previous source and cache state for work in that transaction.

Outside finalisation, changes to inputs do not trigger automatic refresh.
`refreshed_at` is a database refresh-transaction clock. Use recorded capture
provenance for collection time and explicit snapshot-bound queries for
historical facts. Auto-views can combine different snapshot labels across
devices and commands. Use a supported bounded query to establish membership,
counts, source coverage or observed health. This contract covers native
DuckDB and adds no host capability.
