# Investigate devices, interfaces and coverage

Question: **“How many devices are in this capture, and what can we actually inspect?”** First run the [offline example](../offline-data.md) in a new home. This workflow uses that home's `import.json`, so no guessed snapshot label or model is required.

For questions about the current inventory and downloads in a configured chat, see the [Open WebUI guide](../openwebui.md#count-devices-and-check-role-coverage).

## Establish scope and membership

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"operation":"snapshot_summary","snapshot_id":sid}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer query_batch
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"operation":"devices","snapshot_id":sid,"limit":10}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer query_batch
```

For this fixture, inventory and observed counts are both 2. The exact returned set is R1/R2, with unknown roles. Six records cover five distinct command names. Keep those units separate: six command outputs do not mean six distinct commands or devices.

Check `has_more`, total and response limits before treating returned names as complete. For larger scopes, narrow filters or use supported bounded SQL pagination with stable ordering. The batch API has bounded samples and a `limit`; do not invent an `offset` parameter for it. Equal aggregate counts do not prove equal membership.

## Inspect what was captured

Use `command_coverage` to see captured commands and `multi_device_summary` with the actual device list to expose missing requested devices. These are captured-record questions. `search_commands` searches a possible-command catalog instead and cannot prove a command ran.

For interfaces, `interface_health` uses supported IOS/Junos canonical views and marks status/link/protocol values that are not up as unhealthy. It does not infer missing canonical rows from another table. `bgp_health` uses supported summary sources and normalizes numeric prefix counts or Established as established. OSPF health is not a supported batch operation. Use [topology](topology.md) for relationships and [reporter](../skills/reporter.md) for focused raw evidence.

The native legacy-style `inspect_interfaces` can select a latest snapshot per device; it is unsuitable for claiming a single historical snapshot. Use explicit batch/SQL scope for that question.

## Write a checkable answer

A good fixture answer is: “This imported synthetic snapshot observes R1 and R2 (2 devices); both roles are unknown. It contains six output records covering five command names. This is recorded data, not current network state.” Include the actual snapshot and `temporal.collected_at` source/precision/status from your run, rather than copying a date from the snapshot ID.

For “how many core switches?”, unknown role/class evidence leaves the classification unresolved. Do not promote hostname or platform guesses into a count. For “what is wrong today?”, first establish known collection dates and relevant sources; an older or unknown-time capture cannot answer a live-time question.

## Unreleased counter baseline

For one reviewed device/interface, [metric baselines](../reference/metric-baselines.md)
separate comparable historical intervals and a later target. Use the fixed
analyzer `analyze_baseline` entry, preserving capture clocks, units and
discontinuity gaps. Percentiles of observed net counter deltas are references
for review, not automatic health or alert-policy decisions.

## OSPF and evidence checklist candidate

Use [OSPF analysis](../reference/ospf-analysis.md) for recorded neighbor states
and qualified historical record differences. Before an audit, define the
required device/command pairs and explicit capture-age policy using
[evidence readiness](../reference/evidence-readiness.md). The optional audit
evidence gate can stop rules on missing/unknown evidence; it does not derive
requirements from current inventory or certify the whole network.
