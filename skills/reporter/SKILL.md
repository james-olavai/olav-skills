---
name: reporter
description: "Investigate recorded network evidence, compare snapshots and explain graph impact or pre-check a supplied change plan. Produce sourced findings with explicit gaps."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Reporter

For OSPF neighbor-state questions use analyzer's
[OSPF analysis](../analyzer/references/ospf_analysis.md). For evidence readiness
read [the policy contract](../analyzer/references/evidence_readiness.md): without
both an explicit as-of and maximum age, ask for the missing policy before
calling the checker. Keep unknown verdicts in the main conclusion.

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
Check exit and result status; execution success alone does not prove an answer.

## Investigation

Use `query_batch` for supported summaries, then relevant bounded SQL via
analyzer only where needed. Read [batch query](../netops/references/batch_query.md)
for template semantics, and [evidence query](../netops/references/evidence_query.md)
before `olav-skills run reporter query_evidence` searches. Evidence accepts
`source`, `pattern`, `device`, `snapshot`, `commands`, `ids`, date and paging
keys according to source; analyzer `search_logs` has a different schema.

Start from the actual question and known device names. Read the
[layered troubleshooting guide](references/troubleshoot_layered_l1_to_l4.guide.yaml)
when protocol diagnosis needs L1/link, L2, L3/IGP and overlay correlation.
Consult [routing guidance](../analyzer/references/ROUTING_EXPERT_GUIDE.md) for
protocol reasoning. A hypothesis is not a finding until evidence supports it.
Cross-layer contradictions deserve attention; absent sources stay gaps.

Aim for a small evidence set (roughly eight queries for a focused investigation),
not a mandatory sequence. Stop when the supported verdict is clear. This is a
reasoning guide, not a substitute for deadlines or truncation reporting.
Never repeatedly fetch full config/raw text, repeat deterministic failures or
change filters just to obtain a reassuring answer.

## Comparisons and graph impact

`diff_snapshots` compares returned recorded rows; pin source and target and
report both time provenances. `query_batch`'s `diff_summary` compares identity
keys, not content. `inspect_blast_radius` is available under analyzer/netops;
it models graph connectivity loss, not Batfish, policy or live reachability.
Validate target names and `validation_warnings` before interpreting counts.

For content-level changes, the unreleased candidate adds
`olav-skills run reporter compare_state`; read its
[scope and source contract](references/state_changes.md) first. Pin both
snapshot IDs and the supported source. Historical per-command views may hide
older records; this comparison uses its documented historical source instead.
Record changes and missing coverage do not prove network outages.

## Supplied-plan pre-check

The unreleased candidate provides analyzer's deterministic
`olav-skills run analyzer check_change_plan`; read the
[pre-check contract](../analyzer/references/plan_precheck.md) for structured
inputs versus Markdown-only checks. Preserve each pass/fail/unknown finding and
its source; a completed check is not approval to execute.

`read_change_plan` reads a plan within its supported exports path. Inspect its
actual arguments before use. Compare claimed devices, interfaces, IP/subnets
and protocol against bounded captured evidence; graph impact is optional when
relevant. Report `PRE-CHECK CONCERNS` for conflicts or missing evidence, and a
qualified `PRE-CHECK PASS` only for checks actually completed. A captured-state
sanity check is not formal config verification or approval to execute.

## Deliver the report

Include conclusion, scope/method, evidence-backed findings, severity, risks and
recommendations, and source gaps. Each finding cites device/object, observed
value, source and snapshot/time. Use a concise evidence table; preserve source
IDs and truncation/date exclusions. Distinguish inference from observation.
Return Markdown directly, or use an authorized host writer and state the actual
file path. For selected reviewed files, read
[evidence delivery](references/evidence_delivery.md) before `evidence_bundle`.
It packages/validates integrity and freshness, not report truth or authorship.
An authentic manifest pin needs a separate trusted channel. Read existing files
before editing; there is no automatic append pipeline.

## Unreleased historical analysis and audits

Use `compare_routes` after reading [route changes](references/route_changes.md)
for supported source profiles, verified routing context and multipath semantics.
A missing route record does not prove withdrawal or reachability loss.
Use `compare_counters` with [counter deltas](references/counter_deltas.md):
unknown clocks, resets and wraps must not become invented rates or zero errors.
For raw configuration text use netops `diff_configs` under its
[historical source contract](../netops/references/config_diff.md).

`run_audit` applies a reviewed one-time profile; read
[audit profiles](references/audit_profiles.md). Preserve per-rule concern,
unknown and not-executed states. Partial execution leaves the overall result
unknown. A pass is limited to the selected observed check, not network health.
These local candidates are not in published 0.1.1 or automatically authorized
as WebUI tools.

## Unreleased report review and topology changes

For a report with explicit evidence bindings, use `lint_report` after reading
[report citation checks](references/report_citations.md). Check coverage and
binding failures; passing consistency checks does not establish that a claim is
true or that a caller-provided source is authentic.

Use `compare_topology` with [topology content changes](references/topology_changes.md)
for two exact recorded snapshot labels. Preserve parallel links and unknown
fields. Added or removed observations do not establish physical link up/down;
missing collection evidence is not a complete empty topology.

For OSPF observations use analyzer's [OSPF analysis](../analyzer/references/ospf_analysis.md).
`run_audit` adds an `ospf` profile and an optional explicit evidence gate; read
[audit profiles](references/audit_profiles.md) for scope and time policy.

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
