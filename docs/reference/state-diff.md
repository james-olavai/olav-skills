# Recorded state differences

Olav Skills 0.2 provides `compare_state` for recorded state differences.

Set `OLAV_HOME` to the authorized project. Compare two explicit snapshot IDs:

```sh
printf '%s' '{"source":"bgp_ios","snapshot_id_1":"before","snapshot_id_2":"after","limit":20}' | olav-skills run reporter compare_state
```

This deterministic read-only command compares recorded observations. It does
not compare full raw configurations, authorize devices or execute changes.
The existing key-only `diff_summary` and `diff_snapshots` keep their semantics.
No Open WebUI tool permission or natural-language routing is added.

## Supported sources

| `source` | Historical source and fixed schema | Identity | Compared fields |
| --- | --- | --- | --- |
| `bgp_ios` | `netops.parsed_outputs`, exact `show ip bgp summary` | device_name, bgp_neighbor, vrf, afi | state from state_or_prefixes_received |
| `bgp_junos` | same table, exact `show bgp summary` | device, peer_ip or compatible bgp_neighbor, vrf, afi | native state text or explicit state-or-prefix-count profile |
| `interfaces_ios` | same table, exact `show ip interface brief` | device_name, interface, vrf, afi | status, proto, ip_address |
| `interfaces_junos` | same table, exact `show interfaces terse` | same | link_state as status, admin_state, proto family, ip_address |
| `l2` | `netops.v_l2_links_auto` | source_device, source_interface, destination_device, destination_interface, discovery_protocol | link_status |
| `routes` | unsupported | no stable VRF/AFI/multipath schema | next-hop/AS_PATH are not supported |

Parsed command records must be JSON arrays of objects containing the listed
scalar identity and state/address fields. Missing required fields, nested
values, malformed JSON and null parent data fail explicitly. An explicit null
state/address is supported and remains unknown. Legacy Junos admin_state can be absent and remains unknown. Optional `vrf` and `afi`
may be absent: they remain null, representing unknown scope, never default VRF
or an inferred address family. Source command is fixed by the selector; records
from different commands are never silently combined. Returned
`scope.canonical_binding` identifies the exact command and field schema.

State fields follow [native contracts](native-contracts.md). Only the
state_or_prefixes_received profile interprets a nonnegative integer prefix count
as Established; native Junos state uses known state text. Unknown or unsupported
state values remain unknown. Prefix-count variation alone is not a session
state change. Junos administration is distinct from link state and family.

Per-command auto views hide historical snapshots behind their latest filter.
This operation instead reads the selected historical parsed records directly;
it does not use those views or infer chronology from snapshot names. Both
snapshot IDs must exist in parsed evidence. `latest`, blank and nonexistent
IDs fail. No capture-time qualification is inferred from an explicit ID.

## Interpreting results

The versioned result is `olav.state-diff/v1`. `status: success` means execution
succeeded; `diff_status` is `observed_changes`, `no_observed_changes` or `unknown`.
This is not a health verdict or an execution approval. `scope` includes both
snapshot IDs, exact source binding and device filter. Collection time and
collection coverage are explicitly unknown.

`counts.added`, `removed`, `changed` and `unchanged` are exact counts of the
selected observed records, independent of sample limits. `total_changes` sums
the first three. `value_unknown` counts union records with missing compared
values on either side, including the absent side of added/removed records;
it overlaps the other counts and must not be added to them. Null transitions
can be literal recorded changes with `certainty: unknown`; null on both sides
is not proof of known unchanged state. A no-change comparison with unknown
values has `diff_status: unknown`.

An added/removed record does not prove device arrival, routing withdrawal or
physical link down. L2 direction is preserved; parallel links retain both
interface names. One-sided discovery does not prove bidirectional collection.
An available L2 view can have an empty observed set on either snapshot; exact
record differences remain valid with collection coverage unknown. A missing
view, column or failed query is an error, never an empty set. For parsed
sources, a captured empty array is distinct from a missing selected command:
the latter fails `source_incomplete` rather than inventing an empty capture.

Duplicate identities, including identical duplicates or ambiguous multiple
addresses/paths, fail `ambiguous_identity`. Missing device/interface/peer/L2
identity fields fail `unknown_identity`. No record is arbitrarily selected.
Optional VRF/AFI nulls use null-safe identity matching, qualified by unknown
scope; they do not establish that routing contexts are equivalent.

## Bounds and failures

Input accepts only `source`, `snapshot_id_1`, `snapshot_id_2`, optional `device`
and `limit`. Device is an exact source-device filter (for L2, the reporting
source_device), retained in output; it never silently filters the destination.
`limit` is 1–50, default 20. Values are SQL parameters; table and field names
are fixed. The CLI reads at most 16KiB input. Counts stay in DuckDB and only
SQL-limited samples reach Python. Sample cells are clipped to 256 characters
with `cell_truncated`; clipped identities cannot be used to infer equality.
Samples have a 24KiB JSON byte budget; `sample_count`, `has_more` and
`byte_truncated` distinguish row and byte truncation. The complete response has
a 64KiB hard budget. Full values determine joins, ordering and exact counts.

The comparison uses a read-only connection with external access disabled,
a whole-query 15-second deadline, and the existing bounded host memory/thread
configuration (default 512MB and two threads; ceilings 2048MB and eight).
The returned `limits` reports effective limits. Host-only deadline overrides
can lower the deadline; stdin cannot raise resource budgets or supply SQL/paths.

Errors return nonzero process status, typed `error_type`, `diff_status: unknown`
and `counts: null`. Source/schema/ambiguity errors, timeout and budget failures
never become reassuring zero changes. No warehouse fallback or raw config
reader is attempted. Package execution, harness discovery, model routing and
browser/production acceptance remain separate validation scopes.

Routes remain unsupported in compare_state; use [compare_routes](route-changes.md) for its narrower verified profiles.
