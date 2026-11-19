# 解析器版本与离线重解析

版本管理和离线重解析需要 Olav Skills 0.2。0.1.1 提供早期的 `learner finish_learn` 工作流。

## 验证、启用与回退

通过 stdin 向 `olav-skills run learner manage_parser` 传入一个 JSON 对象。
每次请求都包含已授权的绝对项目路径 `project`、`platform`、`command`
和 `operation`。正常解析或导入前，将 `OLAV_HOME` 设为同一项目。

| 操作 | 附加字段 |
| --- | --- |
| `create` | `version`、`dsl`（`textfsm` 或 `python`）、不含 DSL 标记的 `source`、原始训练文本数组 `training_samples` |
| `validate` | `version`、由 `{raw_output, expected_rows}` 组成的 `holdout` 数组 |
| `inspect` | 可选 `version` |
| `activate` | 已验证的 `version` |
| `rollback` | 可选 `target`，默认 `previous`，也可显式选择 `legacy` |

版本标识固定源码与申报训练样本摘要。每次验证需要两到十六条不同的独立输入，
并逐条比较有序 JSON 记录、字段和值类型。期望值按常规解析的字段归一化规则书写，
且至少一条样本期望非空结果。只更改换行或外层空白仍视为重复输入。摘要用于本地核对，
样本的来源与代表性由操作者确认。验证失败的状态是 `invalid`，候选不可启用，可在纠正
证据后重试。验证成功后证据固定，修改源码需创建新版本。

启用后，正常解析入口使用项目、平台和命令共同选中的候选。候选解析为空时结束查找，
选择记录损坏或解析失败时报告错误。回退到 `legacy` 恢复原有 Python、自定义 TextFSM
和内置模板查找。已存模板及历史解析记录保持原样。

新版 Python 候选采用纯 `parse(raw)` 函数，仅开放受限内建和 `re`、`json`、
`ipaddress`、`collections`、`itertools` 的部分函数。执行复用 AST 和沙箱检查，
对子进程使用精简环境和资源限制。该模式适用于受控项目中审阅过的源码，不提供容器或
操作系统隔离。

## 使用已存原文离线重解析

```sh
printf '%s\n' '{"project":"/absolute/scratch/project","platform":"cisco_ios","command":"show widget state","snapshot_id":"lab-recording-03","version":"v1","output":"widgets-v1.json"}' |
  olav-skills run learner reparse_snapshot
```

输入必须指定精确 SID、平台、命令、已验证版本和新的 JSON 文件名。该版本可处于未启用
状态。入口只读本地 `.olav/databases/main.duckdb`。若导入账本
`netops.ingest_sources` 存有该 SID，核查匹配记录的来源键和正文哈希。若账本没有
该 SID，则 `netops.raw_output_store` 只能提供当前仍携带该 SID 标签的部分记录。
更早已被覆盖的 raw 无法经此入口恢复。查阅 `coverage.source_scope` 区分两种来源，
完整的快照采集覆盖仍为未知。

SID、设备名和导入包标识保留存储中的精确拼写和大小写。命令与平台沿用管理解析器时
的归一化规则。

输出在 `.olav/derived-reparses/` 中新建文件，包含记录的原 SID、设备、命令、
来源、UTF-8 正文哈希、解析器版本与源码哈希、验证摘要以及派生行。采集时间只来自
已存证据。日期形 SID 和写入时间不会当成采集时间，已存导入包时间保留在包级证据中。
解析结果为空标为 `no_rows`。找不到可用来源记录时返回 `unavailable`。
所有选中记录都处理完成才发布文件，成功返回文件路径及 SHA-256，已有文件拒绝覆盖。
原 DB、raw、历史解析数据与既有派生文件均不写入。

单次上限为 128 条 raw、每条 256 KiB、raw 合计 4 MiB、每条最多
2,000 个派生行、全部最多 20,000 行、产物 4 MiB。每个子进程阶段限时 12 秒。
入口要求64位Linux/OFD锁及最大256MiB已checkpoint原生DB，拒绝符号链接和WAL。
专用exec读进程持源读锁复制到父拥有的私有副本，实际DuckDB只读开副本；本地取消
后父清理副本。底层helper不得与同源writer共处一个进程，不把proc路径当真实pin。
详见[英文示例](parser-lifecycle.md)。
