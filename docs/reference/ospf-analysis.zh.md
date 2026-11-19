# OSPF 观测状态与历史记录比较（未发布）

用户问持续卡住或故障时，单次过渡态不足的结论须在主句表达，再列观测；不先肯定卡住再用末尾免责声明收回。无持续时间或已证身份时不排名MTU等根因，未查询历史不等于只有一份记录。命名采集应从授权目录或实际元数据解析，不能用latest替代。

入口为 `olav-skills run analyzer analyze_ospf`，stdin 一个 JSON 对象。
这是本地开发候选，已发布的 0.1.1 不含此入口；下一版本仍须单独验收并取得宿主授权。
固定 `OLAV_HOME` 为已授权的绝对项目路径；只支持 Linux native DuckDB，不连接设备或 warehouse。

```json
{"mode":"inspect","snapshot_id":"capture-a","devices":["R1"],"vendors":["cisco_ios"],"limit":20,"timeout_ms":15000}
```

比较两个精确历史 SID：

```json
{"mode":"compare","snapshot_id_1":"capture-a","snapshot_id_2":"capture-b","devices":["R1"]}
```

`mode` 默认 `inspect`。SID 大小写精确匹配，拒绝 latest/current/newest/all 和相同双 SID。
`devices` 是精确设备名列表，`vendors` 只接受 `cisco_ios`、`juniper_junos`；
两者默认空列表代表不筛选，最多64个且不能重复，实际请求回显为排序列表。
`limit` 默认20、最大50，分别约束观测/变化样本；`timeout_ms` 默认15000、范围1–15000。
拒绝未知键、JSON 重复键、布尔数值、非有限数及重复筛选值。
请求不能补充缺失的 VRF、进程、区域、网络类型或本地角色。

## 支持的来源与未知门

只读取历史 `netops.parsed_outputs` 中 IOS 的精确命令 `show ip ospf neighbor`
和 Junos 的 `show ospf neighbor`，支持 stock IPv4 六字段：
`neighbor_id`、`ip_address`、`interface`、`state`、`priority`、`dead_time`。
非空路由上下文或额外解析字段不在固定 profile 内，返回未知，不静默丢弃。
空/缺源、非法邻居身份/状态、重复父记录或重复规范身份均保留原因。
部分已知观测可展示，但不能当成完整来源或健康的零；指定设备缺对应来源也保持未知。
平台筛选不证明该厂商采集完整，更不证明整个快照设备集合。

记录键为设备、规范平台、精确命令、规范接口、IPv4邻居地址及IPv4邻居ID。
stock 结果没有 VRF、进程或区域，因此 `routing_context` 保留 null，并注明
`not_exposed_by_stock_profile`。命令字符串不能证明默认 VRF 或单进程。
采集时间、覆盖范围和实时状态均未知；SID 和当前数据库 hash 都不是采集时钟。

## 状态语义与比较

结果 schema 为 `olav.ospf-analysis/v1`；先核对 `status`、来源原因、
`effective_request` 和 native `source_version.sha256`。

- `full_observed`：只表示记录中已观测 Full 建立，不证明全网健康。
- `two_way_unknown`：缺网络类型及本地/邻居角色上下文时，2-Way 不能笼统判故障；
  邻居的 DR/BDR 后缀也不补成本地角色。
- `transitional_observed`：Down、Attempt、Init、ExStart、Exchange、Loading，
  只是待查的单次状态，不证明持续卡死、持续时间、MTU 或其它根因。
- `unknown`：未识别状态。所有类别的 `health` 始终为 `unknown`。

单 SID 的 `summary` 包含 `record_count` 和上述四个互斥计数，四项和等于记录数。
无可用记录时 summary/记录数为 null，不当健康零。`observations_more` 表示样本截断；
来源有原因时即使留有已知观测，`analysis_status` 仍为 unknown。

比较两 SID 共用一次固定的数据库副本。只有两边来源均可用且记录键唯一时，
返回 `comparison_status=partial`、`comparison_scope=observed_records_only`，
计数为 `observed_record_added/removed/state_changed/unchanged`；
`changes` 是变化记录的前后样本，`changes_more` 不影响完整的范围内计数。
状态比较包含邻居角色后缀。任何一边重复、歧义、缺源或不支持都抑制整个比较：
unknown、null counts、无变化样本。
`routing_identity` 和 `adjacency_continuity` 始终未知。相同记录键不认证真实会话连续，
记录增减不称物理故障、恢复、连续在线或邻居确实不存在。

## 读取边界

请求16KiB、响应64KiB、源库256MiB；最多4096父记录/解析记录，单父解析JSON256KiB、
合计1MiB。超预算显式失败，样本不是全源导出；平台筛选发生在有界读取之后。
父进程以清洁环境、固定包和 cwd、硬时限管理专用 exec reader 与私有临时副本。
reader 使用 native OFD 锁、只读查询、最终源复核；所有上下文退出后，先关闭其自身
DuckDB 默认 scheduler，再发布 JSON。不会关闭调用方连接或 writer 锁。
非零退出、错误 JSON、请求/来源绑定不一致及响应超限均不发布成功；非零退出只回传
数字 `worker_exit_code`。这些检查不认证源真实性。

Linux专用worker在首次导入DuckDB公共模块前初始化单线程默认内存连接，验源后正常关闭并退出。初始化只允许真实worker exec入口，不替换Python调用者或已加载的连接；必要API和实际设置不符时拒绝执行。验证范围为DuckDB 1.5.6，不代表后续版本或包内所有native reader均已验收。

顶层assessment明确持续性与根因unknown、confirmed_stuck_count为null；空数据、全Full或两次相同过渡态都不能据此统计卡住数量，null不是零。主句与总结须保持同一资格。时钟精度只适用于对应来源，目录Z不可被另一naive字段污染。
