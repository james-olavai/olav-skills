# 功能声明注册表

本页是 OLAV 平台所有功能声明（Claim）的唯一索引。每条声明对应一个可验证的平台能力。

!!! info "企业版声明（0.25.0 起迁移至 olav-ent）"
    下面若干条声明描述的能力从 **0.25.0** 起随 `olav-ent` 分发，**不再包含在开源 `olav` / `olav-netops` 中**：
    Web 服务、HTTP API 与浏览器界面（**C-L2-16、C-L2-29、C-L2-30、C-L2-42、C-L4-07**），
    Daemon 加速器（**C-L2-17**），
    以及多用户 token / LDAP 认证与用户管理（**C-L2-23、C-L2-28、C-L2-44、C-L4-01、C-L4-02**）。
    开源版 `olav service` 仅提供 syslog 接收器；身份默认 `auth.mode=none`（OS 用户即 admin）。这些功能需安装 `olav-ent`（`pip install olav-ent`）。

**方法论：** Doc-Driven Testing（详见 `dev_docs/22. WEB_AND_DOCS_SITE.md` §7）

```
文档声明功能 → CLI 验证 → E2E 测试 → 验证通过 or 收窄/删除
```

**状态说明：**

- ✅ Verified — 已通过 CLI 端到端验证，记录在 `dev_docs/23. CLI_VERIFICATION_LOG.md`
- ⬜ Pending — 代码已实现，待 CLI 验证
- ⚠️ Narrowed — 部分条件下可用，已收窄声明范围
- 🔶 Env-Blocked — 需要外部环境（运行中的服务、CLAB 等）才能验证

---

## Level 1 — 平台核心能力

| ID | 声明 | 文档页 | 状态 |
|----|------|--------|------|
| C-L1-01 | OLAV 让你用自然语言操控基础设施 | [首页](../index.md) | ✅ | v0.10.0 |

---

## Level 2 — 功能级声明

