# 脚本目录与 stdin/结果协议

已安装脚本以绑定 venv 的 `olav-skills list` 为准。下表列出沿用自 0.1.1 的基础脚本；不同技能中的同名脚本可能封装同一领域能力。

| 技能 | 脚本基名 |
| --- | --- |
| analyzer | `describe_table`、`execute_sql`、`generate_change_plan`、`inspect_blast_radius`、`inspect_devices`、`inspect_interfaces`、`query_batch`、`search_logs` |
| importer | `discover_platform`、`ingest_snapshot`、`survey_bundle`、`validate_bundle` |
| learner | `finish_learn`、`prepare_learn` |
| netops | `diff_configs`、`diff_snapshots`、`inspect_blast_radius`、`query_batch`、`read_change_plan`、`search_commands` |
| reporter | `describe_table`、`diff_snapshots`、`query_batch`、`query_evidence`、`read_change_plan` |
| topology | `list_recipes`、`prepare_recipe`、`query_batch`、`query_topology`、`rebuild_views`、`save_recipe` |
| writer | `draw_topology`、`render_topology_drawio`、`render_topology_mermaid`、`render_topology_svg`、`topology_view_filter` |

## 0.2 新增入口

0.2 新增 analyzer 的 `check_change_plan` 和 reporter 的 `compare_state`。
传 JSON 前先读[计划预检查](plan-precheck.md)和
[记录状态差异](state-diff.md)的输入/来源合同。检查执行成功不代表变更获批准，
记录差异也不直接证明网络掉线。


0.2 还新增 learner `manage_parser` / `reparse_snapshot`、analyzer
`export_query` / `run_investigation`、reporter `evidence_bundle`。先阅读
[解析器生命周期](parser-lifecycle.md)、[完整文件导出](full-export.md)、
[固定调查流程](investigation-recipes.md)与[证据包交付](evidence-delivery.md)。
解析器启用属于显式项目写操作，重解析仅生成派生产物；文件完整性、导出完整度和
结论真实性分别核验。


## 传输约定

向 `olav-skills run SKILL SCRIPT` 的 stdin 传**一个 JSON 对象**，stdout 与 stderr 分开。多数脚本打印 JSON 对象，三个 render 转换器则打印包含标记或省略说明的 JSON **字符串**；保存产物前先解码。结果/退出码因脚本而异：`validate_bundle` 可在 `ok: false` 时退出零，转换器可在返回省略注释时退出零。`execute_sql`、`query_evidence` 在错误路径返回非零；快照 diff 参数错误退出 2，执行失败退出 1。两者均需检查。

`{}` 不是通用有效请求，必填参数因脚本而异。空可选列表通常表示在绑定范围内发现，不是“不选任何项”。有界 batch 不接受任意数据库/项目路径。

## 有界 batch 操作

通用输入有 `operation`、显式 `snapshot_id`、可选且大小写精确匹配的 `devices`、`vendors`、`commands`、`limit`（1–50，默认 10）。列表最多 50 值、每值 256 字符。未知或不适用字段会报错，不接受 SQL、任意表或 `db_path`。必须显式绑定 `OLAV_HOME`。

| 操作 | 结果与边界 |
| --- | --- |
| `context` | 相关白名单来源/列是否存在，不返回答案行/计数，不接受数据过滤 |
| `snapshot_summary` | 台账设备、观测设备、非 null 不同命令、记录数 |
| `device_coverage`、`command_coverage` | 厂商/命令覆盖聚合，保留单位 |
| `devices`、`commands` | 有界元数据行，台账与已采集命令范围不同 |
| `multi_device_summary` | 各设备记录/命令/状态，包括请求但零匹配的设备 |
| `top_n` | `group_by` 为 device/command/vendor，`metric` 为 device_count/command_count/record_count |
| `diff_summary` | 两个显式 ID，parsed/command/topology/OC 标识键集合，不是语义 diff |
| `interface_health`、`bgp_health` | 仅支持的规范视图，来源缺失是失败，不是健康的空结果 |

元数据行操作可选支持的非空 `fields`、`order_by` 和 `order`，会添加稳定次序字段。Top N 使用 metric/order，勿给冲突排序。协议 health 不接受命令过滤，也不支持 OSPF health。模板不能静默丢弃用户条件。

