---
name: analyzer
description: "Analyze recorded inventory, interface/BGP states, OSPF neighbor observations, counter baselines and evidence freshness; use bounded queries and draft change plans."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Analyzer

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

## Query recorded state

`query_batch` supports `snapshot_summary`, `device_coverage`,
`command_coverage`, `devices`, `commands`, `multi_device_summary`, `top_n`,
`diff_summary`, `interface_health`, `bgp_health`. Read the
[query contract](../netops/references/batch_query.md) for accepted keys, exact
counts versus samples, inventory versus observed membership and source failures.
Use `context` only for relevant capability/schema metadata when templates do
not cover the question; then use bounded `execute_sql` if authorized. Do not
force an unsupported OSPF health question into BGP health.

`execute_sql` with `query` alone returns schema, not an answer. With `sql` it
runs read-only queries and bounds returned rows (`max_rows`, `has_more`).
Mutation returns `requires_approval` without executing; this script cannot
perform an approved write either. Do not replace it with a direct DB connection.
`export_bounded=true` exports only the bounded preview, not the whole source.
Use explicit projections and scope instead of raw config dumps or `SELECT *`.
Resolve named captures from an authorized capture catalog or actual metadata;
`latest` is not a substitute for a user-selected label. Ask if unresolved.
`describe_table` handles unfamiliar views; inspect actual columns instead of
assuming a historical stable schema.

`inspect_devices`, `inspect_interfaces`, `inspect_blast_radius` accept scoped
requests. `inspect_devices` rejects explicit `snapshot_id`;
`inspect_interfaces` supports only `latest` and may choose latest per device,
not a single historical capture. Prefer templates/SQL for historical scope.
Graph impact is recorded connectivity, not forwarding or config
simulation. For text use reporter's `query_evidence` or analyzer's `search_logs`;
read the [evidence contract](../netops/references/evidence_query.md), since
argument names, date scope and paging differ.

## Investigation and file export

For the fixed recorded inventory, interface or BGP investigation, use
`olav-skills run analyzer run_investigation` after reading its
[recipe contract](references/investigation_recipes.md). Supply an exact snapshot
and preserve unknown states, missing views and current-versus-historical
metadata. A recipe reuses tested queries; it does not learn or cache answers.

For a requested file beyond the SQL preview, read the
[full-export contract](references/full_export.md) before using
`olav-skills run analyzer export_query`. Keep the bound source version and
inspect complete/partial/failed status; a saved file does not establish full
source coverage. These entries require separate host authorization.

## Draft changes only when requested

Read [change planning](references/change_planning.md) for fact gathering,
vendor syntax, rollback and the limited bulk generator. Gather endpoint
platforms, interfaces/IPs, relevant links and actual routing process/config;
redundancy/decommission work additionally needs graph impact and its warnings.
An empty regex match does not prove a protocol is unconfigured. Missing facts
remain unknown; do not choose an unverified free port or conflict-free subnet.

Return Markdown with summary, scope, implementation, rollback, verification,
risks and pre-change review. Write a file only via an authorized host file tool,
or report inline if none exists. No framework automatically saves or stops a
plan. A draft does not simulate, validate or apply a network change.
## Plan checks

Version 0.2 provides `olav-skills run analyzer check_change_plan`.
Read the [pre-check contract](references/plan_precheck.md) before supplying a
plan and explicit authorized project/snapshot. Distinguish structured semantic
checks from Markdown structure checks, and preserve unknown source, platform,
address/VRF and capture coverage. Address overlap alone does not prove a
conflict, and a plan remains unapproved even when supported checks pass.
Run it only where the host grants this capability.

## Evidence boundaries

Use a supported bounded template first, one batch for a fleet question. Empty
optional filters discover within authorized scope; limits, clipped cells and
`has_more` / `byte_truncated` still apply. Discovery is not an unlimited export.
Do not infer snapshot membership from inventory or device role from a hostname,
platform, ASN or loopback. Keep unknown roles separate. Missing/unsupported
sources, failed queries and unknown values are not zero or healthy results.
Do not silently drop filters or narrow the user's question.

Cite snapshot and necessary collection-time provenance, including precision,
source and status. `timestamp_without_timezone` is a wall clock, not a known
instant; say the timezone is unknown when citing it. Omit unneeded clocks in
count answers. Recorded data is not live state. Ingest/update time and a
date-looking ID do not prove capture time. If `latest` returns
`ambiguous_snapshot_time`, report the ambiguity and use an explicit authorized
snapshot only when the user's scope supports it; do not order IDs to guess.
An explicit ID can support observations while collection time stays unknown.
Stop once evidence answers the question. Do not repeat unchanged failures or
raise budgets; a corrective retry needs a real corrected argument within scope.
Report unfinished scope and source/preview truncation.

## Native state interpretation

For default IOS/Junos parsing and the shared interface/BGP record semantics,
read [native contracts](../netops/references/native_contracts.md). Keep protocol
families distinct from line states, unknown distinct from nonhealthy, and
current inventory metadata distinct from historical source membership.

## Historical metric baselines

For a selected interface counter's recorded baseline and a separate target
interval, read [metric baselines](references/metric_baselines.md), then use
`analyze_baseline`. Keep source scope, units, capture clocks, minimum samples
and discontinuity checks. Statistics describe observed net deltas per second;
unknown clocks and resets must not become zero. A percentile reference is a
review suggestion, not certification of health or an automatic alert policy.

## OSPF and audit evidence

For neighbor states, read [OSPF analysis](references/ospf_analysis.md), then
use `analyze_ospf`. A single transition cannot prove a stuck session: keep that
unknown in the conclusion itself, not just a later caveat. For an explicit
checklist, read [evidence readiness](references/evidence_readiness.md).
Only call `check_evidence` when both as-of and maximum-age policy are explicit
from the user or an authorized host policy. Missing either means ask first;
never borrow capture time as as-of or invent a default age.
