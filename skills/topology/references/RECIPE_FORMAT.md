# Recipe YAML format (agent-facing)

Recipes are persisted by `save_recipe` under the bound project's config
`recipes/user/` directory and in `view_recipes`. Use the returned paths instead
of assuming a global home. Shipped historical built-in recipe files are not
seeded; built-in query layers use per-command views (see SUPPORTED_BUILTIN.md).

## Schema (top-level is a single-entry list for consistency with loader)

```yaml
- command: "show bgp summary"           # verbatim CLI command (must match parsed_outputs.command)
  concept: bgp_neighbors                # canonical category; pre-defined or user-extended
  vendor_hint: cisco_ios                # one of: cisco_ios, cisco_nxos, juniper_junos, arista_eos, universal
  field_mappings:                       # canonical_name -> source JSON key in parsed_data
    neighbor_ip: neighbor_ip
    neighbor_as: neighbor_as
    local_as: local_as
    router_id: router_id
    state: state_pfxrcd
    uptime: up_down
  filter_expr: ""                       # optional WHERE fragment (rarely used)
```

## Concepts and consumers

A custom concept produces `netops.v_<concept>_auto` when rebuild succeeds.
It must be queried through bounded SQL: the packaged `query_topology` accepts
only bgp/ospf/l2/all and does not expose arbitrary custom concepts. The field
names below describe common built-in typed records, not a promise that saving
a recipe wires it into those records or automatically validates that schema.

## Canonical field names (expected by Pydantic schemas)

### bgp_neighbors (→ BGPSession)
- `neighbor_ip` (required, IPvAnyAddress)
- `neighbor_as` (int)
- `local_as` (int | None)
- `router_id` (str | None)
- `state` (Literal: Established / Active / Idle / Connect / OpenSent / OpenConfirm / Down)
- `uptime` (str | None)

### ospf_neighbors (→ OSPFAdjacency)
- `neighbor_id` (required)
- `neighbor_ip` (IPvAnyAddress | None)
- `interface` (required)
- `area` (str | None)
- `state` (Literal: Full / FULL/DR / FULL/BDR / FULL/DROTHER / 2-Way / ...)
- `dead_time` (str | None)

## State canonicalization rules (applied at ingest, not by recipe or view)

- BGP numeric state ('0', '1', ...) → `'Established'` (Cisco prefix-count idiom)
- BGP `'Establ'` (Junos truncated) → `'Established'`
- OSPF: preserve role suffixes (don't strip `/BDR` from `FULL/BDR`)
- Everything else: pass-through

Recipes declare **which SOURCE field to use**; they do NOT encode canonical
values. R83.2 moved canonicalization out of the view layer into
`olav_skills.netops.tools.field_normalizer`, applied when the output is parsed — so
`parsed_outputs.parsed_data` already holds canonical states before any view
reads it, and `rebuild_views` projects columns without rewriting values. A
source field that still looks raw in the view was raw at ingest time; fix the
parser, not the recipe.

## Special directive: `@topology_links`

For L2 topology (where data is already structured in
`netops.topology_links`), use:

```yaml
- command: "@topology_links"
  concept: topology_l2
  vendor_hint: universal
  field_mappings: {}                     # empty — the ingest pipeline owns this view
```

`v_l2_links_auto` is built by `view_builder.build_l2_topology_view` during
ingest. `rebuild_views` reports the `@`-directive under `skipped` rather than
rebuilding it — the recipe row is coverage metadata, not a view definition.

## Validation (what `save_recipe` checks)

1. YAML parses; required fields present (`command`, `concept`, `field_mappings`)
2. `vendor_hint` is in the allowed set
3. Dry-run `CREATE OR REPLACE VIEW _probe AS ...` succeeds
4. `SELECT COUNT(*) FROM _probe` > 0 (reject empty unless `force=True`)

Canonical field names are **not** checked against the concept's schema — a
mapping the Pydantic schema does not recognise is accepted here and surfaces
later, when a consumer reads the view.

## Example LLM-drafted recipe (BFD on Cisco)

```yaml
- command: show bfd neighbors
  concept: bfd_sessions              # new custom concept
  vendor_hint: cisco_ios
  field_mappings:
    neighbor_ip: neighaddr            # source JSON key
    interface: interface
    state: state
    uptime: up_time
```
