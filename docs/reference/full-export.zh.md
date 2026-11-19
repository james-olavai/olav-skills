# 完整导出限定范围内的记录

未发布的独立候选提供 `olav-skills run analyzer export_query`。使用经过审阅的
64位Linux候选安装，且系统须支持OFD锁。调用进程需要已授权项目的
读取权限和其 `.olav/exports` 目录的写入权限。将 `OLAV_HOME` 设置为该项目的绝对路径。

## 选择范围

```sh
export OLAV_HOME=/absolute/authorized/project
printf '%s' '{"project":"/absolute/authorized/project","export_id":"parsed-s1","source":"parsed_outputs","snapshot_id":"s1","columns":["device_name","command","parsed_data"],"filters":{"platform":"ios"},"format":"jsonl","max_rows":1000}' |
  olav-skills run analyzer export_query
```

必填字段为 `project`、`export_id`、`source` 和精确 `snapshot_id`。
导出 ID 最长 64 个 ASCII 字符，以字母或数字起头，可以包含下划线和连字符。
支持以下两张原生物理表。

| 来源 | 默认列 | 其它可选列 |
| --- | --- | --- |
| `parsed_outputs` | `device_name`、`command`、`parsed_data` | `snapshot_id`、`platform`、`ingested_at` |
| `topology_links` | `source_device`、`source_interface`、`destination_device`、`destination_interface` | `snapshot_id`、`link_id`、`discovery_protocol`、`link_type`、`link_status`、`link_speed`、`first_seen`、`last_seen`、`last_verified`、`status_changes`、`platform` |

`columns` 可指定范围内不重复的有序列。可选 `filters` 将除 `snapshot_id` 之外
的允许 VARCHAR 列映射到区分大小写的精确字符串。记录范围遵循实际存储的 SID。
拒绝 `latest` 等别名、任意 SQL、视图、外部文件或自指定输出路径。完整交付覆盖
该数据库版本中符合条件的表记录和所选字段。零行只表示该查询没有找到匹配记录。
采集覆盖与网络状态仍需其它来源支撑。

## 读取与续跑

成功发布后，响应给出状态、清单路径和回执。读取
`.olav/exports/<export_id>/manifest.json` 核对范围、字段类型、源 SHA-256、
运行时身份、分片顺序，以及每片 SHA-256、实际行数和字节数。回执是清单原始字节的
SHA-256。仅使用清单列出的分片。

- `complete` 表示观察到查询结束，`total_rows` 为确切行数。JSONL 分片可按
  清单顺序合并。CSV 每片有表头，合并时保留一个表头。
- `partial` 给出已交付进度，`total_rows` 未知。将回执独立于产物目录保存。
  续跑提交原范围和 ID 并带上 `"receipt":"<上次返回的回执>"`，可调整本次预算。
  续跑检查原请求、回执、源和运行时，并独立核对已有分片的真实行数、字节和哈希。
  每次成功响应后更新回执。
- `failed` 代表本次没有新增已核实交付。首次查询或数据预算失败可能留下失败清单。
  续跑失败可能保留原检查点。进程或 I/O 中断时，最终发布状态可能未知。
  上次可信回执与清单不符时，使用新的导出 ID。独立保存的回执固定清单字节，无签名能力。

JSONL 为 UTF-8，每行一个对象。SQL NULL 输出为 JSON `null`。
其余值用 DuckDB 的 VARCHAR 表示，包括数值、时间戳和 JSON 文本。清单按投影顺序
列明原 DuckDB 字段类型。CSV 为 UTF-8，字段统一加引号、NULL 写为空字段、
可能被识别为公式的值前置单引号。需要区分 SQL NULL 与空串或保留原文本时选 JSONL。

## 资源和源限制

| 上限 | 默认 | 硬上限 |
| --- | ---: | ---: |
| 每次提交行数 | 1,000 | 10,000 |
| 每片实际字节，包括表头 | 4 MiB | 16 MiB |
| 输入结束后的单次运行时间 | 15 秒 | 60 秒 |
| 单行序列化 | 512 KiB | 512 KiB |
| 数据库文件 | 1 GiB | 1 GiB |
| 整体行数、字节、分片数 | 1,000,000 / 256 MiB / 256 | 1,000,000 / 256 MiB / 256 |

可用 `max_rows`、`max_bytes`、`timeout_ms` 设置单次预算。独立进程使用
128 MiB DuckDB 内存预算、单线程、无溢写和外部读取，并受 768 MiB 进程地址空间
上限约束。实际扫描和排序可能超出本次输出行数，并可能触发资源失败。
单行过大或本次无法前进时失败，已交付的字段不被裁剪。

源为已 checkpoint、无 WAL 的 `.olav/databases/main.duckdb`。命令使用绑定到实际
inode 的只读连接并核对源指纹。拒绝符号链接和有硬链接的源文件、分片或清单。
源或运行时变化后，为新版本使用新的导出 ID。

来源在专用exec读进程内持OFD锁复制到父拥有的私有临时目录，DuckDB实际只读查询
副本；核源/副本身份和字节哈希后，退出复核成功才发布manifest/回执。单靠proc描述符
路径不能固定DuckDB打开对象。父在本地超时终止/回收worker后清副本；执行runtime变化
后旧回执不悄然续用，需新export ID。
