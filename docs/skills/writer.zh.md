# writer：图表与呈现

Writer 将记录关系或已有邻接表转换为产物。调查由 analyzer/reporter 承担；writer 应保留技术事实，不能添加新事实。文字润色所需的文件编辑/报告保存工具由宿主提供。

## 从限定范围数据库查询绘图

`draw_topology` 一次读取关系并写图。提供 `name_like`（SQL LIKE 模式），或 `center` 配合 `hops`，并指定真实 `snapshot_id`。可选过滤有 `roles`、`sites`；角色未知可能导致过滤不完整。`%core%` 只是名称范围，不证明网络角色。

`protocol` 支持 `l2_cdp`、`bgp`、`ospf`。`diagram_format` 支持 `drawio`、`mermaid`；**SVG 不是 `draw_topology` 输出格式**。可选 `filename` 指定基名，`max_hosts` 限制范围，默认 120。

检查 `status`、实际 `path`、`hosts`、`edges`、`scope` 及格式。未指定范围会拒绝，`too_wide` 要求缩小图。[合成示例](../offline-data.md)使用 `name_like: R%`、真实快照 ID、`max_hosts: 2` 和 Mermaid，验证两节点/一边及真实文件。

## 转换已有授权表格

| 脚本 | 输入 | 输出 |
| --- | --- | --- |
| `render_topology_mermaid` | `adjacencies_table_markdown` | JSON 编码的 Mermaid 代码块字符串或省略说明 |
| `render_topology_drawio` | 同一表格，可选 `device_metadata`、`protocol` | JSON 编码的 XML 字符串或省略说明 |
| `render_topology_svg` | 同一表格，可选 `protocol` | JSON 编码的惰性 SVG 字符串或省略注释 |

保存前解码 JSON 字符串，stdout 不是原始 XML/SVG。转换器不会增加数据库查询。使用来源、来源接口、目标、目标接口和状态等列，与生成的邻接表一致；不要补造缺失链路。

SVG 不接受路径、数据库、网络、metadata 或模板选项。stdin 请求上限 300000 字节，表格文本上限 262144 UTF-8 字节、300 节点、1000 边、每格 256 字符。空、错误或超预算输入返回 `diagram omitted` 注释，不能当成成功图表。

## 产物保留来源

报告中在图旁标明快照、来源和范围。连线记录关系观测，不证明物理布线或包可达性。分享前审阅产物，设备标识可能敏感。对已有文字，IP、AS、设备名、时间、CLI、表格和代码块均应原样保留。另见[拓扑流程](../workflows/topology.md)与[安全](../safety.md)。
