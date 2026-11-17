# Simulator

**simulator** 是 Batfish 驱动的**配置层评估器**。它通过对一个运行中的 Batfish
服务调用 pybatfish，回答 BGP/OSPF 兼容性、可达性、路由查找、route-map 策略、
ACL 检索和差分可达性问题。它推理的是**配置意图**，不是实时设备状态。

!!! note "无 SQL、无日志、无实时设备"
    simulator **没有** `execute_sql`、**没有**日志访问、**没有** SSH 到设备。
    它只通过 Batfish 读取配置。当前状态的 SQL 问题用 [analyzer](analyzer.md)；
    需要新鲜设备数据用 [collector](collector.md)。

---

## 何时使用

- "用这份配置 R1 和 R2 之间的 BGP 会起来吗？"
- "从 R3 能到 10.0.5.0/24 吗？"
- "R1 给 8.8.8.8 选的是哪条路由？"
- "这条 ACL / route-map 放行这个流量吗？"
- "当前配置和候选配置之间可达性有什么变化？"

典型调用方：[analyzer](analyzer.md) 通过
`task("simulator", "<配置问题>")` 委托配置层 what-if。

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `batfish_capability` | 预检 —— Batfish 能解析哪些设备。混合厂商范围时先调它 |
| `batfish_q` | 通用 Batfish 问题运行器（RAG 驱动）—— 兼容性、可达性、路由查找、策略、ACL、diff |
| `read_change_plan` | 读取 analyzer 起草的计划，以评估候选配置 |
| `format_and_export` | 把评估结果渲染并导出到 `exports/` |

---

## 示例

```bash
olav --agent netops "把 R2 配成路由反射器后 BGP 会起来吗？"
```

编排器路由到 simulator，它先调 `batfish_capability` 确认目标设备可解析，再经
`batfish_q` 运行兼容性问题，报告会话/路由结果。

```python
task("simulator", "在当前快照上检查从 R1 到 10.0.5.0/24 的可达性")
```
