# analyzer：有界分析与计划准备

Analyzer 提供记录元数据、有界 SQL、设备/接口检查、本地日志搜索及变更计划准备。它不调用模型端点，也不下发设备配置。先明确证据问题，数据足以回答或已证明缺口时即停止调查。

## 优先匹配模板

`query_batch` 支持快照统计、设备/命令覆盖、过滤设备/命令、多设备汇总、Top N、标识键差异及部分接口/BGP 健康检查。先用 `{"operation":"context"}` 查看能力，再选操作和显式快照。未支持意图应保留不支持，不能为了套模板丢掉日期/厂商/设备条件。

原生历史快照的设备与接口问题，优先使用模板或有界 SQL。包中仍有旧风格 `inspect_devices` 和 `inspect_interfaces`，但本地原生路径不支持任意显式快照：前者拒绝给定 `snapshot_id`，后者拒绝 `latest` 之外的值。接口检查可能逐设备选最新记录，不能将这种便利入口写成同一历史采集。

## SQL 需要明确提供

将 `OLAV_HOME` 指向合成根后：

```sh
printf '%s\n' '{"sql":"SELECT hostname, role FROM netops.devices ORDER BY hostname","max_rows":10}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer execute_sql
```

预期为 R1/R2，角色未知。这是台账查询，不是采集时间证明。只有 `query` 没有 `sql` 时返回 `needs_sql_generation` 与 schema，由宿主或人编写 SQL。`describe_table` 接受 `table_name` 和可选 `include_samples`，只读取相关 schema。字段投影、绑定 `params`、稳定排序及[结果限制](../reference/scripts.md)都需要保留。

修改语句不执行，SQL 外部文件/网络访问会拒绝。显式 `export_bounded` 只写适用的有界预览，不是完整数据集。

## 其它交付

`search_logs` 搜索已复制的本地日志；没有 syslog 存储是来源缺失。`inspect_blast_radius` 计算记录图中移除元素的影响，精确总量与样本成员分开，不是可达性模拟。`generate_change_plan` 根据型号模式在 `exports/change_plans/` 写批量计划，参数有 `model_pattern`、`output_filename`、`upgrade_description`、`bfs_order`。草案使用前须审阅事实、假设、设备语法、回退和后检查。

包中没有模拟器或通用报告写入 API。宿主可通过获授权文件工具写审阅后的报告。另见[设备调查](../workflows/inventory.md)、[变更](../workflows/changes.md)及[安全](../safety.md)。
