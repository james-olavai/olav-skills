# topology: recorded network relationships

Use topology for BGP sessions, OSPF adjacencies and CDP/LLDP-derived L2 links. These relationships are derived from collected command outputs. They do not establish live connectivity, forwarding policy or end-to-end reachability.

## Query an explicit snapshot

`query_topology` accepts `concept` (`bgp`, `ospf`, `l2`, `all`) and `snapshot_id`. Use the real ID returned by import. The [offline guide](../offline-data.md) shows how to encode that ID safely from `import.json` into stdin JSON.

The response contains the selected `snapshot_id`, `bgp_sessions`, `ospf_adjacencies`, `l2_links` and `absent_layers`. Read absence before interpreting an empty list. The synthetic example has an L2 link but no OSPF source view: “OSPF was not available in this capture” is justified; “there are no OSPF neighbors” is not.

BGP numeric prefix counts can normalize to `Established`; OSPF role suffixes such as `FULL/BDR` are retained. Missing optional OSPF area data can remain null. Native built-in relationships depend on their supported canonical source views; another vendor's parsed records do not automatically imply built-in relationship support. Inspect actual coverage.

The default topology selector has its own L2-preference and ID-ordering behavior. It is not the bounded batch selector's conservative collection-time resolution. Pass explicit IDs to keep relationship queries and diagrams aligned.

## Extend views with a reviewed recipe

| Step | Script and important input |
| --- | --- |
| Check coverage | `list_recipes`, optional `concept`; distinguish `table_present: false` |
| Gather drafting material | `prepare_recipe` with `protocol` and `vendor` |
| Review declarative YAML | Use actual sample field names as mapping values |
| Validate and save | `save_recipe` with **`recipe_yaml`** (not `yaml_text`) |
| Rebuild derived views | `rebuild_views`, optional `concept` |

`prepare_recipe` returns candidates, a parsed sample and drafting instructions; it does not call a model or write the recipe. `save_recipe` validates, dry-runs and persists; it and `rebuild_views` are write operations. Do not force a zero-row recipe without understanding why it is empty. Source commands missing from a snapshot can be reported as skipped.

Custom concepts are queried through the resulting view with reviewed bounded SQL. The built-in `query_topology` only understands the four concepts above; passing a custom name can look like an empty result and is not a valid coverage test. For diagrams use [writer](writer.md), and for a practical query-to-artifact flow see [the topology workflow](../workflows/topology.md).
