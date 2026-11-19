# topology：已记录的网络关系

Topology 用于 BGP 会话、OSPF 邻接和由 CDP/LLDP 推导的二层链路。关系来自采集命令输出，不证明实时连通、转发策略或端到端可达性。

## 查询显式快照

`query_topology` 接受 `concept`（`bgp`、`ospf`、`l2`、`all`）及 `snapshot_id`。使用导入返回的真实 ID。[离线指南](../offline-data.md)演示从 `import.json` 安全编码为 stdin JSON。

原生结果新增 `result_contract`（schema=`olav.topology-result/v1`、backend=`native`），包含有效 concept、按 bgp/ospf/l2 排序的请求层及实际解析的快照 ID。各层区分 `not_requested`（未检查，count=null）、`unavailable`（无所需视图或无可用快照，count=null）、`empty`（视图可用，所选范围返回零行）及 `rows`（实际返回正数行）。

本版 `collection_coverage` 固定为 `unknown`。视图是数据库全局对象，不能证明所选快照采集过命令；零行也不证明没有协议或会话。`absent_layers` 只列出已请求但缺所需视图的层，兼容数组本身不能表达请求范围。无可解析快照时外层 ID 为 `none`、合同 ID 为 null，请求层为 `unavailable`、reason=`snapshot_unavailable`，不声称检查过视图。旧 warehouse 后端不在该原生合同范围内。

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

自定义 concept 应通过生成的视图使用审阅过的有界 SQL 查询。内置 `query_topology` 只理解上述四个概念；原生路径会拒绝自定义名，不再返回成功空数组。图表见 [writer](writer.md)，完整查询到产物流程见[拓扑工作流](../workflows/topology.md)。
