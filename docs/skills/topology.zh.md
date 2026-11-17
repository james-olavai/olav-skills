# topology：已记录的网络关系

Topology 用于 BGP 会话、OSPF 邻接和由 CDP/LLDP 推导的二层链路。关系来自采集命令输出，不证明实时连通、转发策略或端到端可达性。

## 查询显式快照

`query_topology` 接受 `concept`（`bgp`、`ospf`、`l2`、`all`）及 `snapshot_id`。使用导入返回的真实 ID。[离线指南](../offline-data.md)演示从 `import.json` 安全编码为 stdin JSON。

结果包含所选 `snapshot_id`、`bgp_sessions`、`ospf_adjacencies`、`l2_links` 和 `absent_layers`。解释空列表前先看来源缺失。合成示例有二层链路，没有 OSPF 来源视图，因此可以说“本采集没有可用 OSPF 数据”，不能说“没有 OSPF 邻居”。

BGP 数字前缀数可归一化为 `Established`；OSPF 的 `FULL/BDR` 等角色后缀保留。可选 area 数据缺失时可为 null。原生内置关系依赖支持的规范来源视图；其它厂商存在解析记录，不自动证明内置关系支持，应核对实际覆盖。

拓扑默认选择器有自己的二层优先及 ID 排序逻辑，与有界 batch 基于采集时间的保守解析不同。使用显式 ID，才能让关系与图表选中同一快照。

## 通过审阅 recipe 扩展视图

| 步骤 | 脚本与关键输入 |
| --- | --- |
| 检查覆盖 | `list_recipes`，可选 `concept`；识别 `table_present: false` |
| 收集草拟材料 | `prepare_recipe`，传 `protocol` 和 `vendor` |
| 审阅声明式 YAML | 映射值必须是实际样本字段 |
| 校验并保存 | `save_recipe`，参数是 **`recipe_yaml`**，不是 `yaml_text` |
| 重建派生视图 | `rebuild_views`，可选 `concept` |

`prepare_recipe` 返回候选、解析样本和草拟说明，不调用模型也不写 recipe。`save_recipe` 校验、试跑并保存；它和 `rebuild_views` 都是写操作。没有弄清零行原因前不要强制保存。快照缺来源命令时可能返回 skipped。

自定义 concept 应通过生成的视图使用审阅过的有界 SQL 查询。内置 `query_topology` 只理解上述四个概念；传自定义名可能看似空结果，不能用来验证覆盖。图表见 [writer](writer.md)，完整查询到产物流程见[拓扑工作流](../workflows/topology.md)。
