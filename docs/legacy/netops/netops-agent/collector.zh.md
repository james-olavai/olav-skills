# Collector

**collector** 负责实时网络数据采集。它是唯一连接实时设备的 netops 子 agent：
并行 CLI / Nornir 执行、活性与时延探测、新鲜快照。它的下游与离线导入相同
（`raw_output_store → parsed_outputs → v_*_auto views`）。

---

## 何时使用

- "所有核心路由器都可达吗？"
- "在每台设备上执行 `show ip route`"
- "给 R1 和 R2 打一个新鲜快照"
- "检查全网 OSPF 邻居状态"

编排器把实时采集请求路由到这里，因为它们需要 SSH 连接设备。

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `search_commands` | 预检 —— 发现某平台可用的命令 / 管道规则 |
| `execute_cli_parallel` | 在多台设备上并行执行 show 命令；对照 `commands` 表校验（黑名单命令在连接前被拒） |
| `take_snapshot` | 触发新鲜快照 → `parsed_outputs` + `raw_output_store` |

活性与可达性探测（ping / traceroute / 端口扫描）直接用 `run_shell`。

!!! warning "命令安全"
    在任何 SSH 连接前，`execute_cli_parallel` 会对照平台的 `commands` 表和
    黑名单校验命令。未在白名单中、或命中黑名单模式（如 `reload`、
    `write erase`）的命令，在连接前即被拒绝。

---

## 示例

```bash
olav --agent netops "检查全网 BGP 邻居是否都已建立"
```

编排器委托给 collector，它发现设备名，经 `execute_cli_parallel` 在每台设备上
并行执行 `show ip bgp summary`，返回聚合的逐设备状态。

```python
task("collector", "给 R1 和 R2 打一个新鲜快照")
```
