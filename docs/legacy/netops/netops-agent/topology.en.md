# Topology

The **topology** sub-agent answers network topology queries and does
LLM-assisted recipe discovery. It ships with four built-in protocols —
**BGP**, **OSPF**, **CDP/LLDP**, and **L2** — and returns a typed
`TopologySnapshot`.

For a protocol it doesn't know yet, declare it in
`~/.olav/config/topology.yaml`; on the first query the agent **drafts a YAML
recipe** and freezes it for reuse. There is no Python-code generation and no
sandbox — a recipe is pure SQL/view declaration.

---

## When to use

- "What connects to R3?"
- "Show the BGP adjacency graph"
- "Shortest path R1 → R4"
- "Build a topology view for a protocol we haven't modeled yet"

---

## Tools

| Tool | When to use |
|------|-------------|
| `query_topology` | Run a topology query → typed `TopologySnapshot` |
| `discover_recipe` | LLM-assisted draft of a YAML recipe for a new protocol |
| `save_recipe` | Freeze a discovered recipe for reuse |
| `rebuild_views` | Pure-SQL view rebuild (no LLM) |
| `list_recipes` | List built-in + saved recipes |

---

## Example

```bash
olav --agent netops "What connects to R3?"
```

The orchestrator routes to topology, which runs `query_topology` and returns
the neighbors of R3 as a typed snapshot. For a not-yet-modeled protocol, the
agent calls `discover_recipe`, drafts a YAML recipe, and `save_recipe` freezes
it so later queries reuse it directly.

```python
task("topology", "show the OSPF adjacency graph")
```
