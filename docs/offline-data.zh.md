# 第一次离线分析：校验、导入、查询与报告

本指南针对已安装的 `olav-skills` 0.1.1，可通过 PyPI 或审阅过的 wheel 获取。合成示例不需要模型、设备连接或凭据。先完成[安装](installation.md)；示例使用 POSIX shell，请替换为实际绝对 venv 路径及新的分析目录。

持有经审阅的 0.2.0 wheel 时，可在同一合成示例上追加精确快照体检和变更草案预检。[候选版上手](release-candidate.md)

## 执行完整合成示例

wheel 和 sdist 包含 `examples/synthetic_walkthrough.py` 及 `examples/synthetic_2host_bundle`；安装 wheel 后位于 `olav_skills/examples`。在源码目录外执行已安装副本：

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
export PYTHONDONTWRITEBYTECODE=1
"$SKILLS_PYTHON" -I -B -m olav_skills.examples.synthetic_walkthrough --home "$OLAV_HOME"
```

传给 `--home` 的目录必须不存在或为空。示例创建目录，在清理过的子进程环境固定 `OLAV_HOME`，再调用已安装 CLI 并传入 stdin JSON。它校验包、导入、断言精确设备集合 `{R1, R2}`（不只计数），生成 Mermaid 图，取得 R1 的一条 `show version` 证据，再依据已核实结果编写 Markdown 报告。输入有六份命令输出及一条 CDP 链路，不涉及模型或真实网络访问。

| 输出 | 核对内容 |
| --- | --- |
| `validate.json` | `ok: true`、计数、内容哈希 |
| `import.json` | `hosts: 2`、`commands: 6`、真实 `db_path`、`snapshot_id`、解析报告 |
| `devices.json` | 精确 R1/R2，`has_more: false` |
| `topology.json` | `status: ok`、两节点一边、真实存在的 `path` |
| `evidence.json` | 一条 R1 记录、来源标识/哈希、片段边界 |
| `exports/reports/synthetic_inventory.md` | 合成范围、快照及产物引用 |
| `*.stderr.log` | 与 JSON stdout 分开的分步诊断 |

导入还返回完整 import-report 路径，包含不支持/未解析命令。示例 Markdown 由 Python 根据已核实结果组成，不是独立报告写入 API 或模型分析。缺命令、角色未知、时间缺失仍须保留，示例不能证明实时网络状态。

## 分别调用

完成示例后保留同一 `SKILLS_PYTHON` 和 `OLAV_HOME`。定位已安装夹具并仅校验目录：

```sh
BUNDLE=$("$SKILLS_PYTHON" -I -B -c 'from importlib.resources import files; print(files("olav_skills") / "examples/synthetic_2host_bundle")')
"$SKILLS_PYTHON" -I -B -c 'import json,sys; print(json.dumps({"path":sys.argv[1]}))' "$BUNDLE" | "$SKILLS_PYTHON" -I -B -m olav_skills run importer validate_bundle
```

校验器支持目录包，检查 manifest schema、命令内容 SHA-256 与数量；应检查 JSON 的 `ok`、`errors`、`warnings`，不能只看退出码。哈希证明一致性，不证明发送者身份或授权。ZIP 交接应先审阅路径权限，在新目录安全展开后校验；不能因为导入器支持 ZIP 就假定目录校验器也支持。

处理新的授权包时，将 `BUNDLE` 指向审阅后的绝对目录，并选新分析根，不要为了尝试命令重复导入现有用户数据：

```sh
export OLAV_HOME=/absolute/path/to/new-authorized-analysis
mkdir -p "$OLAV_HOME"
"$SKILLS_PYTHON" -I -B -c 'import json,sys; print(json.dumps({"path":sys.argv[1]}))' "$BUNDLE" | "$SKILLS_PYTHON" -I -B -m olav_skills run importer ingest_snapshot
```

核对数据库路径、包哈希、快照 ID 与解析报告。导入写数据库、派生视图和报告，随包模板可以暂存供审阅，不会静默启用。本流程面向原生本地 DuckDB；服务端/DuckLake 维护有独立合同。

完整示例生成的合成根可执行以下只读 SQL：

```sh
printf '%s\n' '{"sql":"SELECT hostname FROM netops.devices ORDER BY hostname","max_rows":10}' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer execute_sql
```

拓扑和证据使用导入实际返回的 `snapshot_id`。以下从保存的 `import.json` 取值，再通过 JSON 编码传参：

```sh
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"concept":"all","snapshot_id":sid}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run topology query_topology
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"source":"command_output","pattern":"Hostname","device":"R1","commands":["show version"],"snapshot":sid,"limit":5}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run reporter query_evidence
```

拓扑来源缺失时，对应协议层的关系仍为未知。优先使用精确快照 ID，并将真实采集时间证据写入报告。快照标签和默认 `latest` 都无法确定数据的采集时刻或新鲜度。

## 使用自己的离线采集

Collector 独立交付，在已授权设备环境按其文档采集。本包不连接设备，也不提供采集命令。通过批准的传输方式把审阅后的授权包带到分析机器，保留 manifest 与命令原始字节；分享前检查脱敏及来源，单凭 redaction 标志不够。

规范目录为 `manifest.yaml`、`devices/<hostname>/_meta.yaml` 和逐命令 `.txt`。合成包只用于理解格式，不能把真实数据伪称合成。可读包名或声明数量不代表精确成员。先校验，再导入新根，检查解析覆盖和查询结果。

避免继承其它项目的 `OLAV_DB_PATH`、服务连接或配置。Python 导入数据模块前固定 `OLAV_HOME`，保留只读来源包并按需备份分析根。本文不会原地转换历史生产库。

## 错误与结果边界

输入缺失或无效时检查 `errors`，`ok: false` 不继续。表/视图缺失、来源为空、解析缺口与根目录错误需要分别识别；未知不计零。确定性 SQL 用 `sql`，自然语言 `query` 不会自动生成本地 SQL。修改与外部访问会被拒绝。

检查 `has_more`、截断、限制与计数口径；预览和有界 CSV 不自动成为全量导出。需要时缩小条件或使用支持的分页和稳定排序，不从不完整页或相同聚合数量推断成员相同。JSON stdout 与退出码因脚本而异，须同时检查 stderr、实际产物与语义结果。文件流程通过不代表宿主发现、普通问答、模型推理或真实数据准确性通过。