### 基础命令

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-01 | CLI 正确报告版本号和系统信息 | `olav version` | [安装](../getting-started/installation.md) | ✅ | v0.10.0 |
| C-L2-02 | CLI 列出所有可用 Agent 及描述 | `olav list` | [第一个查询](../getting-started/first-query.md) | ✅ | v0.10.0 |
| C-L2-03 | 列出并切换活跃 workspace | `olav workspace list/use` | [管理工作空间](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-13 | 初始化项目目录结构 | `olav init` | [安装](../getting-started/installation.md) | ✅ | v0.10.0 |
| C-L2-27 | 重置 Agent 对话历史 | `olav reset --agent <name>` | [CLI 命令](cli.md) | ✅ | v0.10.0 |

### 技能管理

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-04 | 从本地目录安装 Skill | `olav agent install <path>` | [构建技能](../guides/build-a-skill.md) | ✅ | v0.10.0 |
| C-L2-25 | 从 Git URL 安装 Skill | `olav agent install <url>` | [构建技能](../guides/build-a-skill.md) | ✅ | v0.10.0 |
| C-L2-06 | 追加工具到已有 workspace | `olav agent install --merge-into` | [构建技能](../guides/build-a-skill.md) | ✅ ⚠️ | v0.10.0 |
| C-L2-36 | 列出和查看已安装技能状态 | `olav skill list/status` | [管理工作空间](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-37 | Skill 声明 Python 依赖自动创建隔离 venv | `requires_packages` in MANIFEST | [构建技能](../guides/build-a-skill.md) | ✅ | v0.10.0 |

### 服务注册

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-19 | 一行命令注册 OpenAPI 服务 | `olav registry register <url>` | [连接服务](../guides/connect-a-service.md) | ✅ | v0.10.0 |
| C-L2-20 | DevOps agent 生成环境感知型自动化脚本 | `olav --agent devops "写脚本..."` | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |

### 审计与日志

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-08 | 查询审计日志、列出最近操作 | `olav log list/errors` | [审计与日志](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-22 | 导出审计数据为训练格式 | `olav log export trajectory/sft/atif` | [审计与日志](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-12 | 多用户并发 Audit 无写冲突 | 5 并发 `olav log list` | [审计与日志](../guides/audit-and-logs.md) | ✅ | v0.10.0 |

### 后台服务

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-16 | Web 服务提供浏览器界面和 REST API | `olav service web start` | [后台服务](../guides/services.md) | ✅ | v0.10.0 |
| C-L2-17 | Daemon 加速 CLI 响应 | `olav service daemon start` | [后台服务](../guides/services.md) | ✅ | v0.10.0 |
| C-L2-18 | Syslog 接收器接收网络设备日志 | `olav service logs start` | [后台服务](../guides/services.md) | ✅ | v0.10.0 |

### HTTP API

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-29 | SSE 流式查询接口 | `POST /runs/stream` | [HTTP API](http-api.md) | ✅ | v0.10.0 |
| C-L2-30 | 多轮对话线程管理 | `POST /threads` | [HTTP API](http-api.md) | ✅ | v0.10.0 |

### 交互模式

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-14 | TUI 交互模式支持多轮对话 | `olav`（无参数） | [第一个查询](../getting-started/first-query.md) | ✅ | v0.10.0 |
| C-L2-15 | 恢复之前的会话 | `olav --session <id>` | [第一个查询](../getting-started/first-query.md) | ✅ | v0.10.0 |
| C-L2-24 | /trace-review 分析失败模式写入记忆 | `/trace-review` | [自我改进](../concepts/self-improving-loop.md) | ✅ | v0.10.0 |
| C-L2-26 | 跳过工具调用确认 | `olav --auto-approve` | [使用 Agent](../guides/using-agents.md) | ✅ | v0.10.0 |

### 知识库与记忆

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-21 | 知识库索引文档并支持语义搜索 | `olav kb sync` / `olav kb search` | [知识库](../guides/knowledge-base.md) | ✅ | v0.10.0 |

### 用户与认证

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-23 | 管理员创建/管理用户和令牌 | `olav admin "add-user..."` | [用户与角色](users-and-roles.md) | ✅ | v0.10.0 |
| C-L2-28 | 支持多种认证模式 | `auth.mode` 配置 | [安全模型](../concepts/security-model.md) | ✅ | v0.10.0 |

### Core Agent 工具

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-31 | Core Agent 执行 Python 代码 | `olav --agent core "run python:..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-32 | Core Agent 执行 SQL 查询 | `olav --agent core "what tables..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-33 | Core Agent Web 搜索 | `olav --agent core "search:..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-34 | Core Agent 执行 Shell 命令 | `olav --agent core "run: df -h"` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-35 | Admin Agent 工作空间健康检查 | `olav --agent admin "health check"` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |

### 导出与集成

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-10 | 导出 Claude 兼容 plugin | `olav export claude-plugin` | [CLI 命令](cli.md) | ✅ | v0.10.0 |
| C-L2-11 | 自动截断大型 API 响应 | Python SDK | [CLI 命令](cli.md) | ✅ | v0.10.0 |

### 配置

| ID | 声明 | CLI 指令 | 文档页 | 状态 | 版本 |
|----|------|---------|--------|------|------|
| C-L2-38 | 为不同 Agent 指定不同 LLM 模型 | `agent_overrides` in api.json | [配置](configuration.md) | ✅ | v0.10.0 |
| C-L2-39 | 环境变量覆盖配置文件 | `OLAV_LLM_*` | [配置](configuration.md) | ✅ | v0.10.0 |
| C-L2-40 | `olav log show <run-id>` 显示完整事件序列 | `olav log show <id>` | [审计与日志](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-41 | 服务管理：status / start --all / stop --all | `olav service status/start --all/stop --all` | [后台服务](../guides/services.md) | ✅ | v0.10.0 |
| C-L2-42 | Web API 自省：/openapi.json、/docs、/threads/search | `GET /openapi.json` `GET /docs` | [HTTP API](http-api.md) | ✅ | v0.10.0 |
| C-L2-43 | `olav workspace status` 显示 Agent 状态 | `olav workspace status` | [管理工作空间](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-44 | Admin rotate-token 与 add-user --expires | `olav admin "rotate-token <n>"` | [用户与角色](users-and-roles.md) | ✅ | v0.10.0 |
| C-L2-45 | 审计数据库可直接通过 DuckDB 查询 | `duckdb .olav/databases/audit.duckdb` | [审计与日志](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-46 | `/model <name>` 会话内切换 LLM 模型 | TUI: `/model gpt-4o-mini` | [CLI 参考](cli.md) | ✅ | v0.10.0 |
| C-L2-47 | `@file.txt` 文件内容注入 | TUI: `@/path/to/file.txt` | [CLI 参考](cli.md) | ✅ | v0.10.0 |
| C-L2-48 | `!command` Shell 透传 | TUI: `!echo hello` | [CLI 参考](cli.md) | ✅ | v0.10.0 |
| C-L2-49 | `--sandbox modal/daytona/runloop` 远程沙箱 flag | `olav --sandbox modal version` | [Agent Harness](../concepts/agent-harness.md) | ✅ | v0.10.0 |
| C-L2-50 | `olav registry list/refresh/status` | `olav registry list` | [连接服务](../guides/connect-a-service.md) | ✅ | v0.10.0 |
| C-L2-51 | `olav config evolve --list/--approve` | `olav config evolve --list` | [CLI 参考](cli.md) | ✅ | v0.10.0 |

---

## NetOps 扩展声明

### 快速启动

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-01 | `/netops_init --dry-run` 通过环境检查 | `/netops_init --dry-run` | [快速启动](../netops/quick-start/index.md) | ✅ | v0.10.0 |
| C-NE-02 | `olav list` 包含 `ops` 和 `ops-lab` agent | `olav list` | [快速启动](../netops/quick-start/index.md) | ✅ | v0.10.0 |
| C-NE-03 | `/netops_init` 填充 `netops.parsed_outputs` | `/netops_init` | [快速启动](../netops/quick-start/index.md) | ✅ | v0.10.0 |
| C-NE-04 | `/netops_init` 从 LLDP/CDP 生成拓扑链路 | `/netops_init` | [快速启动](../netops/quick-start/index.md) | ✅ | v0.10.0 |

### 概念与配置

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-05 | 快照生成跨表共享的唯一 `snapshot_id` | `execute_sql` | [概念](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-06 | 自定义 TextFSM 模板优先于 NTC 内置模板 | 手动测试 | [概念](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-07 | `CommandRegistry.reload()` 无需重启即可加载新模板 | Python API | [概念](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-08 | `netops.devices` 含 hostname, ip_address, platform | `DESCRIBE netops.devices` | [数据库结构](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-09 | `v_bgp_neighbors_auto` 从 parsed_outputs 自动生成 | `SELECT * FROM v_bgp_neighbors_auto` | [数据库结构](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-10 | `netops.topology_links` 含 `discovery_protocol` | `SELECT discovery_protocol FROM netops.topology_links` | [数据库结构](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-11 | `netops.raw_output_store` 保存最新原始 CLI 输出 | `SELECT * FROM netops.raw_output_store LIMIT 5` | [数据库结构](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-12 | `nornir/hosts.yaml` 定义设备清单 | 手动测试 | [配置](../netops/concepts/configuration.md) | ✅ | v0.10.0 |
| C-NE-13 | `blacklisted_commands.yaml` 支持正则；无效正则被跳过 | 手动测试 | [配置](../netops/concepts/configuration.md) | ✅ | v0.10.0 |
| C-NE-14 | `cron_schedules.yaml` 自定义 trace_learner 计划 | `crontab -l` | [配置](../netops/concepts/configuration.md) | ✅ | v0.10.0 |

### Core Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-15 | 自然语言查询返回快照数据结果 | `olav "R1 BGP 邻居？"` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-16 | 黑名单命令被 `execute_cli` 拒绝 | 手动测试 | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-17 | `/learn_cmd` 执行命令并生成 TextFSM 模板 | `/learn_cmd "show version" -d R1` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-18 | `take_snapshot` 触发采集并生成新 snapshot_id | `take_snapshot` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-19 | Core Agent 对复杂分析建议使用 Ops Agent | 复杂查询 | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |

### Ops Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-20 | Ops Agent 复杂分析路由到 Analysis 子 Agent | `olav --agent netops "分析..."` | [Ops Agent](../netops/netops-agent/index.md) | ✅ | v0.10.0 |
| C-NE-21 | Ops Agent 设备探测路由到 Probe 子 Agent | `olav --agent netops "探测..."` | [Ops Agent](../netops/netops-agent/index.md) | ✅ | v0.10.0 |
| C-NE-22 | What-If 仿真：移除节点后的影响范围 | `olav --agent netops "如果 R1 挂了..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-23 | analyzer 只读（`execute_sql` / `generate_change_plan`），不访问实时设备 | 文件检查 | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-24 | `execute_cli_parallel` 白名单验证 | 内部工具 | [Collector](../netops/netops-agent/collector.md) | ✅ | v0.10.0 |
| C-NE-25 | 设备名限制为 `[a-zA-Z0-9_\-.]` | 手动测试 | [Collector](../netops/netops-agent/collector.md) | ✅ | v0.10.0 |
| C-NE-26 | `diff_topology_drift` 检测链路状态变化 | `olav --agent netops "什么变了..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-27 | `diff_sql_state` 跨快照对比任意表 | `olav --agent netops "对比..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |

### Lab Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-28 | `deploy_lab` 从快照数据构建 ContainerLab 拓扑 | `olav --agent netops-lab "部署实验室"` | Lab Agent (enterprise) | 🔶 | v0.10.0 |
| C-NE-29 | Lab Agent 推送 SRL 配置并验证 BGP/OSPF 收敛 | `olav --agent netops-lab "推送配置并验证"` | Lab Agent (enterprise) | 🔶 | v0.10.0 |
| C-NE-30 | `deploy_lab` 自动处理 SRL topology.yml 问题和 veth 注入 | 内部工具 | Lab Agent (enterprise) | 🔶 | v0.10.0 |
| C-NE-31 | Lab Agent 不含 `execute_cli`——无法访问生产设备 | 代码检查 | Lab Agent (enterprise) | ✅ | v0.10.0 |

### Audit Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-32 | Profile Authoring 引导创建审计 Profile | `olav --agent audit "创建..."` | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.17 (R17 designer 合入 auditor) |
| C-NE-33 | Auditor 执行 Profile 并输出 Markdown 报告 | `olav --agent audit "运行..."` | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.10.0 |
| C-NE-34 | Profile Authoring 在生成 SQL 前验证表/列存在 | 内部校验 | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.17 (R17 designer 合入 auditor) |
| C-NE-35 | `analyze_thresholds` 提供 P50/P90/P95 建议 | 内部工具 | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.10.0 |

### Infra Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-36 | `olav registry register` 注册服务并生成 API 参考 | `olav registry register <url>` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.12.0 |
| C-NE-37 | `olav --agent devops` 通过 `api_request` 查询注册的服务 | `olav --agent devops "查询..."` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.12.0 |
| C-NE-38 | `api_request` 对未注册的服务返回清晰错误 | `api_request("nonexistent", ...)` | [Infra Agent](../netops/infra-agent/index.md) | ✅ | v0.12.0 |
| C-NE-39 | 写操作需要 `--enable-api-write` + 模拟运行 + 强制审批 | `--enable-api-write` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.12.0 |
| C-NE-40 | `network_isolation=True` 沙箱强制阻止 HTTP 写操作 | 沙箱测试 | [Infra Agent](../netops/infra-agent/index.md) | ✅ | v0.12.0 |

### DevOps Agent

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-NE-41 | DevOps Agent 生成脚本前查询 `netops.devices` | `olav --agent devops "编写..."` | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |
| C-NE-42 | 生成的脚本包含 `--dry-run`、错误处理和摘要 | 脚本检查 | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |
| C-NE-43 | 脚本使用数据库中的真实设备名/IP，而非占位符 | 脚本检查 | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |
| C-NE-44 | 脚本作为可执行文件导出至 `exports/scripts/` | 文件检查 | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |

---

## Level 3 — 知识存储（UKS）

### 顶层能力

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-L3-UKS | 统一知识存储：以单张 LanceDB 表管理文档、记忆和图节点 | `olav kb status` | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Schema 与迁移

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-01 | memory 表包含 `origin`、`confidence`、`tags` 列 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-02 | `add_memory()` 省略新参数时使用正确默认值 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-03 | 旧 schema 表在首次访问时自动迁移 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-09 | `migrate_memory_table()` 从 `metadata.source` 推断 `origin`/`confidence` | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-25 | `migrate_kb_chunks()` 将 `kb_chunks` 行迁移至统一 memory 表 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-26 | `from olav.core.knowledge` 触发 `ImportError`（已删除） | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-27 | `search_knowledge_lancedb` 工具文件已删除 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### 捕获与衰减

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-04 | 提取条目含 `tags` 数组 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-05 | `capture` 写入 `origin="agent"`、`confidence=importance` | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-06 | `document` origin 不衰减 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-07 | `agent` origin 正常衰减 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-08 | 高频 recall（`access_count >= 5`）衰减率减半 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### 图谱

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-10 | `materialize_graph()` 返回含 `nodes` 和 `edges` 键的字典 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-11 | 向量相似度创建带 `weight` 字段的 implicit `similar` 边 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-12 | 共享 tag 通过实体节点创建 explicit 边 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-15 | `graspologic` 不可用 → `cluster_knowledge()` 返回 `None` | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### 导出与同步

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-13 | `export_obsidian()` 生成含 YAML frontmatter 的 `.md` 文件 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-14 | `export_visjs()` 生成含 vis.js 内容的 HTML 文件 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-17 | `sync_from_files()` 对磁盘已修改的条目执行 UPDATE | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-18 | `sync_from_files()` 对新文件执行 INSERT | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-19 | `sync_from_files()` 文件删除时执行 DELETE | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-20 | `sync_from_files(dry_run=True)` 不修改任何数据 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### 导入与搜索

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-21 | 导入 `.md` 文件创建 `origin='document'`、`confidence=1.0` 的记忆条目 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-22 | `hybrid_search()` 同时返回 `agent` 和 `document` origin 的结果 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-28 | 迁移 `kb_chunks` 后，`search_by_vector()` 返回 `origin='document'` 的条目 | 单元测试 | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### CLI 命令

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-KB-16 | `olav kb export` 命令可用且成功退出（exit 0） | `olav kb export` | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-23 | `olav kb status` 输出知识库统计信息 | `olav kb status` | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-24 | `olav kb graph` 生成 HTML 文件 | `olav kb graph` | [知识库](../guides/knowledge-base.md) | ✅ | v0.13.0 |

---

## Level 4 — 多用户与安全

| ID | 声明 | CLI 命令 | 文档页 | 状态 | 版本 |
|----|------|----------|--------|------|------|
| C-L4-01 | `olav init` 自动创建 admin 用户 + 写 token 至 `~/.olav/token` + 设置 `auth.mode=token` | `olav init` | [用户与角色](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-02 | `olav admin add-user` 在创建 token 前验证 Linux 用户存在 | `olav admin add-user <name>` | [用户与角色](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-03 | `olav sessions` 从审计数据库列出当前用户的 session | `olav sessions` | [用户与角色](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-04 | `olav --resume <thread_id>` 跨界面恢复 session | `olav --resume <id>` | [用户与角色](users-and-roles.md) | ⬜ | v0.14.0 |
| C-L4-05 | Thread 归属校验：非 owner 请求返回 HTTP 403；admin 可绕过 | `POST /runs/stream` | [安全模型](../concepts/security-model.md) | ✅ | v0.14.0 |
| C-L4-06 | `search_logs` 工具通过 DuckDB 查询 syslog Parquet 文件 | agent 工具 | [后台服务](../guides/services.md) | ✅ | v0.14.0 |
| C-L4-07 | `auth.mode=none` 时 API 服务器启动发出警告 | `olav service web start` | [安全模型](../concepts/security-model.md) | ✅ | v0.14.0 |

---

## 统计

| 状态 | 数量 |
|------|------|
| ✅ Verified | 112 |
| ⬜ Pending | 1 |
| 🔶 Env-Blocked | 6 |
| **合计** | **119** |

---

## 验证流程

```bash
# 对每条 ⬜ Claim 执行：
# 1. 运行 CLI 指令
olav <claim 对应的命令>

# 2. 记录结果到 dev_docs/23. CLI_VERIFICATION_LOG.md
# 3. 通过 → 状态改为 ✅，补充版本号
# 4. 失败 → 修代码 or 收窄声明 or 删除 Claim

# 对 🔶 Claim：需要先搭建环境（LLM API Key / 运行中的外部服务）
```
