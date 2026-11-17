# netops：导航与共享比较

netops 用于选择技能并复用共享读取工具。它读取已记录数据，不连接设备，也不创建子代理 runtime。在独立包中，“委派”表示读取相应同级技能，再通过 [stdin JSON](../reference/scripts.md) 调用实际可用脚本。

## 按所需结果选择

| 需要 | 技能 |
| --- | --- |
| 导入离线文件并检查解析情况 | [importer](importer.md) |
| 统计、元数据、有界 SQL 或变更草案 | [analyzer](analyzer.md) |
| BGP、OSPF、二层关系 | [topology](topology.md) |
| 附来源片段的调查 | [reporter](reporter.md) |
| 绘制关系或润色已有文字 | [writer](writer.md) |
| 为不支持输出扩展解析器 | [learner](learner.md) |

包中没有采集器、模拟器、记忆 API 或工作流执行器。不要把旧 `task(...)` 引用转成宿主并不存在的工具。[Portable 导出](../portable.md)中保留的工作流同样仅为元数据。

## 共享脚本

`query_batch` 提供有界元数据模板，analyzer、reporter、topology 下也有该入口。优先使用一个匹配模板，避免逐设备循环。它通过 `OLAV_HOME` 绑定分析根，不接受模型任意指定数据库路径。选择显式快照，不支持的过滤或来源错误应保留。

`diff_snapshots` 比较源/目标标识键集合，不能发现所有内容变化。`diff_configs` 比较指定设备、命令的已保存 CLI 文本。`read_change_plan` 读取已有计划；`inspect_blast_radius` 计算观测图中移除节点或边的影响。解释结果前请读[变更流程](../workflows/changes.md)。

`search_commands` 通过 `device`、`platform`、`keyword`、`limit` 搜索命令目录。该目录与 batch 的 `commands` 不同，后者统计已采集命令记录。目录条目不代表某快照执行过该命令，也不授予设备执行权。

## 第一个有界调用

选定已校验的分析根后：

```sh
printf '%s\n' '{"operation":"context"}' | /absolute/path/to/skills-venv/bin/olav-skills run netops query_batch
```

它返回能力/来源字段，不返回设备计数。再根据[脚本参考](../reference/scripts.md)选匹配操作，传入真实快照 ID。最终说明保留采集时间来源和未知值。宿主模型的自然语言路由需要单独测试，本页导航不能证明路由准确。
