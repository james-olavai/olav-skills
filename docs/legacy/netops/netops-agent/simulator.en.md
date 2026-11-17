# Simulator

The **simulator** is a Batfish-backed **config-layer evaluator**. It answers
BGP/OSPF compatibility, reachability, route lookup, route-map policy, ACL
search, and differential-reachability questions by running pybatfish against a
live Batfish service. It reasons about **configuration intent**, not live
device state.

!!! note "No SQL, no logs, no live devices"
    The simulator has **no** `execute_sql`, **no** log access, and **no** SSH
    to devices. It reads configuration through Batfish only. For current-state
    SQL questions use the [analyzer](analyzer.md); for fresh device data use
    the [collector](collector.md).

---

## When to use

- "Would BGP come up between R1 and R2 with this config?"
- "Is 10.0.5.0/24 reachable from R3?"
- "What route does R1 pick for 8.8.8.8?"
- "Does this ACL / route-map permit the flow?"
- "What reachability changes between the current and candidate config?"

Typical caller: the [analyzer](analyzer.md) delegates a config-layer what-if
via `task("simulator", "<config question>")`.

---

## Tools

| Tool | When to use |
|------|-------------|
| `batfish_capability` | Pre-flight — which devices Batfish can parse. Call FIRST for a mixed-vendor scope |
| `batfish_q` | Generic Batfish question runner (RAG-driven) — compatibility, reachability, route lookup, policy, ACL, diff |
| `read_change_plan` | Read a plan drafted by the analyzer to evaluate a candidate config |
| `format_and_export` | Render + export the evaluation result to `exports/` |

---

## Example

```bash
olav --agent netops "Would BGP come up if I configured R2 as a route reflector?"
```

The orchestrator routes to the simulator, which calls `batfish_capability` to
confirm the target devices parse, then runs the compatibility question via
`batfish_q` and reports session/route outcomes.

```python
task("simulator", "check reachability from R1 to 10.0.5.0/24 on the current snapshot")
```
