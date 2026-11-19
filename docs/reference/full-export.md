# Export recorded rows

Use `olav-skills run analyzer export_query` in 0.2 on 64-bit Linux with OFD
locks available. The
process needs read access to an explicitly authorized native project and write
access to its `.olav/exports` directory. Set `OLAV_HOME` to the same absolute
project path before invoking the script.

## Select the scope

```sh
export OLAV_HOME=/absolute/authorized/project
printf '%s' '{"project":"/absolute/authorized/project","export_id":"parsed-s1","source":"parsed_outputs","snapshot_id":"s1","columns":["device_name","command","parsed_data"],"filters":{"platform":"ios"},"format":"jsonl","max_rows":1000}' |
  olav-skills run analyzer export_query
```

Required keys are `project`, `export_id`, `source` and the exact
`snapshot_id`. The export ID can contain up to 64 ASCII letters, digits,
underscores or hyphens and begins with a letter or digit. The source is one of
two native physical tables.

| Source | Default columns | Other selectable columns |
| --- | --- | --- |
| `parsed_outputs` | `device_name`, `command`, `parsed_data` | `snapshot_id`, `platform`, `ingested_at` |
| `topology_links` | `source_device`, `source_interface`, `destination_device`, `destination_interface` | `snapshot_id`, `link_id`, `discovery_protocol`, `link_type`, `link_status`, `link_speed`, `first_seen`, `last_seen`, `last_verified`, `status_changes`, `platform` |

Use `columns` to select a unique ordered list from the table. Optional
`filters` map allowed VARCHAR columns other than `snapshot_id` to exact
case-sensitive strings. Historical groups follow the stored SID exactly. Supply
no `latest` alias, SQL, views, file sources or paths for output. A complete result
covers the selected table rows and projection for these filters in one database
version. A zero-row result establishes only that this query found no matching
stored rows. Collection completeness and network state require other evidence.

## Read or resume the export

The response gives a status, manifest path and receipt after successful
publication. Read `.olav/exports/<export_id>/manifest.json` for scope,
types, source SHA-256, runtime identity, ordered part names, and each part's
SHA-256, row count and actual byte count. The receipt is the SHA-256 of the
manifest bytes. Read only the ordered parts listed in the manifest.

- `complete` includes the exact `total_rows` after observing the end of the
  query. A CSV export includes a header in each part. For JSONL concatenate
  the parts in listed order. For CSV retain one header when combining parts.
- `partial` includes progress and an unknown `total_rows`. Keep its receipt
  independently of the output directory. Submit the same scope and ID with
  `"receipt":"<last returned receipt>"`; per-attempt budgets may change.
  Resume checks the receipt, request, source and runtime and independently
  verifies the existing bytes, hashes and record counts. Each new response
  replaces the old receipt.
- `failed` means the attempt delivered no new verified result. An initial
  query or data-budget failure can leave a failed manifest. A failed resume may
  preserve the previous checkpoint. A process/I/O interruption can leave some
  publication state uncertain. If the last trusted receipt no longer matches
  the checkpoint, start a new export ID. A receipt saved outside the output
  directory pins its bytes and provides no signature for rewritten artifacts.

JSONL is UTF-8 with one object per row. SQL NULL becomes JSON `null`, and
every non-null value uses DuckDB's VARCHAR representation, including numbers,
timestamps and textual JSON. The manifest lists the original DuckDB types in
projection order. CSV UTF-8 uses quoted fields, empty fields for NULL, and
prefixes potentially formula-like values with an apostrophe. Use JSONL when
the SQL NULL/empty string distinction or exact text matters.

## Resource and source limits

| Limit | Default | Hard maximum |
| --- | ---: | ---: |
| Rows per attempt | 1,000 | 10,000 |
| Part bytes, including CSV header | 4 MiB | 16 MiB |
| Attempt deadline after input | 15 s | 60 s |
| Serialized row | 512 KiB | 512 KiB |
| Database file | 1 GiB | 1 GiB |
| Total rows, bytes and parts | 1,000,000 / 256 MiB / 256 | 1,000,000 / 256 MiB / 256 |

Set per-attempt limits with `max_rows`, `max_bytes` and `timeout_ms`.
The worker uses a 128 MiB DuckDB memory budget, one engine thread, no spill or
external reads, and a 768 MiB process address-space ceiling. A query may scan
and sort more rows than it emits and can fail within those resource limits.
An oversized row or empty partial attempt fails without truncating any cell.

Use the checkpointed `.olav/databases/main.duckdb` without a WAL. The isolated worker locks the source with an OFD read lock, copies its verified descriptor into a parent-owned private scratch and queries that copy read-only. It verifies source/copy identities and the hash of actual copied bytes before publication. Symbolic links and linked source/output files
are refused. If the content or runtime changes, start a new export ID for the
new version.

Source-copy and query run only in a dedicated exec reader. The parent owns temporary storage and removes copies after local deadline cancellation. Success manifest/receipt publication occurs only after final source validation exits successfully. A direct proc-file-descriptor pathname is not an actual DuckDB open guarantee. Existing receipts from a changed execution runtime require a new export ID.
