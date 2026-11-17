# 构建自定义技能

Skill（技能）是扩展 OLAV Agent 能力的方式。当内置工具不能满足你的需求时，你可以编写自己的技能——一组 Python 函数，让 Agent 学会新的操作。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-04 | `olav agent install <path>` 从本地目录安装 Skill | ✅ v0.10.0 |
    | C-L2-25 | `olav agent install <git-url>` 从 Git 仓库安装 | ✅ v0.10.0 |
    | C-L2-06 | `olav agent install --merge-into` 追加工具到已有 workspace | ✅ ⚠️ v0.10.0 |
    | C-L2-37 | `requires_packages` 声明依赖自动创建隔离 venv | ✅ v0.10.0 |

---

## 什么时候需要自定义技能？

- 你有内部 API（工单系统、CMDB、监控平台），想让 OLAV 能查询
- 你想封装常用的运维操作（批量配置、健康检查脚本）
- 你有特定的数据处理逻辑需要自动化

---

## 技能结构（v0.11+）

从 v0.11.0 起，OLAV 采用 **scripts/ 目录 + SKILL.md** 作为技能的标准结构，与 deepagents 规范对齐：

```
my-skill/
├── SKILL.md            ← 技能声明：名称、工具白名单、脚本列表
├── scripts/
│   ├── list_items.py   ← 每个脚本一个功能
│   └── create_item.py
└── prompts/
    └── system.md       ← （可选）sub-agent 系统 prompt
```

