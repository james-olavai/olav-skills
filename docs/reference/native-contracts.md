# Native interface and BGP records

These changes are in an unreleased standalone candidate. Published `olav-skills==0.1.1` does not include the parser and query updates described here.

| Recorded command | State source | Interpretation |
| --- | --- | --- |
| IOS `show ip interface brief` | `status`, `proto` | Interface and line protocol states |
| Junos `show interfaces terse` | `admin_state`, `link_state` | Administrative and link states. `proto` carries the protocol family. |
| IOS `show ip bgp summary` | `bgp_neighbor`, `state_or_prefixes_received` | A nonnegative integer prefix count, including zero, indicates Established. |
| Junos `show bgp summary` | Native `peer_ip`, `state`, or the compatible pair `bgp_neighbor`, `state_or_prefixes_received` | Native state text uses known BGP states. Numeric prefix counts indicate Established only with the compatible pair. |

For interfaces, `up` in both state fields is healthy. A known non-up component such as `down`, `admin-down`, `err-disabled`, `testing`, or `dormant` is nonhealthy even if the other field is missing. Remaining combinations are unknown. A historical Junos profile without `admin_state` can establish a known non-up link state and leaves administrative state unknown. For BGP, `Established`, `Estab`, and `Establ` identify an established session. `Idle`, `Active`, `Connect`, `OpenSent`, and `OpenConfirm` are observed nonhealthy states. Other values stay unknown. Simultaneously complete Junos BGP field pairs are ambiguous.

## Native import

Managed parsers, local Python parsers, and local TextFSM overrides retain precedence. Default Junos terse parsing covers physical and logical rows, recognized protocol families, and IPv4/IPv6 local addresses. Continuations inherit the interface and the latest named family, and repeated observations remain separate records. The parser records the embedded template digest. It rejects orphan or unsupported continuations, unsupported addresses, and oversized input. A parser miss can leave only raw evidence.

Default Junos BGP summary uses the installed Junos NTC template. A supported single summary includes one `Groups` header and peer lines matching its declared count. Native parsing retains peers without routing-table lines, including consecutive peers in that case. Unsupported layouts and unbound records fail closed. The default Junos parsers limit raw input to 1 MiB, individual lines to 4,096 characters, and records to 4,096. Support is verified using standalone import of synthetic IOS and Junos captures.

## Read operations

- `query_batch` keeps `interface_health` and `bgp_health` and exposes `unknown_count` alongside `healthy_count` and `unhealthy_count`. Its bounded samples contain observed nonhealthy records. For Junos interfaces, `status` remains the link state, `proto` the protocol family, and the additional `admin_state` field records administration. Native Junos BGP peers and state text use the existing `bgp_neighbor` and `state_or_prefixes_received` response aliases.
- [Recorded investigation recipes](investigation-recipes.md) return nonhealthy and unknown samples and bind the output database hash to a read-only private snapshot copy.
- [Recorded state differences](state-diff.md) read the selected commands from two exact historical SIDs. They retain legacy sample field names and expose `old_admin_state` and `new_admin_state` for Junos. BGP prefix count changes within an established session do not change its state. The comparator refuses duplicate identities, which can include one Junos interface reported on multiple family or address rows. `added` and `removed` describe record direction. Missing scope, source, or unsupported shapes leave counts unknown.

Interface and BGP data are recorded observations. Per-command views can omit older captures. Missing records cannot establish collection coverage, current health, or a physical event. Missing VRF and AFI stay unknown. The native read operations retain their input, output, and execution limits.
