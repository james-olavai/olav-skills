---
name: netops
description: "Navigate recorded-network skills and use shared bounded snapshot/config differences, command search and graph impact helpers. No live collection or device changes."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Netops

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

## Select the capability

| Intent | Read when needed |
|---|---|
| Counts, inventory, protocol health, bounded SQL, draft changes | [analyzer](../analyzer/SKILL.md) |
| Recorded BGP/OSPF/L2 relationships or custom recipes | [topology](../topology/SKILL.md) |
| Investigate evidence, compare snapshots, report or pre-check | [reporter](../reporter/SKILL.md) |
| Import an authorized offline capture | [importer](../importer/SKILL.md) |
| Prepare and validate a missing parser | [learner](../learner/SKILL.md) |
| Render diagrams or polish supplied reports | [writer](../writer/SKILL.md) |

Answer simple supported questions directly with the relevant script. Multi-step
workflows are guidance, not an automatically injected or executed agent graph.
This package cannot collect fresh data, run ping, formally simulate configs or
apply changes. Name the gap if the question requires those capabilities.

Shared scripts use `olav-skills run netops <script>`: `query_batch`,
`diff_snapshots`, `diff_configs`, `inspect_blast_radius`, `read_change_plan`,
`search_commands`. Search commands is a catalog lookup, not command execution.
Read script signatures before uncommon calls; do not invent aliases.

For template arguments, counts and source limits read
[batch query](references/batch_query.md). For scoped recorded-text searches and
continuation read [evidence query](references/evidence_query.md). For host file
and output behavior read [output rules](references/OUTPUT_EXPORT_RULES.md).
For native schema/value discovery read [introspection cache](references/introspection_cache.md).
Cache contents are advisory refresh metadata, not a capture clock or live state.

## Unreleased native diagnostics

When investigating missing data or unsupported analysis, the development
candidate provides `olav-skills doctor --data --project /absolute/project`.
Use an explicit authorized native project; a data check is not a network health
probe or proof of capture completeness. The published 0.1.1 lacks this option.
For content-level changes use reporter's
[recorded state comparison](../reporter/references/state_changes.md), distinct
from identity-key and raw text differences. Use analyzer's
[plan pre-check](../analyzer/references/plan_precheck.md) for deterministic plan
checks. These entry points require the candidate build and actual host execution
permission; they do not add tools to a restricted host.

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

## Unreleased reproducible work

For recorded investigation recipes or full file export, read analyzer's
[recipes](../analyzer/references/investigation_recipes.md) or
[export contract](../analyzer/references/full_export.md). For explicitly managed
parser versions and derived replay, read learner's
[lifecycle contract](../learner/references/parser_lifecycle.md). To bundle
reviewed report/evidence files and check freshness, read reporter's
[evidence delivery contract](../reporter/references/evidence_delivery.md).
These local candidates require their documented scope and host permissions;
published 0.1.1 does not contain them.

## Unreleased native contracts and historical analysis

Read [native state contracts](references/native_contracts.md) for default
IOS/Junos parser coverage and shared query/comparison state semantics. For raw
configuration comparison use [historical config diff](references/config_diff.md)
and two explicit SIDs. Reporter adds
[route changes](../reporter/references/route_changes.md),
[counter deltas](../reporter/references/counter_deltas.md) and
[one-time audit profiles](../reporter/references/audit_profiles.md).
Each contract retains missing scope, capture-time evidence and source bounds;
none grants device execution or background scheduling.
