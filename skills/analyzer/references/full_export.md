# Full export of a fixed recorded scope

Use the unreleased `export_query` script when the user explicitly asks for all
stored rows in a supported exact snapshot scope and authorizes a project output.
Use `execute_sql` for ad hoc bounded analysis. Select the candidate's bound Python
on Linux. Set `OLAV_HOME` to the authorized absolute project and supply that same
path in the request.

```json
{"project":"/absolute/project","export_id":"records-s1","source":"parsed_outputs","snapshot_id":"s1","columns":["device_name","command","parsed_data"],"format":"jsonl","max_rows":1000}
```

Dispatch with `olav-skills run analyzer export_query`. Source
`parsed_outputs` defaults to the columns shown and also allows `snapshot_id`,
`platform` and `ingested_at`. Source `topology_links` defaults to
`source_device`, `source_interface`, `destination_device` and
`destination_interface`. Its additional columns are `snapshot_id`, `link_id`,
`discovery_protocol`, `link_type`, `link_status`, `link_speed`, `first_seen`,
`last_seen`, `last_verified`, `status_changes` and `platform`. Optional
`filters` match supported VARCHAR columns exactly, excluding `snapshot_id`.
The script refuses arbitrary SQL, views, `latest` and external reads.

Output is saved under `.olav/exports/<export_id>/`. Read the verified manifest
and its ordered parts. JSONL represents non-null scalars as strings and NULL
as JSON null. CSV repeats the header per part, uses empty fields for NULL, and
protects formula-like text. The manifest gives data version, source types,
query scope, file counts, bytes and hashes.

For `partial`, preserve the returned receipt separately and resubmit the same
scope with `receipt`. Change only the per-attempt budgets if needed. Report a
known total only after `complete`. A failed or interrupted attempt cannot prove
delivery. An empty result or completed export establishes only the rows in the
selected table and projection, within the exact SID and filters. Preserve
uncertainty about capture completeness and real network state.

Source-copy and query run only in a dedicated exec reader. The parent owns temporary storage and removes copies after local deadline cancellation. Success manifest/receipt publication occurs only after final source validation exits successfully. A direct proc-file-descriptor pathname is not an actual DuckDB open guarantee. Existing receipts from a changed execution runtime require a new export ID.
