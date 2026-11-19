# Recorded topology changes

In Olav Skills 0.2, compare two exact snapshots with:

```sh
printf '%s' '{"snapshot_id_1":"before-id","snapshot_id_2":"after-id"}' | olav-skills run reporter compare_topology
```

Set an authorized absolute `OLAV_HOME`. The default `source="parsed_neighbors"` uses historical parsed Cisco IOS
`show cdp neighbors detail` and `show lldp neighbors detail` records. Both sides
must have matching device/command parent scope. It preserves device/interface
endpoints and parallel links, normalizes direction and interface abbreviations,
and compares aggregated observed protocol sets. It does not infer hostname or
IP ownership. LLDP uses port ID, never an arbitrary port description.

The stock profiles have no physical link status, type or speed evidence. Those
fields remain unknown. Empty/missing/malformed sources, unsupported platforms
and ambiguous duplicates cannot become a zero count or a healthy network.
Opposite-device observations and different protocols can aggregate legitimately;
duplicate endpoint records within one parent remain conservatively unknown.

The optional `source="topology_links"` compares current stored labels only and
always reports partial. Native import overwrites a matching link's snapshot
label, so this source cannot reconstruct historical topology. It can compare
known protocol/status/type/speed fields when present; unknown values are never
treated as changes. Either side empty makes counts null.

`limit` bounds samples (default 20, max 50); `timeout_ms` caps the complete
operation (max/default 15000). IDs must be distinct and exact, without latest
aliases. Database reads use a dedicated supervised process, locked native copy,
source hash and bounded input/output. No database mutation occurs.

A successful read may still return `comparison_status="unknown"` or `partial`.
Counts describe observed_added, observed_removed, content_changed, unchanged and
content_unknown. Changed and unknown can overlap for a single link; do not add
these counts into a link total. `change_count` counts definite observed changes;
`changes` can also show unknown fields. Check `changes_more` for omitted samples.
Budget errors expose null counts. Coverage, collection time and live state stay
unknown. Observation removal does not establish a physical outage.
