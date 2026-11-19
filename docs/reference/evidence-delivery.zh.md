# 离线证据报告包

此能力随未发布候选提供。安装已审阅的候选版本后，可通过
`olav-skills run reporter evidence_bundle` 执行。标准输入接收一个 JSON 对象。

## 创建和校验

将逐项批准交付的 UTF-8 JSON、CSV 或 Markdown 文件放入授权本地项目中已存在的
`exports/evidence_sources/`。审查文件名和内容中的敏感信息，并建立输出目录
`exports/evidence_bundles/`。

运行 `olav-skills run reporter evidence_bundle`。请求中的项目路径必须为绝对路径，
来源文件使用项目相对路径。

```json
{
  "operation": "create",
  "project": "/absolute/authorized-project",
  "bundle": "review.zip",
  "sources": [
    "exports/evidence_sources/evidence.json",
    "exports/evidence_sources/report.md"
  ]
}
```

输出 ZIP 中包含 `manifest.json` 和逐个编号的来源文件。清单记录文件相对路径、
原始字节数、SHA-256 及生成器的实际版本。采集时间为 `unknown`，覆盖范围为
所列文件，档案覆盖状态为 `partial`。JSON 中可识别的 `has_more`、`truncated`、
有界摘录等截断标记会反映在清单中，原始字节一并保留。生成的元数据省略项目绝对路径
和文件系统时间戳。

独立校验时，将 ZIP 放入接收项目的 `exports/evidence_bundles/`。提交
`"operation": "verify"`、对应 `project` 及 `bundle` 文件名。若从独立可信渠道
收到创建结果中的摘要，可传入 `expected_manifest_sha256`。校验检查归档结构和
内容摘要，独立摘要匹配可将结果与已知清单绑定。缺少独立摘要时，ZIP 和清单可以
被同步替换。报告原有主张、实际采集时间、来源完整性和作者身份须另外核实。

## 按明确来源复核

提交 `"operation": "recheck"`，使用原请求的 `sources`、`project` 和 `bundle`。
可同时传入 `expected_manifest_sha256`。复核先校验 ZIP，再按所列路径对照文件字节。
调用方必须再次列明与清单完全一致的路径，项目中未选中的文件不在核对范围内。
要改变来源集合，请用新名称创建另一个 ZIP。

| 复核状态 | 含义 |
| --- | --- |
| `unchanged` | 所选文件读取时的字节数和哈希均匹配。 |
| `stale` | 至少一项来源已改变或缺失。 |
| `unknown` | 至少一项来源无法安全读取，且未观察到改变或缺失。 |
| `not_checked` | 操作未要求复核，或前置校验失败。 |

校验通过的旧包仍可能过期。部分来源不可用时，请查看逐文件结果。复核只读取
文件并保留原 ZIP。文件相同只表明当次对照范围内字节相同，数据库、后续采集记录
和现网状态须另外核实。

## 限制

文件数量上限为 16，每个文件 1 MiB，来源文件合计 8 MiB。JSON 请求与清单分别
限 16 KiB。单文件限 20,000 行，JSON 内容检查限 30,000 个节点。本地 POSIX
文件系统上的命令时间上限为 10 秒。校验仅接受该格式的有界、未压缩普通 ZIP 条目。

路径须使用可移植的 ASCII 名称，并显式指定单个文件。脚本拒绝隐藏路径、常见私有或
凭据名称、符号链接、硬链接及特殊文件，并拦截部分可识别的凭据内容。内容检查覆盖
有限，交付前仍需审查和脱敏。写入仅限现有输出目录中的新 ZIP，已有目标文件会被保留。
