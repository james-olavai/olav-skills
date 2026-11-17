# NetworkModel（NoM）

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NOM-01 | `load_network_model()` 构造时零 DuckDB I/O（lazy） | ✅ v0.18.1 R52 |
    | C-NOM-02 | `model.physical` 把 `netops.topology_links` 封装为 `networkx.Graph` | ✅ v0.18.1 R52 |
    | C-NOM-03 | `model.l2` 从 `parsed_outputs` 产 VLAN 成员 | ✅ v0.18.1 R55 |
    | C-NOM-04 | `model.l3.ospf` / `model.l3.bgp` 邻居图 | ✅ v0.18.1 R53/R54 |
    | C-NOM-05 | `model.l4` 提取 Cisco route-map 子句（只读） | ✅ v0.18.1 R56 |
    | C-NOM-06 | `model.l4.walk(device, policy, matches)` 确定性走法 | ✅ v0.18.1 R58 |
    | C-NOM-07 | `model.l4.policy(device, neighbor, direction)` 端到端求值 | ✅ v0.18.1 R59 |
    | C-NOM-08 | `sandbox_prologue` 把 `model` 自动注入 `execute_in_sandbox` 运行（ARCH-14）| ⚠️ 仅基础设施 |

---

## 概述

Network Object Model（NoM）是当前快照的分层惰性视图。构造几乎无成本；
每层在首次属性访问时 materialize 并缓存到实例。

```python
from olav_netops.sim import load_network_model

m = load_network_model()            # 不碰 DB
m.physical.graph                    # 触发 topology_links 读
m.physical.neighbors("R1")          # 上一行已缓存
```

!!! note "没有 `run_python_simulation` agent 工具"
    不存在 `run_python_simulation` 工具（从未交付）。`model` 自动注入发生在
    `execute_in_sandbox` 内、由 `sandbox_prologue`（ARCH-14）完成 —— 目前只有
    `learner` 子 agent 走这条路径，不是通用 agent 沙箱。自己写脚本时直接用
    上面的 `load_network_model()` 构造。

在 `sandbox_prologue` 运行的地方，同一对象已预绑定 — 直接用 `model.*`：

```python
# 沙箱内 — 无需 import
diameter = nx.diameter(model.physical.graph) if model.physical.graph else None
_result = {"diameter": diameter}
```

`olav_netops` 未安装（平台最小装）时 `model = None`，用户代码用
`if model:` 守门即可。

---

## 层

| 属性 | 数据源 | 形状 | 引入 |
|---|---|---|---|
| `model.physical` | `netops.topology_links` | `PhysicalLayer`（links + networkx.Graph）| R52 |
| `model.l2` | `parsed_outputs` WHERE `command ILIKE '%vlan%'` | `L2Layer`（vlans + graph） | R55 |
| `model.l3.ospf` | `parsed_outputs` WHERE `command ILIKE '%ospf neighbor%'` | `OspfLayer`（adjacencies + graph） | R53 |
| `model.l3.bgp` | `parsed_outputs` WHERE `command ILIKE '%bgp summary%'` | `BgpLayer`（sessions + graph） | R54 |
| `model.l4` | `raw_output_store` WHERE `command ILIKE '%running-config%'` | `L4Layer`（clauses + neighbor_policies） | R56 + R59 |

所有层容忍 vendor 字段大小写变体（Cisco `neighbor_id` / Junos `peer` /
大写变体），目标快照或 DB 缺失时优雅降级为空集合 — 访问时不抛异常。

---

## 典型查询

### L1 — 哪些设备与 R1 相邻？

```python
m = load_network_model()
m.physical.neighbors("R1")      # → ['R2', 'R3']
m.physical.devices()            # → 拓扑里全部 hostname
```

### L3 — OSPF / BGP 健康聚合

```python
m.l3.ospf.unhealthy()           # 非 FULL/2WAY 的邻接
m.l3.bgp.unhealthy()            # 非 Established 的 session
                                # （Cisco 数字 prefix count 视为健康）
```

### L4 — 评估一个 BGP peer 的 policy

```python
# R1 向 10.0.12.2 导出 — 这个 prefix 能过吗？
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

当该 peer/direction 无 route-map 绑定时，返回 `unbound=True`、
`action="permit"`（Cisco 默认 permit-all）。

### L4 — LLM-backed matcher 的确定性 walk

```python
def _match(cond: str) -> bool:
    # 问 LLM："10.1.0.0/16 是否落在 prefix-list CUSTOMERS 里？"
    return llm_lookup(cond)

m.l4.walk("R1", "EXPORT_CUSTOMERS", matches=_match)
```

`matches` 接受：

* `dict[str, bool]` — 条件→布尔的显式映射
* `callable(cond_str) -> bool` — LLM / 外部 eval 的插入点
* `None` — 所有条件视为 True（what-if 假设）

---

## 公开 API

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

| 入口 | 用途 |
|---|---|
| `load_network_model(snapshot=None, scope=None, db_path=None)` | 公开入口。`snapshot=None` 取 `MAX(snapshot_id)`；`scope=[hosts]` 限制层到这些设备；`db_path` 默认 `MAIN_DB_PATH`。 |
| `NetworkModel.physical` / `.l2` / `.l3.ospf` / `.l3.bgp` / `.l4` | 惰性缓存层访问器。 |
| `NetworkModel.devices` | scope 里的 hostname（或 physical 拓扑全部节点）。 |
| `L4Layer.walk(device, policy_name, matches)` | 确定性子句 walker。 |
| `L4Layer.policy(device, neighbor, direction, matches=None)` | 端到端求值（BGP neighbor → route-map 解析）。 |
| `L4Layer.policy_for_neighbor(device, ip, direction)` | 返回 policy name 或 None。 |

---

## 分阶段履历

| Round | 交付 |
|---|---|
| R52 | 包骨架 + L1 physical + L2/L3/L4 stub 契约 |
| R53 | `OspfLayer` |
| R54 | `BgpLayer` |
| R55 | `L2Layer` |
| R56 | `L4Layer` 子句提取（Cisco route-map stanza，只读） |
| R57 | 沙箱 auto-inject `model = load_network_model()` |
| R58 | `L4Layer.walk()` 确定性 policy walker |
| R59 | `L4Layer.policy()` 端到端 + BGP neighbor→route-map 绑定 |

外部 P3+ 扩展（Junos policy-statement parser、prefix-list / community-list
内容、multi-AF scoping）是 vendor 扩展 track，不属 `dev_docs/00. issues.md::
ISSUE-ARCH-14` 声明的 P1-P3 核心 scope。
