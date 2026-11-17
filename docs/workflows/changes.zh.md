# 比较采集并准备变更草案

比较需要同一授权分析范围内的两个已知快照。明确记录源与目标 ID，以及各自采集时间来源、解析覆盖。导入顺序、ID 字典序和文件修改时间都不能证明采集顺序。

## 区分差异类型

| 工具 | 比较内容 | 不能证明 |
| --- | --- | --- |
| `query_batch` 的 `diff_summary` | `diff_kind` 选择的标识键 | 完整语义/正文差异 |
| `diff_snapshots` | parsed、raw、topology 或 OC 表的键 | 标识键不变时的值变化 |
| `diff_configs` | 指定设备/命令的已保存 CLI 文本 | 变化原因或设备当前状态 |
| `inspect_blast_radius` | 观测图中移除元素 | 策略、转发或包可达性 |

键比较中，新增是目标减来源，删除是来源减目标。精确总量与有界样本分别检查，保留各方向 `has_more`/截断。可选来源表可能不存在，应选实际相关表，不要假装所有来源齐全。

## 在夹具验证比较入口

以下是自比较冒烟检查，不是真实网络变化证据。两次都使用实际导入快照，并选择已知存在的表：

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"snapshot_id_1":sid,"snapshot_id_2":sid,"table_name":"raw_output_store"}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run reporter diff_snapshots
```

预期 `status: success`，新增键和删除键均为零。真实比较时，换成该范围内实际导入且已核实的源/目标 ID。键差为零仍可能隐藏正文或字段值变化；按需检索聚焦证据，或比较已保存文本。

`diff_configs` 需要 `device`、`command`、`snapshot_id_1`、`snapshot_id_2`；`sections`、`context_lines`、`full` 控制呈现。其来源是已保存快照 raw 文件，不能假定每个导入库都同时有这类文件系统来源。需披露紧凑输出限制及缺文件。

## 从证据形成草案

工程变更草案应写明真实端点/平台、接口、路由上下文、限制、未知和影响证据，包括完整审阅过的实施步骤、对称回退、预期后检查和变更前验证。已采集状态可能陈旧。草案由人或宿主用授权工具编写；`generate_change_plan` 是独立的批量型号模式产物工具，不是通用意图转配置编译器。

包中没有模拟器。旧 Batfish/`sim` 引用不是可执行的独立验证。图影响计算或格式完整的 Markdown 都不能认证变更。应用前，使用当前授权网络状态和合适的外部测试独立验证。Skills 不执行设备配置，也不安排维护窗口。