行有上限，单元格可能在 256 字符裁剪，普通 batch 响应上限 12000 UTF-8 字节。聚合可以在范围内精确，而展示行只是样本。检查 `has_more`、字节截断、有效限制、计数范围/单位及真实来源。batch 没有通用 `offset`，需要时缩小过滤。源/目标时间元数据分别检查。

## 有界 SQL

`execute_sql` 接受 `sql`、可选绑定 `params`、`max_rows`、`max_cell_chars`、`max_total_bytes`、`timeout_ms`、`offset`、`export_bounded`；只有 `query` 时返回 schema，由调用方生成 SQL。`explain_only` 仅取 schema。`describe_table` 则接受 `table_name` 与可选 `include_samples`。

库级上限包括 1000 行、每格 8000 字符、响应 1000000 字节、60000 毫秒、SQL 65536 字节；入口可能施加更低的 tier/context 限制。请求更大值不会解除限制，应检查返回 `budget` 与结果合同。取行上限不等于引擎全部扫描量或宿主 RSS 上限。

`data`、`returned_rows`、`returned_bytes` 描述当前页；`has_more`、`next_offset`、`truncated`、`cell_truncated`、`budget_exceeded` 描述不完整性。未完整观测时 `count`/`total` 保持 null；offset 零之后的页面不建立完整总量。分页保持排序、过滤和快照稳定。单元格裁剪应缩小字段提取，翻页不能恢复。超时或字节错误不是成功空结果。请求并实际生成的 CSV 仅包含返回的有界预览。

## 时间与证据

`temporal` 按精度/来源/状态区分采集、导入、记录更新、事件时钟。任意 SQL 时间范围未知，不能把快照时间套到无关 SELECT。不同便利脚本保留各自默认选择器，应显式给 ID。

`query_evidence` 要检查匹配/总量完整性、分页、片段边界及日期排除。未知时间候选在正文匹配前排除，不是匹配数，也不能加到其它数量上构造分母。`no_dated_candidates` 可能表示日期问题未解决。详细日期行为见 [reporter](../skills/reporter.md)，参数与写效果见各[技能页面](../index.md)。

## 原生合同与历史分析

0.2 将 `query_batch`、`compare_state`、`run_investigation` 统一到
[原生合同](native-contracts.md)。netops diff_configs遵循
[历史配置来源合同](config-diff.md)。reporter新增
[compare_routes](route-changes.md)、[compare_counters](counter-deltas.md)和
[run_audit](audit-profiles.md)。仍使用绑定解释器和stdin JSON，保留来源、限制与未知。

## 报告引用与拓扑差异

reporter `lint_report` 按调用者固定的证据上下文检查报告引用与显式结论映射。
使用前阅读[引用校验](report-citations.zh.md)，保留覆盖缺口；引用有效不证明结论真实。
`compare_topology` 比较精确双快照的链路内容，保留端点接口与并行链路，详见
[拓扑内容差异](topology-changes.zh.md)。缺采集证据或字段保持未知。
WebUI 工具权限由私有集成决定。

## 历史路径与指标基线

topology `inspect_path` 查询历史邻居图的有界候选路径，保留接口与并行链路，
详见[拓扑路径](topology-paths.zh.md)。analyzer `analyze_baseline` 将可比历史区间
与独立目标区间分开，计算固定指标的统计参考，详见[指标基线](metric-baselines.zh.md)。
采集覆盖、时钟、重置与枚举遗漏须保留未知。WebUI 工具权限由私有集成决定。

## OSPF 与证据准入

analyzer `analyze_ospf` 提供OSPF采集观测与有界历史记录比较，先读
[OSPF分析](ospf-analysis.zh.md)。`check_evidence` 按显式设备/命令清单、as-of和
时效政策核对历史raw与parsed，先读[证据准入](evidence-readiness.zh.md)。
`run_audit` 可选OSPF规则与显式证据门，详见[规则集](audit-profiles.zh.md)，默认
三检查不变。新native CLI导入保存新来源账本，不回填旧无历史数据。WebUI 工具注册由私有集成决定。

native snapshot_summary的result_contract.counts按字段区分：inventory_device_count是当前项目目录（设备/厂商过滤），不受所选SID或命令筛选限制；observed_device_count是所选SID解析记录与当前筛选目录的交集。未登记parsed设备不在该join统计，不能宣称全部采集覆盖。各项标注实际来源、适用过滤及SID是否适用，保留原数字不变。仓储适配不代验；时钟精度只适用同一个来源字段。
