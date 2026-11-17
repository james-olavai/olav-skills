# NetworkModel (NoM)

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NOM-01 | `load_network_model()` constructs with zero DuckDB I/O (lazy) | ✅ v0.18.1 R52 |
    | C-NOM-02 | `model.physical` wraps `netops.topology_links` as `networkx.Graph` | ✅ v0.18.1 R52 |
    | C-NOM-03 | `model.l2` returns VLAN memberships from `parsed_outputs` | ✅ v0.18.1 R55 |
    | C-NOM-04 | `model.l3.ospf` / `model.l3.bgp` return adjacency graphs | ✅ v0.18.1 R53/R54 |
    | C-NOM-05 | `model.l4` extracts Cisco route-map clauses (read-only) | ✅ v0.18.1 R56 |
    | C-NOM-06 | `model.l4.walk(device, policy, matches)` deterministic walker | ✅ v0.18.1 R58 |
    | C-NOM-07 | `model.l4.policy(device, neighbor, direction)` end-to-end evaluator | ✅ v0.18.1 R59 |
    | C-NOM-08 | `sandbox_prologue` auto-injects `model` into `execute_in_sandbox` runs (ARCH-14) | ⚠️ infra-only |

---

## Overview

The Network Object Model (NoM) is a lazy, layered view of the current
snapshot. Construction is essentially free; each layer materialises on
first attribute access and caches on the instance.

```python
from olav_netops.sim import load_network_model

m = load_network_model()            # no DB touch
m.physical.graph                    # triggers topology_links read
m.physical.neighbors("R1")          # cached from previous line
```

!!! note "No `run_python_simulation` agent tool"
    There is no `run_python_simulation` tool (it was never shipped). The
    `model` auto-injection happens inside `execute_in_sandbox` via
    `sandbox_prologue` (ARCH-14) — today that path is exercised by the
    `learner` sub-agent, not a general agent sandbox. In your own scripts,
    construct it directly with `load_network_model()` as shown above.

Where `sandbox_prologue` runs, the same object is pre-bound — just reach
for `model.*`:

```python
# Inside the sandbox — no import needed
diameter = nx.diameter(model.physical.graph) if model.physical.graph else None
_result = {"diameter": diameter}
```

When `olav_netops` isn't installed (platform-only minimal install),
`model` is `None` so user code can guard with `if model:`.

---

## Layers

| Attribute | Source | Shape | Round |
|---|---|---|---|
| `model.physical` | `netops.topology_links` | `PhysicalLayer` (links + networkx.Graph) | R52 |
| `model.l2` | `parsed_outputs` where `command ILIKE '%vlan%'` | `L2Layer` (vlans + graph) | R55 |
| `model.l3.ospf` | `parsed_outputs` where `command ILIKE '%ospf neighbor%'` | `OspfLayer` (adjacencies + graph) | R53 |
| `model.l3.bgp` | `parsed_outputs` where `command ILIKE '%bgp summary%'` | `BgpLayer` (sessions + graph) | R54 |
| `model.l4` | `raw_output_store` where `command ILIKE '%running-config%'` | `L4Layer` (clauses + neighbor_policies) | R56 + R59 |

All layers tolerate vendor field-case variation (Cisco `neighbor_id` /
Junos `peer` / uppercase variants) and degrade gracefully to empty
collections when the target snapshot or DB is absent — never raises at
access time.

---

## Typical queries

### L1 — which devices neighbour R1?

```python
m = load_network_model()
m.physical.neighbors("R1")      # → ['R2', 'R3']
m.physical.devices()            # → all hostnames in topology
```

### L3 — OSPF / BGP health roll-up

```python
m.l3.ospf.unhealthy()           # adjacencies NOT FULL/2WAY
m.l3.bgp.unhealthy()            # sessions NOT Established (numeric
                                # prefix counts treated as healthy)
```

### L4 — evaluate a policy against a BGP peer

```python
# R1 exports to 10.0.12.2 — will this prefix pass?
m.l4.policy(
    device="R1",
    neighbor="10.0.12.2",
    direction="out",
    matches={"ip address prefix-list CUST_PREFIXES": True},
)
# → {
#     "action": "permit",
#     "policy_name": "EXPORT_CUSTOMERS",
#     "sets": ["local-preference 200"],
#     "trace": [{seq: 10, action: "permit", matched: True, ...}],
#     "unbound": False,
# }
```

When no route-map is bound to that peer/direction, the result carries
`unbound=True` and `action="permit"` (Cisco's permit-all default).

### L4 — deterministic walk with an LLM-backed matcher

```python
def _match(cond: str) -> bool:
    # Ask an LLM: "does 10.1.0.0/16 fall in prefix-list CUSTOMERS?"
    return llm_lookup(cond)

m.l4.walk("R1", "EXPORT_CUSTOMERS", matches=_match)
```

`matches` accepts:

* `dict[str, bool]` — explicit per-condition map
* `callable(cond_str) -> bool` — plug-in point for LLM / external eval
* `None` — treat every condition as True (what-if "all match")

---

## Public API

```python
from olav_netops.sim import (
    load_network_model,
    NetworkModel,
    PhysicalLayer,
    L2Layer,
    OspfLayer,
    BgpLayer,
    L4Layer,
)
```

| Entry | Purpose |
|---|---|
| `load_network_model(snapshot=None, scope=None, db_path=None)` | Public entry point. `snapshot=None` picks `MAX(snapshot_id)`; `scope=[hosts]` restricts per-layer to those devices; `db_path` defaults to `MAIN_DB_PATH`. |
| `NetworkModel.physical` / `.l2` / `.l3.ospf` / `.l3.bgp` / `.l4` | Lazy-cached layer accessors. |
| `NetworkModel.devices` | Hostnames in scope (or all nodes of physical topology). |
| `L4Layer.walk(device, policy_name, matches)` | Deterministic clause walker. |
| `L4Layer.policy(device, neighbor, direction, matches=None)` | End-to-end eval with BGP neighbor → route-map resolution. |
| `L4Layer.policy_for_neighbor(device, ip, direction)` | Returns policy name string or None. |

---

## Staging history

| Round | Delivered |
|---|---|
| R52 | Package skeleton + L1 physical + stub contract for L2/L3/L4 |
| R53 | `OspfLayer` |
| R54 | `BgpLayer` |
| R55 | `L2Layer` |
| R56 | `L4Layer` clause extraction (Cisco route-map stanzas, read-only) |
| R57 | Sandbox auto-inject `model = load_network_model()` |
| R58 | `L4Layer.walk()` deterministic policy walker |
| R59 | `L4Layer.policy()` end-to-end + BGP neighbor→route-map binding |

The external P3-plus expansion (Junos policy-statement parser, prefix-
list / community-list contents, multi-AF scoping) is a documented
vendor-expansion track that falls outside the P1-P3 scope declared in
`dev_docs/00. issues.md::ISSUE-ARCH-14`.
