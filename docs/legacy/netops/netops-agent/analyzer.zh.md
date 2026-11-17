# Analyzer（默认）

**analyzer** 是 netops 的默认子 agent：一个只读的**变更计划起草器**。它通过
SQL 从快照数据库收集设备事实，然后把厂商特定的变更计划 —— 每台设备的 CLI、
回滚、事后检查、风险分级 —— 写到 `exports/change_plans/`。它**对实时设备只读**：
它读 DB，从不连接或配置设备。

analyzer 还兼管两项被合并进来的工作：

- **调查报告（Workflow D）** —— 审计 / "为什么" 的书面分析，导出到
  `exports/reports/`。
- **快照 diff 与漂移**（从已退役的 `ops-diff` 子 agent 吸收）—— 对比两个时间点
  之间的配置、快照、SQL 状态和拓扑。
- **L2 拓扑 what-if** —— 基于图拓扑的影响面分析（`inspect_blast_radius`），
  *不是* Batfish 配置模拟。

!!! note "What-if / 兼容性是另一个子 agent"
    配置层 what-if —— "BGP 会起来吗…"、可达性、route-map 策略 —— 是
    Batfish 驱动的 [simulator](simulator.md)。analyzer 通过
    `task("simulator", "…")` 委托过去。拓扑查询和 recipe 发现是
    [topology](topology.md) 子 agent。

---

## 何时使用

- "规划 / 添加 / 变更 R1 和 R4 之间的一条新 eBGP 会话"
- "写一份调查报告，说明 R3 为什么丢了路由"
- "自昨天的快照以来有什么变化？"（漂移 / diff）
- "R1–R2 链路断了影响面有多大？"

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `describe_table` | 查询前先查看视图/表的列 |
| `inspect_devices` | 结构化设备事实 —— 传 `devices=[]` 可发现全部 |
| `inspect_interfaces` | 目标节点的结构化接口事实 |
| `inspect_blast_radius` | L2 图拓扑 what-if（影响面）—— 非 Batfish |
| `generate_change_plan` | Fat tool —— 一次调用产出多设备 BFS 变更/升级计划 |
| `query_evidence` | 跨 syslog / 命令输出 / 配置的统一文本检索 |
| `diff_configs` | 对比两个快照之间的设备配置 |
| `diff_snapshots` | 对比两个快照（状态 / 拓扑漂移） |
| `format_and_export` | 把计划或报告渲染并导出到 `exports/` |

analyzer 从不持有 `execute_cli` 或任何写工具 —— 它产出*计划*，不实施。

---

## 示例

```bash
olav --agent netops "怎么在 R1 和 R4 之间加一条 eBGP 会话？"
```

编排器路由到 analyzer，它读取相关设备事实（`inspect_devices`、
`query_evidence`），调用 `generate_change_plan`，把 Markdown 计划 —— 当前状态、
每台设备的拟用配置、回滚、事后检查、风险 —— 写到 `exports/change_plans/`。
配置层 what-if 会继续委托出去：

```python
task("simulator", "用这份配置 R1 和 R4 之间的 eBGP 会话会起来吗？")
```
