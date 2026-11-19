# Recorded configuration differences

The unreleased native candidate strengthens the existing `diff_configs` entry
point. Set `OLAV_HOME` to the authorized absolute native project, then pass the
exact stored device, command and two distinct snapshot IDs.

```sh
printf '%s' '{"device":"edge-1","command":"show running-config","snapshot_id_1":"before","snapshot_id_2":"after"}' | olav-skills run netops diff_configs
```

A side uses the historical `netops.ingest_sources` ledger when it has that
exact SID. The selected record must match the device, command, provenance
identity and raw SHA-256. When the SID has no ledger history, the tool can use
an exact surviving `netops.raw_output_store` row. That coverage is partial.
A missing or overwritten record, duplicate exact match, null body, failed
integrity check or resource limit returns an error and unknown difference.
Require the exact command on both sides, including case and spacing.

Read `sources.baseline` and `sources.target` for actual labels, raw hashes,
source type and collected time. If time is unknown, describe the result
as a comparison of the two recorded labels in the requested order. Date-like
IDs, ingestion timestamps and file times supply no device collection time.

Use `changed_blocks` to discuss changes observed under interface, router, ACL,
route-map and global headings. ACL and route-map order is preserved in the
comparison. Grouping uses Cisco-style text structure. The optional `sections`
array selects a group kind or a full-token heading prefix. Inspect
`diff_status`, exact comparison counts, `preview.truncated` and
`blocks_truncated` before citing the result. `full: true` raises the preview
to at most 400 lines within the same 24 KiB limit. The native Linux reader
uses a private read-only copy of the locked and bounded source. The result
describes recorded configuration text and requires explicit host permission
to read it.