Agent 通过 `execute_skill_script` 调用脚本，而不是直接暴露为 LangChain `@tool`。这一机制提供路径围栏、审计日志和对小模型友好的语义接口，详见 [安全模型 →](../concepts/security-model.md#技能执行安全execute_skill_script-机制)。

### SKILL.md — 技能声明

```yaml
---
name: my-skill
description: "查询工单系统的工具集"
tools:
  - olav_recall_memory
  - execute_skill_script   # 必须声明，才能调用 scripts/ 下的脚本
scripts:
  - name: list_items
    file: list_items.py
    description: "列出指定团队的未关闭工单。Args: team."
  - name: create_item
    file: create_item.py
    description: "创建新工单。Args: title, team, priority."
---

## My Skill

查询和管理工单系统的工具集。

## Scripts

通过 `execute_skill_script("my-skill", "list_items.py", {"team": "platform"})` 调用。
```

### Harness 能力 opt-in（v0.20+）

在 `SKILL.md` 的 `metadata:` 块中声明以下 flag，即可为该 Agent 启用对应的 Harness 中间件，无需修改 system prompt：

```yaml
---
name: my-skill
description: "..."
tools:
  - execute_skill_script
  - write_todos             # enable_todo_list 时必须列出
metadata:
  rubric_middleware: true   # 输出自评 + 自动重试（有固定输出模板时推荐）
  enable_todo_list: true    # 多步骤写操作进度跟踪（有写操作工作流时推荐）
  type: agent
  version: 1.0.0
  category: my-category
---
```

| Flag | 需要额外工具 | 推荐场景 |
|---|---|---|
| `rubric_middleware: true` | 无 | 有固定 Markdown 模板的报告/变更类 Agent |
| `enable_todo_list: true` | `write_todos` 加入 `tools:` | 有多步骤写操作（API 写入、批量变更）的 Agent |

详见 [Agent Harness — 质量保障中间件 →](../concepts/agent-harness.md#质量保障中间件v020)

---

### scripts/list_items.py — 脚本标准格式（JSON stdin）

```python
"""列出指定团队的未关闭工单。"""
import json, sys
import requests

def list_items(team: str = "") -> dict:
    resp = requests.get(
        "https://jira.internal/api/search",
        params={"jql": f"assignee in membersOf('{team}') AND status != Closed"},
        headers={"Authorization": "Bearer YOUR_TOKEN"}
    )
    issues = resp.json().get("issues", [])
    return {"count": len(issues), "items": issues}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(list_items(**args)))
```

脚本约定：从 `sys.stdin` 读 JSON 参数，将结果以 JSON 写入 `stdout`。

!!! tip "第三方脚本：argparse 模式"
    如果集成使用 `argparse` 编写的现有脚本，在 `scripts:` 条目中加 `argv: true`，OLAV 会自动切换为 `--key value` CLI 参数传递：
    ```yaml
    scripts:
      - name: legacy_report
        file: legacy_report.py
        argv: true
    ```
    安装第三方技能时，`olav agent install` 会自动检测 argparse 用法并设置此标志。

### MANIFEST.yaml — 安装元数据（仍然需要）

SKILL.md 描述运行时行为；`workspace.yaml`（或旧式 `MANIFEST.yaml`）提供安装器所需的包依赖和安装目标：

```yaml
kind: Agent
name: my-skill
version: "0.1.0"
description: "查询工单系统的工具集"
route_keywords:
  - ticket
  - jira
  - issue
  - 工单
```

### tools.py — 定义 Agent 可以调用的工具（旧式，v0.10 及以前）

```python
from langchain_core.tools import tool

@tool
def get_open_tickets(team: str) -> list[dict]:
    """查询指定团队的所有未关闭工单。

    Args:
        team: 团队名称，例如 "platform" 或 "network"
    """
    import requests
    resp = requests.get(
        "https://jira.internal/api/search",
        params={"jql": f"assignee in membersOf('{team}') AND status != Closed"},
        headers={"Authorization": "Bearer YOUR_TOKEN"}
    )
    return resp.json()["issues"]

@tool
def get_ticket_detail(ticket_id: str) -> dict:
    """获取单个工单的详细信息。

    Args:
        ticket_id: 工单编号，例如 "OPS-1234"
    """
    import requests
    resp = requests.get(
        f"https://jira.internal/api/issue/{ticket_id}",
        headers={"Authorization": "Bearer YOUR_TOKEN"}
    )
    return resp.json()
```

!!! tip "工具函数的 docstring 非常重要"
    LLM 根据函数的 docstring 来决定何时调用这个工具、如何传递参数。写清晰的描述和参数说明能显著提升 Agent 的准确性。

---

## 安装技能

### 从本地目录安装

```bash
olav agent install ./my-skill/
```

输出：
```
installed my-skill v0.1.0 → .olav/workspace/my-skill/
```

验证：

```bash
olav skill list                  # 列出所有已安装技能
olav skills info my-skill        # 查看技能详情
```

### 从 Git 仓库安装

```bash
olav agent install https://github.com/your-org/my-skill
```

仓库根目录需要包含 `MANIFEST.yaml` 或 `workspace.yaml`。

### 追加到已有技能

如果你想给一个已存在的 Agent 添加新工具，而不是创建新的 Agent：

```bash
olav agent install ./extra-tools/ --merge-into my-skill
```

这会将 `extra-tools/` 中的 `tools.py` 追加到 `my-skill` 的工作空间中。

---

## 使用技能

安装完成后，直接用自然语言提问即可：

```bash
olav "platform 团队有多少个未关闭的工单？"
olav "查看 OPS-1234 的详情"
```

OLAV 会根据 `route_keywords` 自动将问题路由到你的技能。

---

## 声明 Python 依赖

如果你的技能需要额外的 Python 包，在 `MANIFEST.yaml` 中声明：

```yaml
requires_packages:
  - requests>=2.31.0
  - paramiko
```

OLAV 安装技能时会自动创建一个隔离的虚拟环境（`.venv/`），安装声明的包，不会影响主环境。

---

## 卸载技能

直接删除工作空间目录即可：

```bash
rm -rf .olav/workspace/my-skill/
```

---

## 进阶：技能开发建议

1. **函数命名要清晰**：`get_open_tickets` 比 `query_data` 好——LLM 能更准确地理解何时调用
2. **docstring 写完整**：包括功能描述、参数说明、返回值格式
3. **route_keywords 覆盖常见表述**：用户可能说"工单""ticket""issue"，都应该覆盖
4. **错误处理要友好**：返回有意义的错误信息，而不是让 Agent 面对 500 报错
5. **先小后大**：从 1-2 个工具函数开始，验证可用后再扩展

---

## 将工具注入 Core Agent

默认情况下，技能的工具只有在该技能的 workspace 激活时才可用。通过 `inject_into_core`，可以让工具在 **Core Agent** 中始终可用——无论当前 workspace 是什么，非常适合需要随时调用的常用工具。

### 配置方法

在技能的 `workspace.yaml` 中添加 `inject_into_core` 块：

```yaml
name: my-skill
version: 1.0.0
description: 我的自定义技能
route_keywords:
  - 工单
  - issue

inject_into_core:
  tools:
    - execute_cli          # 将此工具注入 Core Agent
    - search_commands
  description: "注入 Core Agent 的技能工具"
```

### 工作原理

运行 `olav agent install my-skill` 时，OLAV 会：

1. 读取 `workspace.yaml` 中的 `inject_into_core` 块
2. 将列出的工具函数追加到 `.olav/workspace/core/AGENT.md`
3. 在 `.olav/workspace/core/SKILL.md` 中更新注入清单

下次调用 Core Agent 时，这些工具将出现在其工具列表中。

### 移除注入的工具

卸载技能时，其注入的工具会自动从 Core Agent 中移除。也可以手动移除：

```bash
olav skill uninstall my-skill
```

### 最佳实践

- 只注入**通用性强**的工具——避免用冷门领域工具堆满 Core Agent
- 保持注入的工具名称唯一——与现有工具冲突时会跳过并发出警告
- `inject_into_core` 适合 `execute_cli`、`search_commands` 或自定义数据访问工具
