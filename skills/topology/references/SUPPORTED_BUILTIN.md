# Built-in protocols, and where `view_recipes` fits

**BGP, OSPF and CDP/LLDP need no recipe.** They are answered from per-command
auto views that the ingest pipeline builds from whatever was collected — one view
per command, columns inferred from the parsed JSON. `query_topology` reads those
views directly.

| Layer | Source view | Built by | Schema model |
|---|---|---|---|
| BGP | `netops.v_show_ip_bgp_summary_auto` | `build_per_command_views` | `BGPSession` |
| OSPF | `netops.v_show_ip_ospf_neighbor_auto` (+ `..._interface_brief_auto` for `area`, when collected) | `build_per_command_views` | `OSPFAdjacency` |
| L2 | `netops.v_l2_links_auto` | `build_l2_topology_view`, from `netops.topology_links` | `L2Link` |

## Native layer scope contract

`result_contract` uses schema `olav.topology-result/v1`, backend `native`,
`effective_concept` and `requested_layers` in bgp/ospf/l2 order. Default concept
is all. `resolved_snapshot_id` is the actual selected ID or null if unresolved.
The old top-level snapshot ID remains `none` for the unresolved case.

Each layer has `requested`, `source_view`, `result_state`, `row_count` and
`collection_coverage`. This native version fixes collection coverage to unknown.

| result_state | source_view | row_count | Meaning |
|---|---|---|---|
| not_requested | not_checked | null | Outside this request; do not infer zero |
| unavailable | absent | null | Required view absent; reason=source_view_absent |
| unavailable | not_checked | null | No resolved snapshot; reason=snapshot_unavailable |
| empty | available | 0 | SELECT returned zero rows for this scope |
| rows | available | positive | Number of rows returned by this SELECT |

Catalogue presence is global to the database, not per-snapshot command coverage.
An older snapshot may have created a view with no rows for the selected one.
Neither available/empty nor absent proves whether devices collected a command,
configured the protocol or have sessions outside these returned records.
`absent_layers` is the compatibility list of requested layers with absent views;
it is empty for unresolved snapshots because views were not checked. Arrays
remain for compatibility, but an empty array alone cannot distinguish these
states. Consumers must use the layer contract and keep source truncation or
later projection bounds separate. Source/validation errors do not yield a
successful empty contract. Invalid concepts are rejected in the native path.
Legacy warehouse delegation does not carry or establish this native contract.

## Identity and cross-layer associations

An L2 row establishes the recorded device/interface pair and discovery/status
fields it contains. It does not supply endpoint IPs, masks or protocol-peer
ownership unless those fields are explicitly returned. A protocol neighbor IP
identifies a peer address observed by the reporting device, not automatically
the named device on an adjacent L2 edge.

For a device/interface-to-address association, cite an explicit returned address
assignment or verified identity mapping in the applicable scope. For subnet
comparison, also require recorded masks/prefixes for the endpoints or a verified
subnet mapping; never infer a mask or subnet from familiar-looking addresses.
If evidence is absent or conflicting, preserve the known edge and keep the
protocol peer unassociated. Labeling an association as uncertain later does not
make an unsupported earlier subnet or identity claim valid. Scope the diagram
to the requested relationships rather than pursuing unrelated protocol facts.

## What `view_recipes` is for now

**User-added protocols only** — BFD, HSRP, VRRP, IS-IS, LDP, other user-declared concepts. A recipe maps a canonical field name onto a
column of an existing per-command auto view, and `rebuild_views` materialises it
as `netops.v_<concept>_auto`.

Nothing seeds the table. `save_recipe` creates it on first use, so an install
that has only ever ingested a bundle is in the normal state, not a broken one:
`list_recipes` returns `{"recipes": [], "table_present": false}` and the
discovery flow in `SKILL.md` works from there.

The shipped `recipes/builtin/*.yaml` files are leftovers of the removed L1 layer.
Their `field_mappings` reference field names (`neighbor_ip`, `state_pfxrcd`) that
the current parsers do not produce, so loading them would build empty views —
`rebuild_views` reports such a recipe under `skipped` with the columns it could
not find. Do not seed them.

## Adding a protocol

Use the discovery flow in `SKILL.md` (`prepare_recipe` → draft → `save_recipe` →
`rebuild_views`). A recipe that proves useful across vendors is worth proposing
as a shipped default in a PR — together with a Pydantic model in
`olav_skills.netops.schemas.topology` if the concept is new — but there is no
auto-loading path for it to plug into, and adding one would re-create what R83.2
removed.
