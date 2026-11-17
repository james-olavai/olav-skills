# 记录数据与本地模型

原生离线流程使用显式 `OLAV_HOME` 下的本地 DuckDB，默认位于 `.olav/databases/main.duckdb`。导入会建立来源表与命令专用视图；视图是否存在取决于输入和解析器。同一包版本处理不同采集数据，可用视图也可能不同。

## 从文件到结论

| 层次 | 典型来源 | 含义 |
| --- | --- | --- |
| 包来源 | `netops.bundle_ingests` | 包标识、哈希、导入及输入声明的采集元数据 |
| 设备台账 | `netops.devices` | 已知设备和元数据，不自动代表历史快照成员 |
| 采集文本 | `netops.raw_output_store` | 已记录命令输出，包含没有结构化解析器的输出 |
| 解析记录 | `netops.parsed_outputs` | 结构化解析结果，覆盖范围可小于原始台账 |
| 派生关系 | `netops.topology_links`、命令 `v_*_auto` 视图 | 基于可用输入推导的观测 |

公开双设备示例在 0.1.1 中建立 15 个 netops 表/视图，包括 `v_l2_links_auto`、`v_show_ip_bgp_summary_auto`、`v_show_ip_interface_brief_auto` 和 `v_snapshots_auto`。这只是示例结果，并非每个数据库必备 schema。示例没有 OSPF 来源视图。编写 SQL 前使用 `describe_table` 或相关 `query_batch` context；不要把旧 DDL 粘贴到新分析库。

## 台账、成员与角色

已经导入但未解析的设备仍在台账中，结构化协议表却可能没有它。`snapshot_summary` 区分 `inventory_device_count` 与 `observed_device_count`，命令数和记录数也有不同单位。显式快照的设备查询可将台账限定为已观测成员；不限定快照的台账是另一种范围。报告时保留这些范围说明。

需要设备集合时，应核对精确成员。两个计数相同不证明设备相同。角色为 null 表示未知：名称、厂商、ASN、loopback 和平台本身不能证明核心/边缘角色或设备类别。合成样本中两台设备的角色均未知。

## 时间需要来源

`query_batch` 的 `temporal` 块分别记录采集、导入、记录更新和事件时钟；各有值、精度、来源和状态，时间窗口还可能提供起止边界。没有时区的时间戳不能默认为 UTC。日期形快照名只是推断；自动生成的 `snap_*` ID 不能证明采集时间。CLI 的事件时间可以保持未知。

有界 batch 的 `latest` 根据采集来源选择，可能返回 `ambiguous_snapshot_time`。其它脚本有各自的选择逻辑，例如 `query_topology` 优先取有二层数据的快照，再按 ID 排序。关系、图表及比较操作都应传入导入返回的真实快照 ID，不能假定各工具默认值选择同一份采集。

## 解释缺失与限制

来源视图缺失、成功但没有匹配、解析缺口及时间缺失是不同结果。拓扑的 `absent_layers` 会显示没有来源的协议层。即使聚合计数精确，分页与单元格裁剪也可能限制可见内容，详见[脚本结果](reference/scripts.md)与[设备调查](workflows/inventory.md)。

尽量只读保留原始采集包，将导入/解析报告与结论一起保存。`.olav` 目录沿用不代表所有历史库兼容；迁移现有消费者前先[验证授权副本](migration.md)。
