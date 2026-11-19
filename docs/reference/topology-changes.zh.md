# 记录拓扑变化

这个尚未发布的 native 候选支持精确双快照比较：

```sh
printf '%s' '{"snapshot_id_1":"before-id","snapshot_id_2":"after-id"}' | olav-skills run reporter compare_topology
```

显式设置已授权的绝对路径 `OLAV_HOME`。已发布的 0.1.1 不含这个入口。
默认 `source="parsed_neighbors"` 使用历史解析记录，仅支持 Cisco IOS 的精确命令
`show cdp neighbors detail`、`show lldp neighbors detail`。两侧设备/命令父来源范围
须一致。端点包含设备与接口，规范方向和接口缩写，保留并行链路，并比较聚合后的
协议观测集合。不猜主机别名或 IP 归属；LLDP 使用 port ID，不拿任意端口描述当接口。

这些标准模板没有物理链路状态、类型或速率证据，相应字段始终未知。空、缺失、
畸形来源、不支持的平台及歧义重复记录不能当成零或健康。不同设备方向和不同协议的
合法观测可聚合；同一父记录内重复的端点仍保守未知。

显式 `source="topology_links"` 仅比较表中现存快照标签，始终标为 partial。
原生导入按链路键覆盖旧快照标签，不能借此还原完整历史拓扑。可比较实际存在的协议、
状态、类型和速率内容，未知字段不算确定变化。任侧无记录时 counts 为 null。

`limit` 限样本数，默认 20、最多 50；`timeout_ms` 限全操作，默认及最多 15000。
快照 ID 须精确、不同，禁止 latest 等别名。独立受监督进程以锁保护的只读副本读取，
绑定实际来源 hash，限制输入与输出，不写数据库。

`status="success"` 只表示读取完成，`comparison_status` 仍可能 unknown 或 partial。
计数为 observed_added、observed_removed、content_changed、unchanged、content_unknown。
同一链路可同时有确定字段变化与其他未知字段，changed 和 unknown 不能相加当链路总数。
`change_count` 统计确定观测变化；`changes` 还可能展示未知字段。
`changes_more` 表示样本省略；预算超限返回空计数而非伪装完整差异。
采集覆盖、采集时间和实时状态仍未知。观测记录消失不能称为物理断链。
