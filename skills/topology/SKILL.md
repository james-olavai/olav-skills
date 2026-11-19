---
name: topology
description: "Query recorded BGP, OSPF and CDP/LLDP graph relationships or bounded path candidates. For neighbor-state diagnoses and evidence freshness use analyzer."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Topology

## Execution and permissions

Use the installed, bound interpreter and one JSON object on stdin:

```sh
printf '%s' '{"operation":"snapshot_summary","snapshot_id":"latest"}' | olav-skills run analyzer query_batch
```

Set `OLAV_HOME` to the authorized project before running. The host supplies
execution, file access and permissions. If it exposes a restricted native tool,
use that tool's actual name and schema; a shell example does not grant shell
access or permit bypassing the host. No device access, agent router, model
provider, memory service, simulation engine or general export tool is supplied.
Read sibling skills only when needed; loading a skill does not delegate work.
Scripts return JSON unless their documented output is text. Check process exit
and result status; successful execution alone does not prove an answer.

## Select the evidence question

For neighbor-state interpretation or suspected stuck sessions, use analyzer's
[OSPF contract](../analyzer/references/ospf_analysis.md) and `analyze_ospf`.
Use this skill for graph relationships. A single transition state does not
establish persistence or cause; an unknown verdict must remain unknown in the
first conclusion. Resolve a named capture from the authorized catalog or
actual metadata, rather than treating its label as `latest`.

## Recorded relationships

```sh
printf '%s' '{"concept":"all"}' | olav-skills run topology query_topology
```

Use `concept` = `bgp`, `ospf`, `l2` or `all`, pinning `snapshot_id` when known.
The script reads prebuilt views. Native output has
`result_contract.schema="olav.topology-result/v1"`, `backend="native"`,
`effective_concept`, ordered `requested_layers` and `resolved_snapshot_id`.
Interpret each `layers.bgp/ospf/l2` state before the compatibility arrays:

- `not_requested`: layer not queried, source `not_checked`, count null.
- `unavailable`: no usable snapshot or required view; count null, with reason
  `snapshot_unavailable` or `source_view_absent`.
- `empty`: requested, view available, zero rows in the selected query scope.
- `rows`: requested, view available, `row_count` returned records.

`collection_coverage` is always `unknown` in this version. A database-global
view being available does not prove collection in this snapshot; `empty` does
not prove a protocol is absent or has no sessions. `absent_layers` lists only
requested layers lacking the required view, not uncollected device commands.
If no snapshot can be resolved, the outer ID is `none`, the contract ID is null
and requested layers are unavailable with no view check. Invalid concepts fail.
Older results without this contract and legacy warehouse output do not establish
native layer scope. See [built-in sources](references/SUPPORTED_BUILTIN.md).

For inventory, coverage or health use `query_batch` and the
[query contract](../netops/references/batch_query.md). Adjacency remains the
job of `query_topology`. Use writer for diagrams.

## Device, interface and address identity

Keep L2 device/interface edges separate from protocol peers identified only by
address. Associate a peer address with a named device/interface only when the
returned evidence explicitly records address ownership or supplies a verified
mapping for that scope. A neighbor address, hostname, platform, ASN or one L2
adjacency does not establish that mapping. Management/loopback addresses do not
identify a link endpoint unless evidence explicitly binds them to it.

A same-subnet claim additionally needs the actual endpoint IPs and masks/prefixes
or an explicit verified subnet mapping. Address resemblance or L2 connectivity
alone proves neither subnet membership nor shared addressing. If those facts
are missing, leave the peer unassociated; do not state a speculative association
and then try to retract it with an unknown caveat. See
[identity evidence](references/SUPPORTED_BUILTIN.md).

Draw the relationships actually established by the requested scope. A known L2
edge can be delivered without identifying every protocol peer. If entity names
are unspecified, bounded discovery within the authorized inventory may provide
candidates; candidates do not establish snapshot membership or a connection.
Host-supplied candidates have the same limits and do not override explicit user
entities. If candidates cannot resolve the scope, report the gap. Do not expand
a connection diagram into unrelated protocol exploration to fill address gaps,
or ask for hostnames already established in the context.

## Other protocols: opt-in recipe writes

Read [recipe format](references/RECIPE_FORMAT.md) only when extending coverage.
`list_recipes` reports existing recipes; `table_present=false` means unseeded,
not corrupt. If coverage is missing, explain it. Only with authorized local
recipe/database writes:

1. `prepare_recipe` with `protocol` and `vendor` returns candidate commands,
   a parsed sample and drafting instructions.
2. Draft declarative YAML yourself from actual sample keys. No model provider
   or automatic discovery engine is supplied by the package.
3. `save_recipe` with `recipe_yaml` validates/dry-runs and persists it. Check
   errors and zero-row results; do not force unexplained mappings.
4. `rebuild_views` with `concept` materializes the view; inspect `skipped` and
   row counts. A missing command is a gap, not healthy empty coverage.
5. Query the resulting custom view with authorized bounded analyzer SQL.
   `query_topology` does not support arbitrary concepts; do not use its empty
   result as evidence for a custom protocol.

`field_mappings` maps canonical names to real source JSON fields; vendor hints
are `cisco_ios`, `cisco_nxos`, `juniper_junos`, `arista_eos` or `universal`. Keep OSPF role
suffixes; numeric prefix counts establish BGP only. Do not seed historical
built-in recipe files or write Python ETL as a substitute.
## Evidence boundaries

Use a supported bounded template first, one batch for a fleet question. Empty
optional filters discover within authorized scope; limits, clipped cells and
`has_more` / `byte_truncated` still apply. Discovery is not an unlimited export.
Do not infer snapshot membership from inventory or device role from a hostname,
platform, ASN or loopback. Keep unknown roles separate. Missing/unsupported
sources, failed queries and unknown values are not zero or healthy results.
Do not silently drop filters or narrow the user's question.

Cite snapshot and returned collection-time provenance, including precision,
source and status. Recorded data is not live state. Ingest/update time and a
date-looking ID do not prove capture time. If `latest` returns
`ambiguous_snapshot_time`, report the ambiguity and use an explicit authorized
snapshot only when the user's scope supports it; do not order IDs to guess.
An explicit ID can support observations while collection time stays unknown.
Stop once evidence answers the question. Do not repeat unchanged failures or
raise budgets; a corrective retry needs a real corrected argument within scope.
Report unfinished scope and source/preview truncation.

## Unreleased historical path candidate

For paths between named devices in a recorded neighbor graph, read
[historical topology paths](references/topology_paths.md) and use `inspect_path`
with an exact snapshot. Preserve interfaces, parallel links and enumeration
limits. A graph candidate does not prove forwarding, ECMP or operational failover.
Missing or incomplete discovery must not become a claim of no network path.
This local candidate is absent from published 0.1.1.
