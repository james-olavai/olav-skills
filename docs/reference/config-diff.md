# Recorded configuration differences

The local source candidate strengthens `diff_configs`. The published PyPI
`olav-skills==0.1.1` has the earlier entry point with different source and
snapshot behavior.

Set `OLAV_HOME` to the authorized absolute native project path.

```sh
printf '%s' '{"device":"edge-1","command":"show running-config","snapshot_id_1":"before","snapshot_id_2":"after"}' | olav-skills run netops diff_configs
```

Both calls select the same exact stored device and command. Supply two distinct
snapshot IDs with their original spelling. Missing IDs, `latest`, `current` and
`newest` fail explicitly. The input order sets the baseline and target.
Exact comparison of UTF-8 identity bytes preserves case, accents and internal
spacing regardless of database collation.

## Source and time

Each side selects one row from `netops.ingest_sources` if the exact SID appears
in that historical ledger. The row's raw text must match the identity and
SHA-256 stored in its provenance. An SID absent from the ledger can use a
surviving `netops.raw_output_store` row with the same exact SID, device and
command. Last-writer coverage is partial. Missing, ambiguous, null or
inconsistent evidence yields an error with `diff_status: unknown`.

The result reports actual labels, table, coverage, raw SHA-256 and collection
time per side. Ledger time uses stored capture provenance. Last-writer time
uses supported typed capture fields. Missing, invalid or conflicting capture
evidence remains unknown. Snapshot label, file time and ingest time do not
supply collection time. `database_version` records the fingerprint of the
native database copy used for the comparison.

## Comparison and output

The unified diff preserves the relative order of recorded lines and groups
observed edits under Cisco-style interfaces, routing processes, ACLs,
route maps and global text. Numbered ACL entries share a list key and route-map
sequences share a map key. Body order and repeated lines are retained. The
comparison omits only recognized Cisco preamble lines. In-configuration
descriptions, remarks and banner bodies remain in the comparison.

`sections` optionally selects up to 16 group kinds or case-insensitive heading
prefixes on whole-token boundaries. A selector absent from both captures
returns an error. `context_lines` accepts 0–10 and defaults to 3. Counts describe
line edits for the compared selection. `preview.truncated` and `blocks_truncated`
mark separate output limits. The counts remain exact for that selection when
the preview or group list is truncated.

The request cap is 16 KiB. Each body is limited to 256 KiB and 4,000 lines.
Ledger source metadata is capped at 64 KiB. Preview is capped at 40 lines by
default and 400 with `full: true`. Both modes share a 24 KiB JSON string cap.
The result is capped at 64 KiB. Added or removed lines record text changes in
these two snapshots. They do not certify devices' current running state.

The reader requires a 64-bit Linux host with OFD locks, a checkpointed
native DuckDB source up to 1 GiB and private scratch capacity for its copy.
It copies the verified source descriptor under a read lock, queries the
private copy with external access disabled, and removes the copy. The worker
has a ten-second deadline, one DuckDB thread, a 128 MiB DuckDB budget and a
768 MiB address-space cap. Errors return JSON and nonzero process status.
