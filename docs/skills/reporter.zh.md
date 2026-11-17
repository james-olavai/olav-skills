# reporter：让结论带有来源证据

Reporter 支持调查、快照比较，以及有界读取已记录 CLI/配置/日志片段。它帮助把陈述关联到输入来源，不会补造缺失观测；工具返回成功也不自动证明答案正确。

## 检索聚焦证据

`query_evidence` 必须提供 `source`（`command_output`、`config`、`syslog`）和 `pattern`。按需用 `device`、`commands`、`ids` 及显式 `snapshot` 限定，再用 `limit`、`page` 或 `offset` 有界读取。[离线示例](../offline-data.md)在合成导入后，使用已保存 ID，从 R1 / `show version` 中检索 `Hostname`。

检查匹配项、来源标识/哈希、片段边界、`total`、`total_is_complete`、`has_more` 与分页元数据。匹配数不是设备数，正文片段本身也可能被缩短；分页能取回省略行，却不能恢复被缩短的正文。应缩小证据问题，不要倾倒整个 raw output。

日期过滤（`date_from`/`date_to`）只包括采集日期已知且在范围内的候选。`date_unknown_candidates`、`date_exclusions`、`date_scope` 揭示其它元数据过滤后、正文匹配前因时间未知/无效/冲突而排除的候选。这些排除数不是匹配数，正文并未评估；不要加到展示候选数上虚构分母。即使已知日期有匹配，也要披露未知时间排除。

若 `no_dated_candidates` 同时存在未知时间排除，日期问题仍未回答。翻过末尾的空页也不同于空结果集。跟随 `next_offset`/页码时保持过滤及快照不变，详见[脚本结果语义](../reference/scripts.md)。

## 根据核实结果组织报告

支持的元数据/健康汇总用 `query_batch`，陌生相关视图用 `describe_table`，键集合比较用 `diff_snapshots`；`read_change_plan` 读取已有草案。这些工具不包含 `format_and_export`、模拟器或通用 Markdown 报告写入接口。合成示例通过普通且已审阅的 Python 文件 I/O 写报告；AI 宿主需要自身获授权写入工具。

实用报告应包含范围和快照、采集时间来源、台账/观测成员区分、设备/值/来源支撑的发现、缺失来源、截断及建议。图影响要标为图证据。交给 [writer](writer.md) 排版时保留精确技术 token。每个结论都应由返回证据支持，未知与解析缺口也要写明。
