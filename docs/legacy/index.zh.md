# OLAV Platform 🐺

> **O**nline **L**ogical **A**nalytical **V**ertex for Agentic Operations
>
> 用自然语言操控你的基础设施。

**v0.26.0** — [立即开始 →](getting-started/installation.md)

!!! abstract "功能声明 · [C-L1-01 ✅ v0.10.0]"
    本页为平台 Level 1 声明。验证状态见 [功能声明注册表 →](reference/claim-registry.md)

---

## OLAV 是什么？

OLAV 是一个 AI 原生的智能运维平台。你只需要用自然语言描述需求，OLAV 会自动选择合适的 AI Agent 来完成任务——查询数据、执行命令、分析日志、生成报告，无需手写 SQL 或记忆复杂的 CLI 参数。

```bash
olav "列出所有可用的 Agent"
olav "有哪些数据表？"
olav --agent core "执行健康检查"
olav log list   # 查看所有操作记录
```

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **自然语言查询** | 用日常语言提问，OLAV 自动转换为 SQL / API 调用并返回结果 |
| **多 Agent 协作** | 不同 Agent 专注不同领域（快速查询、配置管理、代码执行），自动路由到最合适的 Agent |
| **连接任意 API** | 一行命令注册 OpenAPI 服务，立即可用自然语言查询 |
| **完整审计日志** | 每一次操作自动记录，支持 `olav log list/errors/show` 回溯查看 |
| **自我改进** | 从历史错误中学习，Agent 会越用越好 |
| **团队协作** *(企业版)* | 多用户 token / LDAP 认证与共享工作空间由 [olav-ent](getting-started/installation.md) 提供。开源版为单用户（OS 身份即 admin）；RBAC 引擎本身保留在开源版 |
| **多层缓存** | LLM SQLiteCache + SemanticCache 双缓存，命中时 0 token 消耗、<1ms 返回（实测 2000x+ 加速） |
| **Agent Harness** | 三层代码沙箱 + HITL 危险命令审批 + Prompt 注入扫描 + 全量审计，生产安全可控 |

---

## 三种使用方式

=== "命令行 (CLI)"
    ```bash
    olav "搜索知识库中的部署流程"
    ```
    适合脚本集成和快速一次性查询。

=== "交互终端 (TUI)"
    ```bash
    olav
    ```
    启动交互式终端，支持多轮对话，适合探索和分析。

=== "Web 界面 · 企业版"
    ```bash
    pip install olav-ent          # Web 界面 + HTTP API 属于企业版扩展
    olav service web start
    # 浏览器访问 http://localhost:2280
    ```
    团队共享的浏览器界面，支持实时流式响应——**需要 [olav-ent](getting-started/installation.md)**。开源版 OLAV 的 `olav service` 仅提供 syslog 接收器。

---

## 快速导航

<div class="grid cards" markdown>

- :rocket: **[快速开始](getting-started/installation.md)** — 5 分钟完成安装，运行第一个查询
- :electric_plug: **[使用指南](guides/connect-a-service.md)** — 连接服务、构建技能、管理工作空间
- :bulb: **[核心概念](concepts/agents-and-skills.md)** — 理解 Agent、Skill、Workspace 的设计思路
- :books: **[参考手册](reference/cli.md)** — CLI 命令、配置项、HTTP API 完整参考

</div>
