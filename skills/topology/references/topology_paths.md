# Historical neighbor-graph paths

Unreleased native candidate; published `olav-skills==0.1.1` lacks this entry.
Use only the installed candidate's bound interpreter and an explicitly authorized
absolute `OLAV_HOME`:

```sh
printf '%s' '{"snapshot_id":"capture-a","source_device":"R1","destination_device":"R3"}' | olav-skills run topology inspect_path
```

The exact SID selects historical `netops.parsed_outputs` rows for Cisco IOS stock
`show cdp neighbors detail` / `show lldp neighbors detail`. `latest`, `current`,
`newest` and `all` are rejected. The current `topology_links` labels are never
used as historical reconstruction. Device names are exact, including case and
DNS suffix; no address ownership, role or hostname matching is inferred.
Interface abbreviations use the shared canonical interface normalizer. LLDP
uses neighbor port ID, not arbitrary port description. Numeric ifIndex/MAC-only
port IDs are unsupported. Unsupported platforms, malformed/empty sources,
unknown endpoints and duplicate parents/records are source gaps, never zero.

Each observed endpoint pair is an undirected neighbor edge. Opposite-side
observations aggregate, and CDP/LLDP protocols form a set. Parallel interface
pairs remain distinct. Reverse queries reverse the reported endpoint direction;
they do not prove bidirectional forwarding. Candidate paths are device-simple
(no repeated device); intermediate traversal joins interfaces by device identity,
without proving bridging, VLAN continuity, routing, forwarding, ECMP, redundancy
or failover. Same-device requests are rejected rather than inventing a zero-hop
path. Link status, speed, type, collection coverage/time and live state remain
unknown. A source hash identifies copied bytes, not authenticity of their origin.

Optional parameters:

| Parameter | Default | Bounds/meaning |
| --- | --- | --- |
| `source_interface` | absent | Canonical filter on the first edge only |
| `destination_interface` | absent | Canonical filter on the last edge only |
| `max_hops` | 8 | Integer 1–16; search scope, not a reachability guarantee |
| `limit` | 20 | Integer 1–50; returned paths, not total network paths |
| `timeout_ms` | 15000 | Integer 1–15000, complete supervised operation |

`schema="olav.topology-paths/v1"`, `status="success"` only proves the read ran.
`path_status="candidates_found"` means `paths` contains observed graph candidates;
`path_count` counts those returned entries only. Each path preserves ordered
`devices`, `hop_count` and interface-level `hops` with protocol sets. With no
candidate, `path_status="unknown"` and `path_count=null`: never report no network
path. Any extractor source reason stops enumeration and returns unknown with no paths;
conflicting/duplicate parents are never unioned into synthetic candidates.

`search_complete` means enumeration exhausted the observed graph within the
requested hop/filter scope. It does not establish complete collection, and
`source_complete` is always false. `search.hop_boundary_reached` records possible
longer expansions. `paths_more=true` means a further candidate was seen but
omitted by output limits; null means enumeration stopped without knowing whether
more candidates exist. Reaching the hop boundary also makes `paths_more` null.
`search.scope` explicitly names the observed-graph/hop/filter scope.
False applies only within the completed search scope,
never to all possible paths or absent observations. Do not describe `limit`
results as shortest, preferred, all-path or equal-cost routing results.

Input is capped at 16 KiB; the shared historical extractor caps combined parents
and records at 4096, combined JSON at 1 MiB and each parent JSON at 256 KiB.
Schema requires standard VARCHAR identity columns and native JSON parsed data;
SID/command scope uses binary encoding. Graph limits are 512 device nodes and
2048 distinct endpoint-pair edges; larger graphs fail closed. Iterative DFS has
at most 16 active hop frames and 20000 edge-expansion steps. Returned path JSON
is capped at 40 KiB and the complete response at 64 KiB. Step/deadline/output
stops preserve only discovered candidates with explicit incomplete status;
input/graph/source failure returns error with null count. Bounds cannot justify
absence or a total count.

Linux native reads run in a clean dedicated exec process, use `native_snapshot`
with a 256 MiB source cap, OFD read lock, private bounded copy, disabled external
access, hash and final source verification. The parent owns a hard deadline and
scratch cleanup, and rejects nonzero reader exits. After every connection and
source-validation context closes, the reader explicitly joins DuckDB's default
scheduler before output. Public Python `inspect_path(raw_bytes)` only supervises
that child and does not close the caller's database/default connection or release
its writer lock. WAL/locked/changed/unavailable sources are errors. This relies
on trusted local filesystem/temp roots and cooperative native locking; it is not
a database authenticity or adversarial filesystem certification. Warehouse and
legacy mixed-layer graphs are unsupported.
