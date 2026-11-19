# 数据能力体检

这是**尚未发布的开发候选**。已发布 `olav-skills==0.1.1` 没有
`doctor --data`；以下命令需要包含该功能且经过审阅的源码构建。仅安装已发布版不会启用新参数。

`olav-skills doctor` 保持快速依赖/资源检查，不打开项目数据库。显式数据检查使用独立的延迟加载入口：

```sh
olav-skills doctor --data --project /absolute/authorized/project
olav-skills doctor --data --project /absolute/authorized/project --snapshot-id exact-import-label
# 等价的显式宿主绑定：
OLAV_HOME=/absolute/authorized/project olav-skills doctor --data --snapshot-id exact-import-label
```

`--project` 优先于 `OLAV_HOME`。项目必须是已存在的绝对目录，路径组件不接受符号链接。
不回退 cwd、配置、dotenv、其它项目或 warehouse。唯一数据库是所选项目的
`.olav/databases/main.duckdb`。文件缺失就报告缺失，不自动创建、初始化或修复。
只选择自己获准读取的数据项目。

不指定 `--snapshot-id` 时，只检查支持源的目录能力和当前库存；快照记录保持
`snapshot_not_selected`。指定时使用导入回执中的精确标签，拒绝 `latest`、`newest`
和 `current`。标签样本仅来自 `parsed_outputs`，按字典序返回；它既不是全部快照，
也不是采集时间排序。截短标签不能代替原始导入标签。

## 检查内容

JSON 合同为 `olav.data-readiness/v1`。`status` 表示执行状态，`data_readiness`
表示记录查询能力。退出 0 表示检查完成，也包括缺库和空数据；退出 2 表示参数、
数据库、超时、内存、外部访问或输出预算失败。退出 0 或
`recorded_queries_available` 都不证明采集完整、协议健康、网络实时状态或所有分析能力可用。

| 来源 | 报告范围 |
| --- | --- |
| `netops.devices` | 当前库存行数、非空 hostname 去重数、空身份数；这是意图，不是快照成员 |
| `netops.parsed_outputs` | 精确标签的记录数及可见设备身份数，不代表全部采集设备 |
| `netops.raw_output_store` | 跨标签当前最后写入记录数，不作为快照历史，不读取原始正文 |
| `netops.topology_links` | 当前保留链路按最后写入标签过滤，不代表完整历史拓扑 |
| `netops.bundle_ingests` | 精确标签的元数据及有资格的 collected_at 范围，不代表逐设备/命令时间覆盖 |
| 标准接口/BGP 视图 | 可用字段及当前投影中精确标签可见记录数；旧标签为空不证明历史源记录不存在 |

固定视图清单为 `v_show_ip_interface_brief_auto`、`v_show_interfaces_terse_auto`、
`v_show_ip_bgp_summary_auto` 和 `v_show_bgp_summary_auto`。本版不检查其它目录源及全部拓扑协议。
缺源、必要字段不支持、可见记录为空、未选快照及查询错误分别报告。视图存在不证明命令已采集、
已解析；空视图不证明协议没有会话。采集和解析覆盖保持 `unknown`。
时间只来自类型合格的 bundle 元数据；缺失、无效或类型不支持时不赋时间资格，
无时区时间保留该限制。不把入库时间或 snapshot 标签当采集时间。

## 预算与后续动作

worker 整体期限 5 秒，DuckDB 只读、内存 128 MB、单线程、禁止落盘溢出，
关闭外部文件/网络访问和扩展自动加载/安装。只执行固定 SELECT，标签使用绑定参数。
每个支持源最多返回 32 个字段，最多 20 个标签样本，每个目录单元格最多 256 字符，
响应最多 24,000 UTF-8 字节；裁剪显式标记。超时、资源或输出错误不为部分能力结果签发结论。
不读取原始 CLI、解析 JSON 正文或 capture 文件。宿主仍须提供文件系统授权；这不能代替操作系统权限边界。

按 `next_steps` 和已有导入返回的 `parse_report`、`report_path`、
`learnable_commands` 排查解析缺口。体检不扫描报告、不重建第二份解析账本。
缺标准视图或保留拓扑时，结合授权导入证据及对应查询自身范围再选择分析。
缺数据时另行执行获准的离线导入；doctor 自身始终只读。
