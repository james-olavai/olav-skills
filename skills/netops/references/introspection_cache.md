# Native introspection cache

Version 0.2 refreshes `netops.value_profile` and
`netops.introspection_cache` as part of native DuckDB ingest finalisation.
Use these as discovery hints within the authorized project. Prefer a supported
bounded query, then verify any chosen fields and values against the intended
source and snapshot.

- `introspection_cache` holds one row after a successful refresh. `fleet` is
  the device inventory projection. `views` contains the names and typed fields
  of native auto-views rebuilt in that refresh. `value_distributions` contains
  the profiled values of state-like columns, with row frequencies and cluster
  IDs. A missing cache means discovery is unavailable. A NULL JSON aggregate
  means its eligible input rows were empty at refresh time.
- `value_profile` covers eligible text columns with 1–30 distinct non-NULL
  values. Identity and dimension columns such as device, snapshot, interface
  and address fields are skipped. Frequencies count repeated rows. Fingerprint
  clusters are local to one view and column and suggest spelling similarities.
- `refreshed_at` is a DuckDB refresh-transaction clock. Use recorded capture
  provenance for collection time. Source edits outside finalisation can make
  the materialised contents stale. Views can select per-device and
  per-command records from different snapshots, and old views can remain
  queryable after their commands disappear from this directory.
- On a failed native refresh the materialised directory is unavailable.
  Rolling back a caller-owned transaction restores its previous database
  state, including its previous cache. Continue with the authorized source
  and keep missing scope unknown.

Keep coverage, live health and snapshot membership grounded in the source
query and collected evidence. There is no new host tool or query permission.
