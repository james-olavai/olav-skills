# Historical route comparison

`compare_routes` is available in Olav Skills 0.2. It compares historical Cisco IOS parsed route records for two exact snapshot IDs in the project selected by `OLAV_HOME`.

```sh
export OLAV_HOME=/absolute/path/to/authorized/project
olav-skills run reporter compare_routes <<'JSON'
{"source":"ios_ipv6_rib","snapshot_id_1":"snap-before","snapshot_id_2":"snap-after","device":"edge-a","vrf":"BLUE"}
JSON
```

| `source` | Historical command | Comparison |
| --- | --- | --- |
| `ios_ipv4_rib` | `show ip route` or `show ip route vrf <name>` | IPv4 prefixes and recorded next-hop sets |
| `ios_ipv6_rib` | `show ipv6 route` or `show ipv6 route vrf <name>` | IPv6 prefixes and recorded next-hop sets |
| `ios_ipv4_bgp` | `show ip bgp` or `show ip bgp vpnv4 vrf <name>` | IPv4 CIDR prefixes, next-hop sets and plain ASN sequences |

The optional `device` and `vrf` filters are case-sensitive. The historical parsed row needs an explicit `vrf` or must come from a single-VRF command recorded in the snapshot. A request filter cannot fill a missing routing context. The usual IOS IPv6 and BGP templates omit the VRF. An existing parser must produce supported fields for a scoped command to take part. Junos route summaries supply table names and counts, so per-prefix Junos comparison is unavailable.

An observation is identified by device name, VRF, address family and normalized CIDR prefix. Repeated identical paths collapse within that identity. IPv6 link-local next hops need their recorded interface. Route metrics and protocol selection are outside this comparison. The BGP profile compares AS_PATH sets only when both snapshots have comparable matching next-hop sets and all corresponding sequences are present. The profile supports plain 32-bit ASN sequences and the single integer emitted by the native normalizer. Missing or blank paths and unsupported AS_SET or confederation formats remain unknown.

`olav.route-diff/v1` reports counts for comparable contexts, sampled `changes` and coverage `gaps`. `counts: null` means no context could be compared. `comparison_status: partial` means one or more contexts or compared fields lacked evidence. The default of 20 samples can be changed with `limit` from 1 to 50. A response can contain fewer samples due to its byte cap. The counts cover all successfully compared stored records in the request even when sample limits apply. `timeout_ms` may narrow the 15-second maximum. Oversized source reads or elapsed deadlines return unknown counts.

The comparison uses a bounded copy of the native historical table's database, with a source SHA-256 and a read lock. It requires Linux and a checkpointed native database. Snapshot order comes from the request. Prefix counts and path differences describe recorded observations, with unknown collection time and coverage. A missing prefix in recorded results cannot establish a live withdrawal or reachability change.
