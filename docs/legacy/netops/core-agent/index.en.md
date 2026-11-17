# Core Agent

The Core Agent is the unified default entry point for OLAV — ask a question, get an answer. It handles 80% of daily network queries in one shot, and automatically delegates to specialised sub-agents (writer, ops) when the task requires it.

!!! info "v0.15.0 — Core Agent replaces Quick Agent"
    As of v0.15.0 the former *Quick Agent* workspace has been merged into *Core Agent*. All capabilities are preserved. The `olav` command now routes through `core` by default.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NE-15 | `olav "R1 BGP neighbors?"` returns query results from snapshot data | ✅ v0.10.0 |
    | C-NE-16 | Blacklisted commands are rejected by `execute_cli` | ✅ v0.10.0 |
    | C-NE-17 | `/learn_cmd "show version" --device R1` executes and generates a TextFSM template | ✅ v0.13.1 |
    | C-NE-18 | `take_snapshot` triggers incremental collection with a new snapshot_id | ✅ v0.13.1 |
    | C-NE-19 | Core Agent suggests upgrading to NetOps Agent for complex multi-step analysis | ✅ v0.13.1 |
    | C-V15-01 | `olav "<question>"` routes to core agent by default (no `--agent` flag required) | ✅ v0.15.0 |
    | C-V15-02 | Semantic router selects specialised agent when confidence > 0.85 | ✅ v0.15.0 |

    *Legend: ⚠️ (E2E-gated) = test exists but requires env-var flag, skipped in CI; ⚠️ (manual) = demo-verified only, no automated CI test*

---

## When to Use Core Agent

| Task | Core Agent | NetOps Agent |
|------|:----------:|:---------:|
| "How many BGP peers are down?" | ✅ Yes | Overkill |
| "Show R2 interfaces" | ✅ Yes | Overkill |
| "What changed since yesterday?" | ✅ Possible | Better (use Diff) |
| "Simulate link failure impact" | ❌ No | ✅ Yes (Analysis) |
| "Deploy a test lab" | ❌ No | ✅ Yes (Lab) |

Core Agent handles simple queries by generating SQL against the snapshot database or executing a single CLI command. For anything requiring iteration, comparison, or simulation, it will suggest using the NetOps Agent — or route there automatically if semantic confidence is high.

---

## Query Examples

### Natural Language to SQL

```bash
olav "Are all BGP neighbors established on R1?"
```

OLAV translates this to:

```sql
SELECT neighbor_ip, neighbor_as, state
FROM v_bgp_neighbors_auto
WHERE device_name = 'R1' AND state != 'Established'
```

### Direct SQL

```bash
olav "execute_sql: SELECT device_name, count(*) as cmd_count FROM netops.parsed_outputs GROUP BY 1"
```

### CLI Execution

```bash
olav "Run 'show ip route' on R2"
```

The agent looks up the command in the whitelist, connects via SSH, and returns the output. Blacklisted commands are rejected before any connection is made.

---

## Snapshot Collection

Trigger an on-demand snapshot:

```bash
olav "Take a snapshot of R1 and R2"
```

This runs the collection pipeline for the specified devices, producing a new `snapshot_id`. The data is immediately queryable.

---

## Learning New Commands

When a show command has no TextFSM template, use `/learn_cmd` to teach OLAV:

```bash
olav "/learn_cmd 'show ip bgp community' --device R1"
```

This will:

1. Execute the command on the device via SSH
2. Analyse the output structure
3. Generate a TextFSM template
4. Save it to `workspace/ops/config/templates/custom/`

The template is available for all future collections without restart (hot-reload).

---

## Tools Available

Core Agent has access to these tools (injected from `netops/tools/` via `inject_into_core`):

| Tool | Purpose |
|------|---------|
| `execute_cli` | Run a single CLI command on a device |
| `search_commands` | Find available commands by platform and keyword |
| `diff_configs` | Compare device configs between two snapshots |
| `take_snapshot` | Trigger on-demand collection |

Plus all **core** platform tools (`execute_sql`, `format_and_export`, `olav_recall_memory`, etc.) which are always available.

---

## Escalation

When Core Agent determines a question requires multi-step analysis, it tells you:

> "This question requires comparing multiple data sources across time. I recommend using the NetOps Agent: `olav --agent netops 'your question'`"

This happens for questions involving simulation, drift detection, or multi-device correlation. With v0.15.0 semantic routing enabled, this handoff can happen automatically.
