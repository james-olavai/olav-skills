# 观测状态内容比较

这是尚未发布的源码候选。已发布的 PyPI `olav-skills==0.1.1` 不含
`compare_state`；以下入口须由后续经过验证的新版本提供。

显式绑定获授权项目的 `OLAV_HOME`，固定两个快照 ID：

```sh
printf '%s' '{"source":"bgp_ios","snapshot_id_1":"before","snapshot_id_2":"after","limit":20}' | olav-skills run reporter compare_state
```

入口只读比较已记录的结构化观测，不执行设备变更，不新增 Open WebUI 工具权限或
自然语言路由。既有键投影 `diff_summary`、`diff_snapshots` 语义不变。

## 实际支持范围

| source | 固定来源 | 身份 | 内容字段 |
| --- | --- | --- | --- |
| bgp_ios | parsed_outputs 的精确命令 show ip bgp summary | device_name、bgp_neighbor、vrf、afi | state_or_prefixes_received 规范为 state |
| bgp_junos | 精确命令 show bgp summary | device、peer_ip或兼容bgp_neighbor、vrf、afi | 原生state文本或明确state_or_prefixes_received形态 |
| interfaces_ios | 精确命令 show ip interface brief | device_name、interface、vrf、afi | status、proto、ip_address |
| interfaces_junos | 精确命令 show interfaces terse | 同上 | link_state映射status、admin_state、proto协议族、ip_address |
| l2 | netops.v_l2_links_auto | 源设备/接口、目的设备/接口、discovery_protocol | link_status |
| routes | 不支持；缺可靠 VRF/AFI/多路径合同 | 不猜旧 routes 表 | 未支持 next-hop 或 AS_PATH |

命令数据来自 `netops.parsed_outputs` 的历史 JSON，不读原始命令/配置文本。
每条 parent 必须是对象数组，记录须含表内所列标量字段；缺字段、复杂对象、无效 JSON
或 parent null 明确失败。字段显式 null 仍是未知。旧Junos admin_state缺失保持未知；vrf/afi也可以缺失，返回 null，
不能补成默认 VRF、IPv4 或全局作用域。不同命令按 source 分开，不悄悄合并。
输出 `scope.canonical_binding` 绑定实际命令和字段合同。

状态解释遵循[原生合同](native-contracts.md)。仅state_or_prefixes_received形态的
非负整数前缀数表示Established；Junos原生state按已知文本状态解释。未知状态不冒正常，
前缀数量变化不算会话状态变化；Junos admin/link与proto协议族分别处理。

现有逐命令自动视图会隐藏非 latest 历史，故本入口直接读取显式 SID 的已解析记录。
两个 SID 都须存在于 parsed evidence；拒绝 latest、空值和不存在 ID，不按字符串或
日期标签推断最新/时间。collection_time 与 coverage 明确未知。

## 结果与边界

结果 schema 为 `olav.state-diff/v1`。status success 只证明执行完成；diff_status 为
observed_changes、no_observed_changes 或 unknown，不代表网络健康或上线许可。
scope 保留来源、两个 SID 与精确 device 过滤。L2 的 device 只过滤报告端 source_device。

counts 中 added/removed/changed/unchanged 是该来源过滤后的观测记录准确数量，
total_changes 为前三项之和，独立于 sample。value_unknown 与它们重叠：任一侧内容
为 null 的合并记录均计入，包括新增/消失记录不存在的另一侧，不能重复相加。
NULL→值仍可显示字面变化，但 certainty 是 unknown；NULL→NULL 不证明已知状态未变。
无字面变化但存在未知值时 diff_status 为 unknown。

新增/消失只称记录变化，不称路由撤销、设备上下线或物理 down。L2 保留方向和双方
接口，不覆盖平行链路，不由单侧发现推断双向采集。已存在 L2 view 的空观测集合可核
比较，coverage 仍未知；缺 view/列或查询失败不能冒充空。parsed [] 可核为捕获空数组，
缺少指定命令 parent 则为 source_incomplete，不能当采集空。

重复身份（包括完全相同的重复或多地址/路径冲突）明确 ambiguous_identity；必需
设备/接口/邻居/发现协议缺失为 unknown_identity，不任取一条。可选 vrf/afi 的 null
使用 null-safe matching，同时保留未知作用域；这不证明路由上下文相等。

只接受 source、snapshot_id_1、snapshot_id_2、可选 device/limit。limit 默认20，
范围1–50；值参数化，表/列固定。stdin 16KiB 上限；SQL先聚合计数、LIMIT后获取样本，
不把全库 fetchall 到 Python。单元格最多256字符并标 cell_truncated，裁剪身份不能
用于推断相等；样本24KiB、总响应64KiB，sample_count/has_more/byte_truncated分别
描述样本与截断，完整值仍参与关联、排序和计数。

连接 read_only、禁外部访问；全查询15秒，沿用有界宿主内存/线程配置（默认512MB/2，
最高2048MB/8）。limits 返回实际配置。stdin不能提供SQL/路径或提高预算；宿主可降低
deadline。错误非零退出，给typed error_type、diff_status unknown、counts null；
缺源/列漂移/歧义/超时/超预算都不变成“零变化”。不回退 warehouse/旧平台。
安装、harness发现、模型路由和浏览器/生产验收仍需分别验证。

compare_state仍不支持routes；需要路由内容时用独立且范围更窄的[compare_routes](route-changes.md)。
