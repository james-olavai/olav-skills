# Exact topology observation differences (unreleased)

This source candidate is not in published 0.1.1. Run:

```sh
printf '%s' '{"snapshot_id_1":"before-id","snapshot_id_2":"after-id"}' | olav-skills run reporter compare_topology
```

Set an authorized absolute `OLAV_HOME` explicitly. Both IDs must be distinct,
exact stored IDs; `latest`, `current`, `newest` and `all` are refused. Identity
predicates use binary equality. No time order is inferred from IDs. Arguments
are only `snapshot_id_1`, `snapshot_id_2`, optional `source`, `limit` (1–50,
default 20) and `timeout_ms` (1–15000, default 15000). Unknown keys fail.

## Historical source (default)

`source="parsed_neighbors"` reads `netops.parsed_outputs` historical JSON
records, not latest-only views. Supported platform is canonical Cisco IOS;
exact commands are `show cdp neighbors detail` and `show lldp neighbors detail`.
Profiles use the stock parser's lowercase `neighbor_name`, `local_interface`
and CDP `neighbor_interface` or LLDP `neighbor_port_id`. LLDP port descriptions,
numeric port IDs, missing names and malformed interfaces are not endpoint
identities. No address-to-device mapping, hostname alias or domain stripping is
invented. Interface abbreviations are canonicalized by netutils. Both endpoints
include device and interface; swapping direction swaps the complete endpoints.
Parallel interface links remain distinct.

The same device/command parent scope must exist in both snapshots. Missing,
empty, invalid or unsupported parent sources leave the whole comparison
unknown. An empty parsed list does not establish collection with zero neighbors.
Duplicate parent rows or duplicate endpoint records within one parent are
conservatively unknown. Valid observations from different parent commands or
opposite devices aggregate into a sorted protocol set. These stock templates
carry no link status, speed or type: those fields remain null/unknown even when
CDP/LLDP neighbors are present. A recorded neighbor does not establish an `up`
physical link. Unsupported platforms (including Junos here) stay unknown.

## Explicit current-table comparison

`source="topology_links"` compares existing exact snapshot labels in the native
table. It is always partial, not a reconstruction of complete historical
snapshots: default native import updates matching `link_id` rows in place and
can overwrite an older snapshot label. If either selected side has no rows,
`counts` is null and the comparison is unknown, never an empty-set diff.

Required VARCHAR columns are snapshot_id and the four endpoint columns.
Optional VARCHAR content columns are discovery_protocol, link_status,
link_type and link_speed. Missing/null/unknown content stays unknown per field;
a known value against unknown never counts as a change. Duplicate endpoint
observations, including opposite-direction rows, conservatively make this
fallback unknown; it does not guess how contradictory statuses should merge.

## Output and limits

`schema="olav.topology-diff/v1"`. `status="success"` means the bounded read
completed; `comparison_status` can still be unknown or partial. Main exits 0
for completed reads (including unknown), 1 for validation/read/budget errors.
`diff_status` describes observed changes, no observed changes, or unknown.
Additions/removals describe returned observations, not physical link creation,
failure or recovery. `scope.coverage`, `collection_time` and `live_state` remain
unknown. `source_version` binds the exact database bytes and native read backend.

Counts are observed_added, observed_removed, content_changed, unchanged and
content_unknown. A link can contribute to both content_changed and
content_unknown when one known field changes and another lacks evidence; do
not sum them into a link total. `change_count` counts links with a definite
observed membership or content change. `changes` also includes unknown-content
samples, with per-field status. `changes_more` means samples were omitted; full
bounded counts remain available. The sample limit is not a data filter.

Input is limited to 16 KiB; source database 256 MiB; selected parent/record rows
4096; combined JSON/cell input 1 MiB; historical parent JSON 256 KiB; endpoint
and current-table content cells 256 UTF-8 bytes. Samples use at most 24 KiB;
response 64 KiB. Exceeding an input/source budget returns an error with null
counts, not a partial complete diff. A dedicated exec reader copies the locked
native source read-only using native_snapshot; the parent enforces the whole
operation deadline, owns scratch cleanup, and publishes only after successful
source revalidation. Caller-held writer locks remain intact. Use a trusted local
TMPDIR filesystem with normal locking. Configured warehouse storage is refused.
No DB writes, remote collection, model call or live network checks occur.
