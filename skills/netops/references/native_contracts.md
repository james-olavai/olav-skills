# Native interface and BGP state contracts

Unreleased standalone candidate. Use the native importer and the fixed `query_batch`, `run_investigation`, or `compare_state` entry with the host-authorized `OLAV_HOME`. Use explicit recorded snapshot IDs for historical evidence.

- IOS interface health reads `status` and line `proto`. Junos reads `admin_state` and `link_state`. Junos `proto` carries family, and legacy missing admin state stays unknown. `up` in both state components is healthy. A known non-up state is nonhealthy. Unrecognized or incomplete state stays unknown unless the other component is known non-up.
- IOS BGP uses `bgp_neighbor` and `state_or_prefixes_received`. Native Junos uses `peer_ip` and `state`. A Junos view may expose the explicit compatible IOS field pair. A nonnegative numeric prefix count implies Established only in the prefix-count profile. Unknown native state remains unknown. Two complete Junos profiles are ambiguous.
- Default Junos import supports the single-summary BGP profile with the installed NTC template, including down peers without routing-table lines. Native terse covers physical/logical rows and supported protocol-family/address continuations. Managed and local template precedence remains in force. Repeated rows are retained as observations.
- Batch health emits exact known and unknown record counts and samples known nonhealthy rows. Investigation also samples unknown rows. Per-command views can omit historical captures. Absence does not establish collection coverage.
- Historical comparison reads selected parsed command records. Direction is from `snapshot_id_1` to `snapshot_id_2`. BGP prefix-count variation alone leaves Established unchanged. Junos admin state compares separately from link state and family. Duplicate identity refuses comparison, including some multi-family or multi-address terse captures. Counts are unknown when the source or selected scope is missing or unsupported.

Report record health as a source-bound observation. Keep live state, collection time gaps, and missing VRF/AFI unknown.
