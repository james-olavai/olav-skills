# Collector

The **collector** performs live network data collection. It is the only netops
sub-agent that connects to live devices: parallel CLI / Nornir execution,
liveness and latency probing, and fresh snapshots. It feeds the same downstream
as offline ingest (`raw_output_store → parsed_outputs → v_*_auto views`).

---

## When to use

- "Are all core routers reachable?"
- "Run `show ip route` on every device"
- "Take a fresh snapshot of R1 and R2"
- "Check OSPF neighbor state across the network"

The orchestrator routes live-collection requests here because they require SSH
connections to devices.

---

## Tools

| Tool | When to use |
|------|-------------|
| `search_commands` | Pre-flight — discover available commands / pipe rules for a platform |
| `execute_cli_parallel` | Run a show command across devices in parallel; validates against the `commands` table (blacklisted commands rejected before connecting) |
| `take_snapshot` | Trigger a fresh snapshot → `parsed_outputs` + `raw_output_store` |

Liveness and reachability probing (ping / traceroute / port scan) uses
`run_shell` directly.

!!! warning "Command safety"
    Before any SSH connection, `execute_cli_parallel` validates the command
    against the platform's `commands` table and the blacklist. Commands not
    whitelisted, or matching a blacklist pattern (e.g. `reload`, `write
    erase`), are rejected before connecting.

---

## Example

```bash
olav --agent netops "Check if all BGP neighbors are established across the network"
```

The orchestrator delegates to the collector, which discovers device names,
runs `show ip bgp summary` on each in parallel via `execute_cli_parallel`, and
returns aggregated per-device status.

```python
task("collector", "take a fresh snapshot of R1 and R2")
```
