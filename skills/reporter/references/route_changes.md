# Compare recorded routes

Use `scripts/compare_routes.py` for route content changes between two explicit native snapshot IDs. Choose one source profile and, when useful, an exact device or VRF to narrow the comparison.

| `source` | Recorded Cisco IOS command | Fields compared |
| --- | --- | --- |
| `ios_ipv4_rib` | `show ip route` or `show ip route vrf <name>` | IPv4 prefix and next-hop set |
| `ios_ipv6_rib` | `show ipv6 route` or `show ipv6 route vrf <name>` | IPv6 prefix and next-hop set |
| `ios_ipv4_bgp` | `show ip bgp` or `show ip bgp vpnv4 vrf <name>` | IPv4 CIDR NLRI, next-hop set and observed ASN sequences on matching next-hop sets |

Both snapshots need historical parsed records for each compared device, VRF and address family. A row's explicit `vrf` or the stored single-VRF command establishes its context. Request filters select context and cannot supply missing evidence. The stock IOS IPv6 and BGP parsers discard or omit VRF on bare commands. Scoped commands need an existing parser that produces the supported fields. A Junos route summary carries counts and table names. The available summary does not support per-prefix comparison.

```json
{"source":"ios_ipv4_rib","snapshot_id_1":"snap-before","snapshot_id_2":"snap-after","device":"edge-1","vrf":"BLUE","limit":20}
```

Use the result's `comparison_status`, `counts`, `gaps`, `change_count` and `changes_more` together. Each sampled route carries device, VRF, address family and prefix. Duplicate paths are collapsed within that identity. `counts: null` means no comparable context. Unknown next hops and missing or unsupported AS_PATH values have their own counts. Prefix additions and removals describe the parsed record sets. Collection coverage, collection time, live reachability and real withdrawals remain unknown.
