# 调查设备、接口与覆盖率

问题：**“这份采集有多少设备，实际能检查什么？”** 先在新根运行[离线示例](../offline-data.md)。本流程读取其 `import.json`，不猜快照标签，也不需要模型。

如果要在已配置的网页对话中查当前台账或下载报告，请参阅 [Open WebUI 使用指南](../openwebui.zh.md)。

## 确定范围与成员

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"operation":"snapshot_summary","snapshot_id":sid}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer query_batch
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"operation":"devices","snapshot_id":sid,"limit":10}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer query_batch
```

本夹具台账和已观测数量均为 2，精确返回集合为 R1/R2，角色未知。六条记录覆盖五个不同命令名。应保留单位：六份命令输出不代表六种命令或六台设备。

将名称视为完整集合前，检查 `has_more`、总量与响应限制。更大范围可以缩小过滤，或使用支持的有界 SQL 分页及稳定排序。batch 只有有界样本与 `limit`，不要虚构 `offset` 参数。聚合数量相同不证明成员相同。

## 核对实际采集内容

`command_coverage` 显示采集过的命令，`multi_device_summary` 配合真实设备列表可暴露请求中缺失的设备。这些问题针对已采集记录。`search_commands` 搜索的是可能命令目录，不能证明执行过。

接口方面，`interface_health` 使用支持的 IOS/Junos 规范视图，将状态/链路/协议非 up 的记录视为不健康，不会从其它表推断缺失规范行。`bgp_health` 使用支持的汇总来源，把数字前缀数或 Established 归为已建立。batch 不支持 OSPF health；关系查询见[拓扑流程](topology.md)，聚焦原始证据见 [reporter](../skills/reporter.md)。

原生旧风格 `inspect_interfaces` 可能逐设备选最新快照，不适合宣称同一历史快照；应使用显式 batch/SQL 范围。

## 输出可核对答案

适合夹具的答案是：“这份已导入合成快照观测到 R1、R2，共 2 台；两者角色未知。包含六份输出，覆盖五个命令名。这是记录数据，不是当前网络状态。”附本次真实快照及 `temporal.collected_at` 的来源/精度/状态，不从 ID 复制日期。

对于“核心交换机多少台”，角色/类别证据未知就无法确定分类，不能把名称或平台猜测变成计数。对于“今天哪里不对”，先确认已知采集日期和相关来源；较旧或未知时间的采集不能回答实时问题。

## 计数器基线

对指定设备和接口，按[指标基线](../reference/metric-baselines.zh.md)将可比历史区间
与之后的目标区间分开，使用固定 analyzer `analyze_baseline` 入口。保留采集时钟、
单位与计数不连续缺口；观测计数器净增量的百分位仅供审阅，不自动认证健康或配置告警。

## OSPF 与证据清单

按[OSPF分析](../reference/ospf-analysis.zh.md)检查邻居观测与历史记录差异。
巡检前明确设备/命令预期清单和采集时效政策，使用
[证据准入](../reference/evidence-readiness.zh.md)；可选audit证据门可在缺失/未知时
阻止规则执行，不用当前库存补分母，也不认证全网健康。
