# 使用 Agent

OLAV 的核心是多个 AI Agent 协作——每个 Agent 专注不同领域，你可以让 OLAV 自动选择，也可以手动指定。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-02 | `olav list` 列出所有可用 Agent | ✅ v0.10.0 |
    | C-L2-03 | `olav workspace list/use` 切换活跃 workspace | ✅ v0.10.0 |
    | C-L2-26 | `olav --auto-approve` 跳过工具调用确认 | ✅ v0.10.0 |

---

## 查看可用的 Agent

```bash
olav list
```

输出示例：
```
Available Agents:

  • config
    Config & System Agent — 服务注册、数据导入、工作空间健康检查
    Location: .olav/workspace/config/

  • core
    Core OLAV platform agent — 代码执行、SQL 查询、平台操作
    Location: .olav/workspace/core/

  • core  (active)
    Core Agent — 快速数据查询、CLI 执行、知识库搜索
    Location: .olav/workspace/core/
```

紧凑视图：

```bash
olav workspace list
```

```
* core                   -       (user)    ← * 表示当前活跃
  config                -       (user)
  core                  -       (user)
  venv-test             v1.0.0  (managed) ← managed = 通过 olav agent install 安装
```

---

## 内置 Agent 一览

| Agent | 角色 | 适用场景 |
|-------|------|---------|
| `core` *(默认)* | 快速查询助手 | "数据库里有哪些表？""最近有什么错误？"——一步到位的查询 |
| `config` | 系统管理员 | 注册外部服务、生成技能、工作空间健康检查 |
| `core` | 全能工程师 | 执行 Python/SQL/Shell、Web 搜索、复杂数据分析 |

---

## 自动路由（默认行为）

不指定 `--agent` 时，OLAV 会根据你的问题内容自动选择最合适的 Agent：

```bash
olav "列出所有 Agent"            # → 自动路由到 Core Agent
olav "数据库里有哪些表？"         # → 自动路由到 Core Agent
olav "执行一次健康检查"           # → 自动路由到 Core Agent
```

路由依据是每个 Agent 在 `MANIFEST.yaml` 中定义的 `route_keywords`。默认活跃的 `core` Agent 适合快速查询。

!!! note "平台命令不经过路由"
    `olav version`、`olav list`、`olav log list` 等内置命令是直接执行的，不会经过 Agent 路由。

---

## 手动指定 Agent

当你明确知道要用哪个 Agent 时：

```bash
olav --agent services "列出已注册的服务"
olav --agent core "数据库里有哪些表？"
olav --agent core "search: deployment procedure"
```

简写：

```bash
olav -a config "列出已注册的服务"
```

---

## 交互模式

启动交互式终端，支持多轮对话，Agent 会保持上下文：

```bash
olav               # 使用默认 Agent 启动
olav --agent admin # 使用指定 Agent 启动
```

在交互模式中，Agent 记得之前的对话内容，你可以自然地追问：

```
> 数据库里有哪些表？
> 最大的表有多少行？
> 导出这些统计数据到 CSV
```

---

## 切换活跃 Agent

活跃 Agent 是你不指定 `--agent` 时默认使用的那个：

```bash
olav workspace list           # 查看所有 Agent，* 标记表示当前活跃
olav workspace use config     # 切换到 config Agent
olav workspace use core      # 切回 Core Agent
```

---

## 在 Web UI 中切换 Agent（v0.21.0+）

!!! info "企业版功能 (olav-ent)"
    Web UI 及其 HTTP API（`/agents`、`/threads`、`/runs/stream`）属于企业版扩展 `olav-ent`（`pip install olav-ent`）。开源版 OLAV 请改用 CLI/TUI 的 `--agent` 切换 agent（见上文各节）。

Web UI 左下角有一个 agent 下拉框，列出 `/agents` 端点返回的所有顶级 agent：

```
┌────── Sidebar ──────┐
│  + New Chat        │
│  Thread list…       │
│                     │
│  Memory Graph ↗     │
│  ● API healthy      │
│  [ agent-select ▼ ] │   ← 点这里切换
│      core (default) │
│      audit          │
│      command_learner│
│      netops         │
│      topology       │
└─────────────────────┘
```

### 语义

1. **`core` 永远置顶** — v0.21.0-rc4 后端强制把平台 agent 排在第一位，
   浏览器自动选中第一项并标注 `(default)`，无论后面装了多少扩展 agent。
2. **每个 thread 绑定一个 agent** — 新建 thread 时记录 dropdown 当前值，
   切换回旧 thread 时 dropdown 自动跟随回去。**同一 thread 的多轮对话保持
   同一个 agent**，避免跨 agent 串线。
3. **刷新浏览器不丢状态** — `currentThreadId`、thread 列表、每个 thread 绑定
   的 `agentId` 都写进 `localStorage` 键 `olav.webui.state.v1`。F5 后 dropdown
   自动跳回上一次的选择。

### 两种使用场景

**场景 A — 想针对某个 agent 开新对话**

1. 先从 dropdown 选 agent（如 `netops`）
2. 点 **+ New Chat**
3. 开始聊天 —— 整条 thread 都走 `netops`

**场景 B — 想在已有 thread 里换 agent**

1. 直接改 dropdown（如从 `core` → `audit`）
2. 下一条消息发往 `audit`，且这条 thread 的绑定被**永久改成** `audit`
3. 后续切走再切回，dropdown 自动停在 `audit`

### 背后做了什么

浏览器把 dropdown 当前值作为 `assistant_id` 带进每一次
`POST /threads/<id>/runs/stream` 请求；服务端 `stream_run()` 走
`get_agent(assistant_id)` 从 per-id cache 取对应的 OLAVAgent（不是
以前的单例），再把 `OlavRunContext(agent_id=...)` 传进 graph，audit
事件里的 `agent_id` 字段就是 dropdown 的选择。

可以用 curl 直接验证：

```bash
TOKEN=$(cat .olav/databases/.auth_token)
TID=$(curl -sS -X POST http://localhost:2280/threads \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["thread_id"])')

# 显式切到 netops：
curl -sS -X POST "http://localhost:2280/threads/$TID/runs/stream" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"assistant_id":"netops","input":{"messages":[{"role":"user","content":"list devices"}]}}'

# 查 audit 确认真的走了 netops 的 graph：
python3 -c "import duckdb; c=duckdb.connect('.olav/databases/audit.duckdb', read_only=True); \
print(c.execute(\"SELECT agent_id, event_type FROM audit_events \
WHERE run_id=(SELECT run_id FROM audit_events WHERE event_type='user_input_received' \
ORDER BY timestamp DESC LIMIT 1)\").fetchall())"
# → [('netops', 'user_input_received'), ('netops', 'assistant_output_final')]
```

---

## 跳过工具确认

默认情况下，Agent 调用工具前会征求你的确认。如果你信任操作（比如只读查询），可以跳过：

```bash
olav --auto-approve "执行健康检查"
```

!!! warning "谨慎使用"
    `--auto-approve` 会自动批准所有工具调用，包括写入操作。建议只在你确定查询是只读的情况下使用。
