# Recorded content differences (unreleased)

Published PyPI 0.1.1 does not contain this source candidate. After installing a
validated release that includes it, use:

```sh
printf '%s' '{"source":"interfaces_ios","snapshot_id_1":"before","snapshot_id_2":"after","device":"edge-a","limit":20}' | olav-skills run reporter compare_state
```

Bind the authorized `OLAV_HOME` first. Two explicit SIDs are required; `latest`
is rejected. Sources are `bgp_ios`, `bgp_junos`, `interfaces_ios`,
`interfaces_junos`, `l2`; `routes` explicitly returns unsupported. See the
public manual topic **Recorded state differences** for exact command/field
schemas. No new Open WebUI permission or model routing is established.

The result schema is `olav.state-diff/v1`. Check process exit and execution
`status`, then `diff_status`, exact `counts`, scope/source binding and
limitations. `added`/`removed` mean records only; never call them physical down,
route withdrawal or live events. Coverage and collection time stay unknown.
Null values stay unknown; missing VRF/AFI never means default. Parallel L2
links retain both interfaces and directed discovery; duplicate identities fail
rather than selecting one. Historical parsed outputs are read directly because
per-command auto views hide older SIDs. Missing command/schema/view/query is
not an empty capture. An existing L2 view's empty observed set can be compared
while collection coverage remains unknown.

`limit` is 1–50, default20; `device` matches the reporting source for L2.
Counts are independent of samples; `has_more`, `byte_truncated` and
`cell_truncated` qualify sample completeness. `value_unknown` overlaps other
counts, including added/removed records; do not sum it again. A failure has
counts null and diff unknown, never zero. Fixed parameterized SQL, read-only
DB, whole-query deadline, memory/row/cell/byte bounds are enforced. Do not
retry unchanged failures or raise host budgets to obtain a reassuring answer.

The current unreleased candidate shares [native state contracts](../../netops/references/native_contracts.md).
Junos BGP accepts its native peer_ip/state or explicit compatible field pair;
Junos admin state is compared separately from link state and protocol family.
Legacy missing admin remains unknown. Simultaneous BGP field pairs or duplicate
multi-family/address identities are rejected. `routes` stays unsupported in
this entry; use the separately scoped [route comparison](route_changes.md).
