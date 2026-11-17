# Topology

**topology** 子 agent 回答网络拓扑查询，并做 LLM 辅助的 recipe 发现。它内置四种
协议 —— **BGP**、**OSPF**、**CDP/LLDP**、**L2** —— 并返回一个带类型的
`TopologySnapshot`。

对于尚不认识的协议，在 `~/.olav/config/topology.yaml` 中声明它；首次查询时 agent
会**起草一份 YAML recipe** 并冻结以供复用。没有 Python 代码生成、没有沙箱 ——
recipe 就是纯 SQL/视图声明。

---

## 何时使用

- "什么连到 R3？"
- "显示 BGP 邻接图"
- "R1 → R4 最短路径"
- "为一个尚未建模的协议构建拓扑视图"

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `query_topology` | 运行拓扑查询 → 带类型的 `TopologySnapshot` |
| `discover_recipe` | LLM 辅助起草新协议的 YAML recipe |
| `save_recipe` | 冻结已发现的 recipe 以供复用 |
| `rebuild_views` | 纯 SQL 视图重建（无 LLM） |
| `list_recipes` | 列出内置 + 已保存的 recipe |

---

## 示例

```bash
olav --agent netops "什么连到 R3？"
```

编排器路由到 topology，它运行 `query_topology`，以带类型的快照返回 R3 的邻居。
对于尚未建模的协议，agent 调用 `discover_recipe` 起草一份 YAML recipe，再用
`save_recipe` 冻结，后续查询直接复用。

```python
task("topology", "显示 OSPF 邻接图")
```
