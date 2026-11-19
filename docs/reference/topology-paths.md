# Historical topology paths

In Olav Skills 0.2, set an authorized absolute `OLAV_HOME` before running
`inspect_path`:

```sh
printf '%s' '{"snapshot_id":"capture-a","source_device":"R1","destination_device":"R3"}' | olav-skills run topology inspect_path
```

The exact snapshot selects historical parsed Cisco IOS detailed CDP/LLDP
observations. Current `topology_links` labels cannot reconstruct history and are
not used. Names remain exact; interfaces are canonicalized, parallel interface
pairs remain separate, and opposite-side/protocol observations aggregate.
LLDP requires an interface port ID, not port description or numeric ifIndex.
Other platforms, malformed records, missing/empty sources and duplicates remain
evidence gaps. Any extractor source reason returns unknown without enumerating
paths, preventing ambiguous parent unions. No hostname, IP ownership or device role is inferred.

Results are device-simple paths in an undirected recorded neighbor graph.
Reverse queries reverse endpoint order. Joining interfaces at an intermediate
device does not prove VLAN continuity, forwarding, routing, ECMP or failover.
Same-device requests are rejected; the tool does not create zero-hop results.
Link state, speed, type, coverage, capture time and live state remain unknown.

Optional `source_interface` and `destination_interface` filter only the first
and last edge. `max_hops` defaults to 8 (1–16), `limit` to 20 (1–50), and
`timeout_ms` to 15000 (1–15000). IDs must be exact, without latest/current aliases.
The tool does not promise shortest, preferred or equal-cost paths.

Check `status`, `path_status`, `reasons`, `search_complete`, `paths_more` and
`search.hop_boundary_reached`. `paths` contains discovered candidates;
`path_count` is their returned count, never total network paths. No candidate
means unknown with a null count. `search_complete=true` applies only to the
observed graph within hop/filter scope; `source_complete` is always false.
`paths_more=true` means another candidate was omitted; null means the remaining
set is unknown; reaching the hop boundary also leaves `paths_more` null.
`search.scope` states the observed-graph/hop/filter boundary. Missing discovery, limits or exhausted search cannot establish
that no network path exists.

Reads use a supervised dedicated Linux process and a read-only locked native
copy with source hash and final identity verification. The process closes every
source context and DuckDB scheduler before output; a failed exit is rejected.
Caller-owned writer locks/default connections are preserved. Limits: 16 KiB
request, 256 MiB source DB, 4096 combined parsed records/parents, 1 MiB combined
JSON, 256 KiB per parent, 512 graph devices, 2048 edges, 20000 expansion steps,
40 KiB path samples and 64 KiB response. Step/output truncation preserves only
discovered results and incompleteness; oversized input/graphs fail closed.
A hash identifies bytes, not origin authenticity. Trusted local filesystem/temp
roots and cooperative DuckDB locking are required. Warehouse, legacy mixed-layer
graphs and real-time collection are outside this contract.
