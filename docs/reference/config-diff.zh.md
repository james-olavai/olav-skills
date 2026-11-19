# 历史配置差异

本页说明 `diff_configs` 在 0.2 中的来源和快照取值规则。0.1.1 使用早期规则。

将 `OLAV_HOME` 设为已授权的原生项目绝对路径。

```sh
printf '%s' '{"device":"edge-1","command":"show running-config","snapshot_id_1":"before","snapshot_id_2":"after"}' | olav-skills run netops diff_configs
```

设备名和命令按数据库的实际文字精确匹配。传入原始拼写的两个不同快照 ID。
省略 ID，或传入 `latest`、`current`、`newest` 会明确失败。输入顺序决定基准与目标。
身份按 UTF-8 字节比较，保留大小写、重音与内部空格，避免数据库排序规则扩大匹配范围。

## 来源与时间

某一侧的精确 SID 存在于 `netops.ingest_sources` 时，该侧从历史账本选择唯一记录，并
核对保存的来源标识和正文 SHA-256。SID 没有账本历史时，可以读取
`netops.raw_output_store` 中精确匹配该 SID、设备和命令的在存记录。last-writer 覆盖
范围为部分。缺失、重复、NULL 或来源不一致时显式失败，差异状态为 `unknown`。

结果分别给出两侧实际标识、来源表、覆盖范围、原文 SHA-256 和采集时间。账本时间来自
在存采集证据。last-writer 时间仅取受支持类型的采集字段。缺失、无效或相互冲突的采集
时间保持未知。快照标签、导入时间和文件时间不能证明采集时间。`database_version`
记录本次实际查询的原生数据库副本指纹。

## 对比与输出

统一差异保留记录行顺序，按 Cisco 风格接口、路由进程、ACL、route-map 和全局内容
归组。编号 ACL 按列表聚合，route-map 按映射名聚合，条目顺序和重复行均保留。
仅过滤明确识别的 Cisco 采集前导行，保留正文里的描述、备注和 banner。

`sections` 可选最多 16 个组别或按完整词匹配的顶层标题前缀，标题筛选不区分大小写。
某个选择器两侧均无匹配时返回错误。`context_lines` 取 0–10，默认 3。
计数针对已选正文。`preview.truncated` 和 `blocks_truncated` 分别标明预览和块列表
裁剪，裁剪时计数仍针对完整的已选正文。

请求上限 16 KiB。每侧正文最多 256 KiB、4,000 行，账本元数据最多 64 KiB。预览默认
最多 40 行，`full: true` 最多 400 行，两者均受 24 KiB JSON 字符串预算约束。整个
响应上限 64 KiB。增减行反映两份已记录正文的比较，不能认证设备当前运行配置。

读取要求支持 OFD 锁的 64 位 Linux、最大 1 GiB 已 checkpoint 的原生 DuckDB 和足够的
私有临时空间。工具持读锁从已验证的源描述符复制，禁用外部访问地只读查询副本并
清理临时文件。worker 总期限 10 秒，DuckDB 单线程、128 MiB 内存设置，进程地址空间
上限 768 MiB。失败返回 JSON 和非零退出码。
