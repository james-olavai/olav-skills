# importer：导入离线采集

Importer 读取已采集文件，校验规范包并建立本地可查询快照。它不 SSH 连接设备，也不修改设备配置。[完整离线示例](../offline-data.md)提供可执行的双设备流程。

## 使用准备后的路径

1. `survey_bundle` 接受 `{"path":"/absolute/input"}`，识别支持格式，可能解压/归一化目录、归档或原始 dump。继续前检查 `error`、`ingest_supported` 与 `notes`。
2. 保留返回的 `path`，它可能与原输入不同；对这个规范目录执行 `validate_bundle`。
3. 仅在 JSON `ok: true` 时继续。检查警告和设备/命令数。哈希不一致属于来源完整性失败，不能绕过。
4. `ingest_snapshot` 接受准备后的 `path`，可选 `collection_source` 与审阅过的 `host_platforms`。先固定新的 `OLAV_HOME`，宿主超时应适应包大小。
5. 记录返回的 `snapshot_id`、`db_path`、包哈希、`parse_report` 与 `report_path`。

对于已审阅规范目录，可以按示例直接校验与导入。校验器本身不接受任意归档格式；survey 产生新目录后，不要再把原始归档传给校验器。

## 包内内容

规范目录包含 `manifest.yaml`、`devices/<hostname>/_meta.yaml` 和逐命令 `.txt`。manifest 声明 schema 版本、collector 名称/版本、主机数量、脱敏状态及内容 SHA-256。哈希按主机/文件排序逐个输入命令文件原始字节，包含可选头部。设备元数据记录名称、管理 IP、平台与厂商。应优先使用明确的采集元数据，避免从文件名推断。

survey 提示设备需要平台检测时，可用 `discover_platform` 的 `host_dir` 参数。平台置信度未知应保留未知，也不能从平台猜网络角色。根据 survey 的 notes 执行支持的下一步；格式不支持时停止。

## 同时报告落盘和缺口

没有结构化解析器也可能成功保存原始输出。报告 `devices_landed`、`command_outputs_landed`、已解析/未解析数量、`unparsed_by_reason` 和无结构化数据设备。通过完整报告区分 `raw_only`、`no_parser_registered` 与 `parser_error`。导入数不是解析数，协议表缺行也不能作为完整设备台账。

适合的解析缺口可建议另行审阅使用 [learner](learner.md)。导入不调用模型，也不会自动学习所有不支持命令。随包模板可以暂存，但不会静默启用。

导入会写分析状态和报告，不提供历史数据库迁移保证或服务端维护 API。保留原始采集，使用历史数据前先[验证授权副本](../migration.md)。另见[配置](../reference/configuration.md)，避免误选其它项目数据库。

## 未发布的原生来源账本

候选的官方native目录导入在同一数据库事务内保存新raw历史及受限来源/采集时钟
元数据。默认SID追加本次导入唯一后缀，不是采集时间证据；后续raw store覆盖不删除
历史账本。旧数据不回填，legacy library默认未opt-in路径保留原界限。详见
[证据准入](../reference/evidence-readiness.zh.md)。actual raw hash只绑定实际脱敏
流程输出，不认证脱敏成功/来源真实性；staging、report及脱敏状态文件不在DB回滚保证内。native导入将脱敏状态绑定到所选项目workspace；变更`OLAV_HOME`后须启动新进程再导入。
