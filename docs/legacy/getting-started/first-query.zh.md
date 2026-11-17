# 运行你的第一个查询

安装完成后，你有三种方式与 OLAV 交互。让我们从最简单的开始。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-02 | `olav list` 列出所有可用 Agent | ✅ v0.10.0 |
    | C-L2-14 | `olav` 无参数启动 TUI 交互模式 | ✅ v0.10.0 |
    | C-L2-15 | `olav --session <id>` 恢复会话 | ✅ v0.10.0 |
    | C-L2-16 | `olav service web start` 启动 Web 界面 | ✅ v0.10.0 |
    | C-L2-08 | `olav log list` 查看操作历史 | ✅ v0.10.0 |

---

## 命令行模式：一句话提问

最快的方式是直接在命令行输入自然语言问题：

```bash
olav "列出所有 Agent"
olav "数据库里有哪些表？"
olav "执行一次健康检查"
```

OLAV 会自动将你的问题路由到最合适的 Agent。默认的 `core` Agent 适合快速查询。

如果你想查询 **OLAV 平台本身**的信息，使用内置命令：

```bash
olav version                          # 查看平台版本
olav list                             # 查看所有可用 Agent
olav --agent services "列出已注册的服务"   # 查看已连接的外部服务
```

你也可以用 `--agent`（或 `-a`）直接指定某个 Agent：

```bash
olav --agent core "run df -h"                              # 执行系统命令
olav --agent core "run this python: import sys; print(sys.version)"  # 执行 Python 代码
```

---

## 交互模式：多轮对话

不带参数运行 `olav` 即可进入交互式终端（TUI），支持多轮对话：

```bash
olav                      # 默认 Agent 启动
olav --agent admin       # 指定 Agent 启动
olav --no-splash          # 跳过启动横幅
```

在交互模式中，你可以像聊天一样自然提问，Agent 会保持上下文：

```
> 有多少个 Agent 可用？
> 最近一小时我做了什么？
> 显示最近的错误
```

### 欢迎页展示你的真实状态

TUI 首屏不是随机提示语，而是你这套安装**此刻的真实状态**：

- **真实发现**：*"⚠ 嵌入后端不可用——记忆/召回功能受限，运行 `olav doctor`
  查看详情"*，或 *"netops 还没有设备数据——导入一份快照即可开始提问"*。
- **会话延续**：回访用户会看到 *"Welcome back — last time (2h ago):
  'why is R1's BGP flapping?'"*，可以直接接着上次的工作继续。

一切健康、无事可报时，才会显示普通的使用技巧。

### 交互模式中的斜杠命令

| 命令 | 功能 |
|------|------|
| `/help` | 显示所有可用命令 |
| `/doctor` | 不离开 TUI 直接运行零 LLM 健康体检（脚手架 + LLM + 嵌入） |
| `/workspace <name>` | 切换到其他 agent 工作区（如 `/workspace admin`） |
| `/clear` | 重置当前对话、清屏 |
| `/model <name>` | 会话内临时切换 LLM 模型（不修改配置文件） |
| `/trace-review` | 一次性手动审查近期失败记录（大部分能力已被常驻的 L1 捕获取代——参见[自我改进循环](../concepts/self-improving-loop.md)） |
| `/quit` `/exit` `/q` | 退出交互模式 |

### 交互模式中的特殊语法

| 语法 | 功能 | 示例 |
|------|------|------|
| `@file.txt` | 将文件内容注入到提问中 | `@config.yaml 这个配置有什么问题？` |
| `!command` | 直接执行 Shell 命令（不经过 Agent） | `!ls -la exports/` |

!!! tip "自我改进是自动的"
    在你使用 OLAV 的过程中，每次成功的工具调用都会被 L1 中间件捕获并写入 LanceDB 记忆，curator 会定期把重复出现的模式蒸馏成可复用的 directive（L2）。两者都通过 AutoRecall 自动注入到 prompt 里——所以系统会**从你的使用中自然变聪明**，不需要任何手工 review。详见[自我改进循环](../concepts/self-improving-loop.md)，包括如何用团队 directive 扩展。

### 恢复之前的会话

```bash
olav --session <session-id>
```

会话存储在 `~/.olav/sessions/`，默认 24 小时后过期。

---

## Web 界面：浏览器访问

!!! info "企业版功能 (olav-ent)"
    Web 界面与 HTTP API 属于企业版扩展 `olav-ent`（`pip install olav-ent`），不包含在开源安装中——请改用上面的 CLI 或交互式 TUI。

如果你更习惯图形界面，或需要在团队内共享（已安装 `olav-ent`）：

```bash
olav service web start
```

然后浏览器访问 `http://localhost:2280`，即可使用与 CLI 相同的 Agent 能力，支持实时流式响应。

详见 [后台服务 →](../guides/services.md)

---

## 了解你的 Agent

运行以下命令查看当前工作空间中有哪些 Agent 可用：

```bash
olav list
```

```
Available Agents:

  • config
    Config & System Agent — 服务注册、数据导入、工作空间健康检查
    Location: .olav/workspace/config/

  • core
    Core OLAV platform agent — 代码执行、SQL 查询、Shell 命令
    Location: .olav/workspace/core/

  • quick  (active)
    Core Agent — 快速数据查询、CLI 执行、知识库搜索
    Location: .olav/workspace/quick/
```

每个 Agent 擅长不同的事情：

| Agent | 擅长做什么 | 使用场景 |
|-------|-----------|---------|
| `quick` *(默认)* | 快速查询，一步到位 | "数据库里有哪些表？""最近有什么错误？" |
| `config` | 管理服务注册、工作空间健康检查 | 连接新 API、诊断工作空间问题 |
| `core` | 执行 Python/SQL/Shell、Web 搜索 | 数据分析、自动化脚本、复杂查询 |

---

## 查看操作历史

OLAV 自动记录每一次操作。查看最近的活动：

```bash
olav log list
```

```
Recent Audit Runs (last 24h):

  [2026-04-03 15:35:52] 7f2693b8  completed     agent=core
  [2026-04-03 15:35:25] 2e31144b  completed     agent=config
  [2026-04-03 15:34:20] 4e314755  completed     agent=quick
```

详见 [审计与日志 →](../guides/audit-and-logs.md)

**下一步：** [了解工作原理 →](how-it-works.md)
