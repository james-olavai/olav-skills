# 原生接口与 BGP 记录

本页说明尚未发布的独立候选。已发布的 `olav-skills==0.1.1` 不含这些解析和查询更新。

| 采集命令 | 状态来源 | 判定依据 |
| --- | --- | --- |
| IOS `show ip interface brief` | `status`、`proto` | 接口状态和线路协议状态 |
| Junos `show interfaces terse` | `admin_state`、`link_state` | 管理状态和链路状态，`proto` 保存协议族 |
| IOS `show ip bgp summary` | `bgp_neighbor`、`state_or_prefixes_received` | 非负整数前缀计数表示 Established，含零 |
| Junos `show bgp summary` | 原生 `peer_ip`、`state`，或兼容的 `bgp_neighbor`、`state_or_prefixes_received` | 原生状态按已识别的 BGP 状态判定。只有兼容字段中的数字前缀计数表示 Established。 |

接口两个状态均为 `up` 时计为正常。一个分量明确为 `down`、`admin-down`、`err-disabled`、`testing` 或 `dormant`，即使另一个未知，也计为已观测非正常。其余组合保持未知。旧 Junos 字段缺 `admin_state` 时，已知非正常链路状态仍可分类，管理状态保留未知。BGP 中 `Established`、`Estab`、`Establ` 均表示已建立。`Idle`、`Active`、`Connect`、`OpenSent`、`OpenConfirm` 计为已观测非正常。其他值保持未知。Junos BGP 同时具有两套完整字段时拒绝歧义。

## 原生导入

显式启用的 managed parser、本地 Python parser 和本地 TextFSM 保留优先级。默认 Junos terse 支持物理和逻辑行、已识别协议族及 IPv4/IPv6 本地地址。续行继承接口和最近明确列出的协议族，重复观测保留独立记录。解析器记录包内模板摘要。孤立或不支持的续行、地址和超限输入不生成结构化结果，原始采集仍可留存。

默认 Junos BGP summary 选择已安装的 Junos NTC 模板。支持的单表摘要有一行 `Groups`，可识别邻居行数与声明一致。没有路由表行的邻居仍可解析，连续出现也分别保留。不支持的布局或不能绑定来源的记录明示失败。两个 Junos 默认解析器单次输入上限 1 MiB、单行上限 4,096 字符，输出上限 4,096 条。已用独立合成 IOS 和 Junos 采集包验证实际导入。

## 读取入口

- `query_batch` 保留 `interface_health` 和 `bgp_health`。计数保留 `healthy_count`、`unhealthy_count`，新增 `unknown_count`，样本是已观测非正常记录。Junos 接口 `status` 仍为链路状态，`proto` 保存协议族，新增 `admin_state` 保存管理状态。原生 Junos BGP 邻居及状态文本使用既有响应字段 `bgp_neighbor` 和 `state_or_prefixes_received`。
- [固定调查配方](investigation-recipes.zh.md) 返回非正常和未知样本，将数据库哈希绑定到实际读取的私有只读快照副本。
- [已记录状态差异](state-diff.zh.md) 从两个明确历史 SID 读取指定命令，保留旧样本字段，并为 Junos 增加 `old_admin_state` 和 `new_admin_state`。已建立 BGP 会话的前缀计数增减不会改变会话状态。重复身份拒绝比较，包括同一接口的多个协议族或地址观测。`added`、`removed` 表示记录增减方向。范围无效、来源缺失或形态不支持时计数未知。

这些结果反映已存观测。每命令生成视图可能缺少旧快照。缺记录时采集覆盖、当前状态和物理事件仍未知，未提供的 VRF/AFI 保留未知。读取入口沿用既有输入、输出和执行上限。
