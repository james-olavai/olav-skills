# writer: diagrams and presentation

Writer turns recorded relationships or an existing adjacency table into artifacts. Investigation belongs to analyzer/reporter; writer must preserve technical facts rather than add new ones. The host supplies any file editing or report-saving tool needed for prose polishing.

## Draw from a scoped database query

`draw_topology` reads relationships and writes a diagram in one call. Supply `name_like` (SQL LIKE pattern) or `center` with `hops`, and an explicit `snapshot_id`. Optional filters include `roles` and `sites`; unknown role data can make such filters incomplete. A hostname pattern such as `%core%` is a naming scope, not proof of network role.

Supported `protocol` values are `l2_cdp`, `bgp` and `ospf`. `diagram_format` is `drawio` or `mermaid`; **SVG is not a `draw_topology` output format**. Optional `filename` selects a base name and `max_hosts` bounds scope (default 120).

Inspect `status`, actual `path`, `hosts`, `edges`, `scope` and format. An unscoped request is refused; `too_wide` means narrow the graph. The synthetic [walkthrough](../offline-data.md) uses `name_like: R%`, its actual snapshot ID, `max_hosts: 2` and Mermaid to verify two nodes/one edge and an existing file.

## Convert an already authorized table

| Script | Input | Output |
| --- | --- | --- |
| `render_topology_mermaid` | `adjacencies_table_markdown` | JSON-encoded string with a Mermaid block or omission note |
| `render_topology_drawio` | Same table; optional `device_metadata`, `protocol` | JSON-encoded XML string or omission note |
| `render_topology_svg` | Same table; optional `protocol` | JSON-encoded inert SVG string or omission comment |

Decode the JSON string before saving it; stdout is not raw XML/SVG. These converters do not add a database query. Use columns identifying source, local interface, destination, remote interface and status as in the generated adjacency tables. Do not invent missing links.

SVG accepts no path, database, network, metadata or template option. It caps the stdin request at 300000 bytes, table text at 262144 UTF-8 bytes, 300 nodes, 1000 edges and 256 characters per cell. Empty, malformed or over-budget input produces a `diagram omitted` comment, which must not be presented as a successful graph.

## Keep provenance with the artifact

Include snapshot, source and scope next to the diagram in a report. A line records a relationship observation, not proven physical wiring or packet reachability. Check artifact contents before sharing; device identifiers can be sensitive. For existing prose, preserve IPs, AS numbers, device names, timestamps, CLI lines, tables and code fences exactly. See [the topology workflow](../workflows/topology.md) and [safety](../safety.md).
