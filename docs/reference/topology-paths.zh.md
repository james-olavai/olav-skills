# 历史拓扑路径

这是尚未发布的 native 候选，已发布的 `olav-skills==0.1.1` 不含此入口。
先设置获准的绝对路径 `OLAV_HOME`，使用经过审查的候选环境：

```sh
printf '%s' '{"snapshot_id":"capture-a","source_device":"R1","destination_device":"R3"}' | olav-skills run topology inspect_path
```

精确快照选择历史解析的 Cisco IOS CDP/LLDP detail 观测，不使用会覆盖旧标签的
`topology_links` 重建历史。设备名保持精确匹配；接口缩写规范化，并行接口对分别保留，
反向观测及协议集合合并。LLDP 使用接口 port ID，不用端口描述或数字 ifIndex。
其它平台、畸形记录、缺失/空源和重复父记录仍是证据缺口。提取器出现任何来源理由就返回
unknown 且不枚举路径，防止歧义父记录合并成虚构路径。不推断主机名、IP 归属或角色。

结果是无向邻居图上不重复经过设备的候选路径，反向查询会反转端点顺序。
在中间设备连接不同接口不证明 VLAN 连通、转发、路由、ECMP 或故障切换。
同设备请求拒绝处理，不虚构零跳路径。链路状态、速率、类型、采集覆盖、采集时间及
实时状态保持未知。

可选 `source_interface`、`destination_interface` 仅过滤首边和末边；
`max_hops` 默认 8、范围 1–16；`limit` 默认 20、范围 1–50；
`timeout_ms` 默认/最大 15000、最小 1。必须精确 SID，拒绝 latest/current 等别名。
不保证最短、首选或等价路由路径。

检查 `status`、`path_status`、`reasons`、`search_complete`、`paths_more` 和
`search.hop_boundary_reached`。`paths` 仅为发现的候选，`path_count` 仅统计已返回候选，
不表示网络全部路径数。未发现时为 unknown 且计数 null。`search_complete=true` 只表示
在已观测图的跳数和过滤范围内枚举完毕；`source_complete` 始终 false。
`paths_more=true` 表示已看到后续候选但省略，null 表示剩余集合未知。
达到跳数边界时 `paths_more` 也为 null；`search.scope` 明确已观测图/跳数/过滤范围。
缺失观测、限额和枚举结束都不能证明网络没有路径。

Linux 专用子进程受父进程硬 deadline 监督，使用只读加锁 native 副本、源 hash 和最终
身份复核；全部 source context 退出后显式关闭 DuckDB scheduler 才输出，异常退出拒绝。
公开 Python 调用保留调用者 writer 锁和默认连接。硬限为：请求 16 KiB、源 DB 256 MiB、
合计解析记录/父记录 4096、合计 JSON 1 MiB、单父 JSON 256 KiB、图节点 512、边 2048、
扩展 20000 步、路径样本 40 KiB、响应 64 KiB。步数或输出裁剪仅返回已发现候选并标明
不完整；输入或图超限则安全失败。Hash 只绑定字节，不认证来源真实性。
依赖可信本机文件系统/临时根和遵守 DuckDB 锁的写者；warehouse、旧混层图和实时采集
不在本合同内。
