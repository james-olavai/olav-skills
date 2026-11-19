# Recorded OSPF analysis

The top-level `assessment` keeps `persistence_status` and `root_cause_status`
unknown and `confirmed_stuck_count` null, including empty input, all-Full rows
and two matching transition records. This profile has no qualified adjacency
duration. Null is not zero, and a transition is not a confirmed persistent fault.
Report this assessment consistently in the conclusion and summary. Do not add
unrequested capture clocks or ranked causes. If a clock is needed, preserve
that clock's own provenance: catalog Z is explicit UTC, while a naive database
value has unknown timezone; never transfer precision across sources.

## State the evidence limit in the conclusion

If the question asks whether a session is stuck, unstable or persistently
failing, one recorded transition establishes no duration. Lead with that
insufficiency, then list the observed state. Do not answer yes/stuck/fault and
try to retract it in a later caveat. ExStart does not establish MTU mismatch or
any ranked cause. 2-Way requires network type and local/remote role evidence.
Persistence needs independently timed comparable observations with qualified
adjacency identity; the mere existence of two record labels is insufficient.
Do not claim the project has only one capture merely because only one was
queried. Resolve requested names using an authorized catalog or source metadata;
if resolution is unavailable ask, rather than substituting latest.

In 0.2, `olav-skills run analyzer analyze_ospf` takes one stdin JSON object.
Run it with host authorization. Set `OLAV_HOME` to the authorized absolute native
project. Linux native DuckDB only; no device access or warehouse support.

```json
{"mode":"inspect","snapshot_id":"capture-a","devices":["R1"],"vendors":["cisco_ios"],"limit":20,"timeout_ms":15000}
```

For a historical record comparison:

```json
{"mode":"compare","snapshot_id_1":"capture-a","snapshot_id_2":"capture-b","devices":["R1"]}
```

`mode` defaults to `inspect`. Exact case-sensitive snapshot IDs are required;
latest/current/newest/all aliases and equal comparison IDs are rejected.
Optional `devices` is a unique exact-name list (maximum 64). `vendors` is a
unique list of `cisco_ios` / `juniper_junos` (maximum 64); both default to `[]`
for no filter. Filters are canonicalized to sorted lists in
`effective_request`. `limit` defaults to 20, maximum 50 per observation/change
sample. `timeout_ms` is 1–15000, default 15000. Unknown keys, duplicate JSON
keys, duplicate filters, booleans as limits and nonfinite numbers are rejected.
No request can supply missing VRF, process, area, network type or local role.

## Supported evidence

Only historical `netops.parsed_outputs`, exact IOS `show ip ospf neighbor` and
Junos `show ospf neighbor`, with their stock IPv4 fields are supported:
`neighbor_id`, `ip_address`, `interface`, `state`, `priority`, `dead_time`.
Other nonempty routing context or extra parsed fields are unsupported, not
silently erased. Empty/missing sources, invalid identity/state, duplicate
parents or duplicate normalized record identities produce unknown reasons.
Known bounded observations may still be shown, but incomplete input never
becomes complete evidence or a healthy zero. Explicit requested devices with
no matching source remain unknown. `vendors` is a platform filter, not proof of
vendor coverage or whole-snapshot membership.

The observed key is `(device, canonical platform, exact command, canonical
interface, IPv4 neighbor address, IPv4 neighbor ID)`. These stock commands do
not expose VRF, process or area. `routing_context` therefore preserves nulls,
with qualification `not_exposed_by_stock_profile`. A command string does not
prove a default VRF or single process. Collection time, source coverage and
live state are unknown; exact SID and current DB hash are not collection times.

## Meaning and output

Schema is `olav.ospf-analysis/v1`. Check `status`, source reasons,
`effective_request`, and native `source_version.sha256` before using results.

- `full_observed`: recorded Full state, indicating observed establishment;
  health remains unknown. It does not prove whole-network health.
- `two_way_unknown`: recorded 2-Way; without network type and local/neighbor
  role context this cannot be classified as an adjacency fault. DR/BDR labels
  on the neighbor alone do not fill the local role.
- `transitional_observed`: Down, Attempt, Init, ExStart, Exchange or Loading;
  an observation to investigate, not proof of persistent stuck state, duration,
  MTU mismatch or another cause.
- `unknown`: unrecognized state. Health is always `unknown` for all classes.

Inspect `summary` contains `record_count` and the four mutually exclusive
class counts, whose sum equals `record_count`; no usable records means null
summary/count, not zero healthy neighbors. Counts cover the bounded parsed
input; `observations_more` indicates an output sample. Reasons make
`analysis_status=unknown` even if some known observations are retained.

Compare uses one pinned DB copy for both SIDs. Unique usable records yield
`comparison_status=partial`, `comparison_scope=observed_records_only`, counts
of `observed_record_added`, `observed_record_removed`,
`observed_record_state_changed`, and `observed_record_unchanged`.
`changes` samples changed records, with before/after observations;
`changes_more` is independent of complete counts. State changes include the
neighbor-role suffix. Duplicate/ambiguous/unsupported sources on either side
suppress the entire comparison: unknown, null counts, no changes. Missing
routing context always leaves `routing_identity` and `adjacency_continuity`
unknown. Neither unchanged keys nor added/removed records prove the same real
session, a physical failure, recovery, continuous uptime or absence of peers.

## Read boundary

Requests are limited to 16 KiB, responses 64 KiB, source DB 256 MiB, at most
4096 source parents/parsed records, 256 KiB per parsed parent and 1 MiB total
parsed payload. Exhaustion fails explicitly; samples do not authorize full
exports. Vendor filtering happens after bounded source reads. The parent
supervises a clean-environment dedicated exec reader with a hard deadline and
private temporary copy. It uses native OFD locking, read-only SQL and final
source revalidation; all contexts exit and the reader closes its own default
DuckDB scheduler before publishing JSON. Caller connections and writer locks
are not closed. Abnormal exit, malformed/mismatched JSON and response-budget
failure do not publish a success; nonzero reader exits expose only the numeric
`worker_exit_code`. This is not a source authenticity certificate.

The dedicated Linux worker initializes a single-thread in-memory DuckDB default
connection before its first public DuckDB import, then closes normally after
source verification. This initialization is restricted to the actual worker
exec entry; Python callers and preloaded connections are never replaced.
Required APIs and effective settings are checked; unsupported initialization
fails closed. Validation covers DuckDB 1.5.6, not future versions or every native
reader in this package.
