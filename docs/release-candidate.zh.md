# 在本地审阅 Olav Skills 0.2.2 wheel

在隔离环境中使用提供的 **0.2.2** wheel 运行合成流程。需要 POSIX shell 和
Python 3.11 或更新版本。合成夹具与工具运行无需模型、凭据或设备访问。
[标准安装](installation.zh.md)

## 安装 runtime 并绑定技能

将 `WHEEL` 指向审阅者提供的 wheel，`WHEELHOUSE` 指向存有兼容依赖
wheel 的目录。安装前核对 wheel 的 SHA-256 与审阅者提供的值。以下命令创建
新的工作目录，从空 home 和空项目开始。

```sh
WHEEL=/absolute/reviewed/olav_skills-0.2.2-py3-none-any.whl
WHEELHOUSE=/absolute/reviewed/dependency-wheels
python3 -c 'from pathlib import Path; import hashlib, sys; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())' "$WHEEL"

WORK=$(mktemp -d)
WORK=$(cd "$WORK" && pwd -P)
REVIEW_HOME="$WORK/home"
HARNESS_PROJECT="$WORK/project"
export OLAV_HOME="$WORK/analysis"
mkdir -p "$REVIEW_HOME" "$HARNESS_PROJECT"
cd "$WORK"

python3 -m venv "$WORK/venv"
SKILLS_PYTHON="$WORK/venv/bin/python"
"$SKILLS_PYTHON" -m pip --isolated install --no-index --find-links "$WHEELHOUSE" "$WHEEL"

skills() {
    env -i PATH="$PATH" HOME="$REVIEW_HOME" OLAV_HOME="$OLAV_HOME" \
        "$SKILLS_PYTHON" -I -B -m olav_skills "$@"
}

skills version
skills doctor
skills list
skills install --harness pi --scope project --project "$HARNESS_PROJECT"
```

预期版本为 `0.2.2`，runtime 体检返回 `ok: true`，列出七个技能。示例将 Pi
项目布局写入 `"$HARNESS_PROJECT/.pi/skills"`。每个 `olav-runtime.json` 都记录
版本及 `$SKILLS_PYTHON` 路径。Codex 或 Claude Code 对应使用 `codex` 或
`claude-code`。以下 CLI 步骤无需启动宿主。[项目与用户布局](installation.md)

## 导入合成包

```sh
"$SKILLS_PYTHON" -I -B -m olav_skills.examples.synthetic_walkthrough --home "$OLAV_HOME"

SNAPSHOT=$("$SKILLS_PYTHON" -I -B -c \
    'import json, sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text())["snapshot_id"])' \
    "$OLAV_HOME/import.json")
```

已安装的示例校验并导入六份命令输出，确认台账设备精确集合为 `R1`、`R2`，
并生成含一条 CDP 记录边的 Mermaid 图。导入回执、一条有界 R1 证据记录和库存
报告保存在 `$OLAV_HOME`。后续步骤使用回执里的精确快照 ID。[示例产物](offline-data.md)

## 数据体检、设备观测与拓扑

```sh
skills doctor --data --project "$OLAV_HOME" --snapshot-id "$SNAPSHOT" > data-doctor.json

"$SKILLS_PYTHON" -I -B - "$SNAPSHOT" <<'PY' | skills run analyzer execute_sql > observed-devices.json
import json
import sys

print(json.dumps({
    "sql": "SELECT DISTINCT device_name AS hostname FROM netops.parsed_outputs "
           "WHERE snapshot_id = ? ORDER BY hostname",
    "params": [sys.argv[1]],
    "max_rows": 10,
}))
PY

"$SKILLS_PYTHON" -I -B -c \
    'import json, sys; print(json.dumps({"concept": "all", "snapshot_id": sys.argv[1]}))' \
    "$SNAPSHOT" | skills run topology query_topology > topology-query.json
```

| 输出 | 预期证据 |
| --- | --- |
| `data-doctor.json` | `status: success`、`data_readiness: recorded_queries_available`、同一快照、六条解析记录和两台观测设备。采集和解析覆盖仍为未知。 |
| `observed-devices.json` | 精确快照中的设备集合为 `R1`、`R2`，`has_more: false`，`truncated: false`。查询依据所选快照的解析观测。 |
| `topology-query.json` | 同一快照，一条从 R2 GigabitEthernet1 到 R1 GigabitEthernet2 的 CDP 记录。OSPF 层不可用，`result_contract.layers.ospf` 的 `row_count: null`，其协议关系仍为未知。 |

## 预检变更草案

草案由工程师或宿主编写。以下示例文本仅用于对合成 R2 记录测试结构化请求。

```sh
"$SKILLS_PYTHON" -I -B - "$SNAPSHOT" <<'PY' | skills run analyzer check_change_plan > plan-precheck.json
import json
import sys

print(json.dumps({
    "snapshot_id": sys.argv[1],
    "plan": {
        "schema": "olav.change-plan/v1",
        "targets": [{
            "device": "R2",
            "platform": "cisco_ios",
            "template": "cisco_ios",
            "steps": ["Record the proposed fixture interface description"],
            "rollback": ["Restore the previous fixture interface description"],
            "verification": [{
                "command": "show ip interface brief",
                "expected": "Review the recorded fixture interface state",
            }],
        }],
    },
}))
PY
```

预期 `execution_status: ok`、`checks_outcome: unknown`。记录中的 R2 通过
目标与平台检查，草案包含回退与验证字段。语法、可达性和仿真仍为未知。预检始终
返回 `decision: not_approved`。[预检合同](reference/plan-precheck.md)

## 升级或回退后重新绑定

pip 更换 venv 内的 runtime 和随包资源。要更新已复制的技能，从目标 venv 对
同一宿主、作用域和根目录重新执行 `install`。回退时使用保留的旧 venv，或先在原
venv 重装经审阅的旧 wheel，再重新绑定。逐项核对技能的版本、解释器和归属清单。
安装器拒绝覆盖本地编辑、额外文件、符号链接和其它归属。环境与绑定回退后，已
导入的数据和产物仍保留。[安装与回退详情](installation.md)

## 宿主验收

本地包检查覆盖安装布局及合成数据上的指定脚本调用。此前在 Pi 中测试的 0.2 代码
未通过自然问答验收，留作独立验证的同义问法也未通过。Codex 和 Claude Code 的自然问答
未测。[兼容性和验证层级](reference/compatibility.zh.md)
