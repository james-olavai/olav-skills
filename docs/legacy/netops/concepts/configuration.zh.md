# 配置参考

每个 agent 和 skill 有自己的 `config/` 目录。配置随 skill 走 — 安装或移动 skill 时，配置一起迁移。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-12 | `nornir/hosts.yaml` 定义采集的设备清单 | ✅ v0.10.0 |
    | C-NE-13 | `blacklisted_commands.yaml` 支持正则，无效 pattern 跳过并记录警告 | ✅ v0.10.0 |
    | C-NE-14 | `cron_schedules.yaml` 可自定义 trace_learner 定时 | ✅ v0.10.0 |

---

## 配置架构

```
.olav/config/                              ← 平台级（仅 4 个文件）
├── api.json                               # LLM 密钥、模型配置
├── api.json.example                       # 模板
├── services.yaml                          # 已注册 API 服务
└── approval_rules.yaml                    # HITL 审批规则

.olav/workspace/
├── netops/config/                         ← NetOps 编排器（共享）
│   ├── blacklisted_commands.yaml          # 命令黑名单（所有子 agent）
│   └── netops_settings.yaml              # 通用 NetOps 设置
│
├── netops/collector/config/               ← collector 子 agent（SSH）
│   ├── nornir/
│   │   ├── hosts.yaml                    # 设备清单
│   │   ├── groups.yaml                   # 组凭证
│   │   ├── defaults.yaml                 # 全局默认
│   │   └── config.yaml                   # Nornir 运行器设置
│   └── default_commands.yaml             # 按平台采集命令
│
├── netops/analyzer/references/            ← analyzer 子 agent（变更计划起草）
│   └── *.guide.yaml                      # 用法指南 / 专家知识
│
├── netops/simulator/references/           ← simulator 子 agent（Batfish）
│   └── *.guide.yaml                      # Batfish 问题 recipe / 指南
│
├── netops/topology/references/            ← topology 子 agent
│   └── *.guide.yaml                      # 拓扑 recipe / 指南
│
└── audit/profiles/                        ← Audit agent
    ├── bgp_health.md                     # BGP 健康检查 Profile
    └── health_full_drift.md              # 全量漂移审计 Profile
```

**原则：** 平台配置在 `.olav/config/`。Skill 配置在各自目录。无跨 skill 配置依赖。

---

## Probe Agent — SSH 与设备采集

### nornir/hosts.yaml

```yaml
R1:
  hostname: 192.168.100.101
  platform: juniper_junos
  groups:
    - core_routers
```

`platform` 值遵循 [NTC-Templates 命名](https://github.com/networktocode/ntc-templates)。

### nornir/groups.yaml

```yaml
core_routers:
  username: admin
  password: "${NORNIR_PASSWORD}"
  connection_options:
    netmiko:
      extras:
        timeout: 30
```

!!! tip "环境变量"
    敏感值使用 `${VAR_NAME}`，运行时展开。

### default_commands.yaml

```yaml
- show version
- show running-config
- show interfaces
- show ip interface brief
```

---

## NetOps 编排器 — 共享配置

### blacklisted_commands.yaml

正则 pattern，跨所有 agent 拦截危险命令。

```yaml
- "reload"
- "write erase"
- "conf(igure)? t(erminal)?"
```

---

## Infra Agent — API 参考

参考文档由 `olav registry register` **自动生成**。

```bash
olav registry register http://netbox:8000
```

---

## DevOps Agent — 脚本参考

`references/` 中的文件作为 agent 静态上下文。添加自定义参考教 agent 你的环境：

```bash
cp my-guide.md .olav/workspace/devops/references/
```

---

## Audit Agent — Profile

Designer 创建，Auditor 执行：

```bash
olav --agent audit "创建 BGP 健康检查 Profile"
olav --agent audit "执行 bgp_health 审计"
```

---

## 定时调度

```bash
olav "每天凌晨 4 点执行快照"
olav "查看所有定时任务"
```

### cron_schedules.yaml

```yaml
schedules:
  snapshot:
    cron: "0 2 * * *"
    agent: config
    instruction: "take snapshot"
```

---

## 平台配置 (.olav/config/)

| 文件 | 用途 |
|------|------|
| `api.json` | LLM 密钥、模型选择 |
| `services.yaml` | 已注册 API 服务 |
| `approval_rules.yaml` | HITL 审批规则 |

!!! warning "不要提交 api.json"
    包含 LLM API 密钥。已在 `.gitignore` 中。

---

## Skill 结构 — Agent 如何注册

### Workspace 目录

每个 agent 位于 `.olav/workspace/<name>/`，标准结构：

```
.olav/workspace/netops/
├── AGENT.md                       # Agent 声明
├── SKILL.md                       # 工具、意图、静态上下文
├── MANIFEST.yaml                  # 路由关键词、版本、依赖
├── prompts/system.md              # 系统提示词
├── tools/                         # Python @tool 文件
├── config/                        # Agent 专属配置
├── references/                    # 静态上下文文件
└── analyzer/ simulator/ collector/ topology/ importer/ learner/    # 子 agent
```

### AGENT.md

声明 agent 及其子 agent：

```yaml
---
name: netops
description: "网络运维 — 变更计划、模拟、漂移检测"
subagents:
  - path: ./analyzer/SKILL.md
  - path: ./simulator/SKILL.md
  - path: ./collector/SKILL.md
  - path: ./topology/SKILL.md
  - path: ./importer/SKILL.md
  - path: ./learner/SKILL.md
---
```

### SKILL.md

声明工具和上下文：

```yaml
---
name: analyzer
description: "变更计划起草器"
tools:
  - execute_sql
static_context:
  - path: ./references/ROUTING_EXPERT_GUIDE.md
metadata:
  network_isolation: "true"
---
```

### PLATFORM.md

平台 agent 注册表（`.olav/workspace/PLATFORM.md`）：

```yaml
---
active: core
agents:
  - admin
  - audit
  - core
  - devops
  - netops
  - services
---
```

`olav agent install` 自动添加到此列表。`olav list` 读取此文件。

---

## 安装 Skill (olav-netops)

```bash
# 本地目录
olav agent install /path/to/olav-netops/

# Git URL
olav agent install https://github.com/james-olavai/olav-netops
```

安装后：
1. Workspace 文件复制到 `.olav/workspace/netops/`
2. Agent 注册到 `PLATFORM.md`

### 安装后设置

```bash
# 1. 复制配置模板
cp .olav/workspace/netops/collector/config/nornir/hosts.yaml.example \
   .olav/workspace/netops/collector/config/nornir/hosts.yaml
# 编辑设备清单

# 2. 初始化
olav --agent netops "/netops_init"
```

---

## 注册 API 服务

```bash
olav registry register http://netbox:8000
```

执行后：
1. 写入 `services.yaml`
2. 解析 OpenAPI schema
3. 生成 reference markdown 到 `infra/references/`

### services.yaml 格式

```yaml
services:
  netbox:
    endpoint: http://netbox:8000
    auth:
      type: bearer
      token_env: NETBOX_TOKEN    # 环境变量名（非实际 token）
    readonly_only: true          # 默认只读
```
