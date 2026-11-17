# Analyzer (default)

The **analyzer** is the default netops sub-agent: a read-only **change-plan
drafter**. It gathers device facts from the snapshot database via SQL, then
writes a vendor-specific change plan — CLI per device, rollback, post-checks,
and a risk classification — to `exports/change_plans/`. It is **read-only on
live devices**: it reads the DB, it never connects to or configures a device.

The analyzer also owns two other jobs that were folded into it:

- **Investigation reports (Workflow D)** — an audit / "why" write-up, exported
  to `exports/reports/`.
- **Snapshot diff & drift** (absorbed from the retired `ops-diff` sub-agent) —
  compare configs, snapshots, SQL state, and topology between two points in time.
- **L2 topology what-if** — blast-radius over the graph topology
  (`inspect_blast_radius`), *not* a Batfish config simulation.

!!! note "What-if / compatibility is a different sub-agent"
    Config-layer what-if — "would BGP come up if…", reachability, route-map
    policy — is the Batfish-backed [simulator](simulator.md). The analyzer
    delegates there via `task("simulator", "…")`. Topology queries and recipe
    discovery are the [topology](topology.md) sub-agent.

---

## When to use

- "plan / add / change / modify / 变更 a new eBGP session between R1 and R4"
- "write an investigation report on why R3 lost its routes"
- "what changed since yesterday's snapshot?" (drift / diff)
- "what's the blast radius if the R1–R2 link goes down?"

---

## Tools

| Tool | When to use |
|------|-------------|
| `describe_table` | Inspect a view/table's columns before querying |
| `inspect_devices` | Structured device facts — pass `devices=[]` to discover all |
| `inspect_interfaces` | Structured interface facts for the target nodes |
| `inspect_blast_radius` | L2 graph-topology what-if (blast radius) — not Batfish |
| `generate_change_plan` | Fat tool — full multi-device BFS change/upgrade plan in ONE call |
| `query_evidence` | Unified text search across syslog / command output / config |
| `diff_configs` | Compare device configs between two snapshots |
| `diff_snapshots` | Compare two snapshots (state / topology drift) |
| `format_and_export` | Render + export the plan or report to `exports/` |

The analyzer never holds `execute_cli` or any write tool — it produces a
*plan*, it does not apply it.

---

## Example

```bash
olav --agent netops "How do I add an eBGP session between R1 and R4?"
```

The orchestrator routes to the analyzer, which reads the relevant device
facts (`inspect_devices`, `query_evidence`), calls `generate_change_plan`, and
writes a Markdown plan — current state, proposed config per device, rollback,
post-checks, and risk — to `exports/change_plans/`. A config-layer what-if is
delegated onward:

```python
task("simulator", "would the eBGP session between R1 and R4 come up with this config?")
```
