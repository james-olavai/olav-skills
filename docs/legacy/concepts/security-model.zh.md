# 安全模型

OLAV 从设计之初就考虑了多用户、团队协作场景下的安全需求。本页介绍认证、授权和数据隔离的工作方式。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-23 | `olav admin "add-user/list-users/revoke-token"` 用户管理 | ✅ v0.10.0 |
    | C-L2-28 | 支持 none/token/ldap/ad/oidc 认证模式 | ✅ v0.10.0 |
    | C-L2-12 | 多用户并发 Audit 无写冲突 | ✅ v0.10.0 |

---

!!! info "企业版功能 (olav-ent)"
    本节涉及的多用户**认证 provider**——token / server-token / LDAP、`olav admin add-user`、`olav login` 及每用户令牌——都属于企业版扩展 `olav-ent`（`pip install olav-ent`）。开源版 OLAV 以 `auth.mode=none` 运行：操作系统用户即身份，且拥有完整管理员权限。下文的**RBAC 引擎**（角色、权限矩阵、`(agent_id, skill_name, action)` 校验）属于开源版 OLAV；下面的 token/LDAP provider 负责在多用户部署中填充它。

## 认证：确认你是谁

每个用户有一个唯一的令牌（Token），存储在 `~/.olav/token`。OLAV 在每次请求时验证此令牌。

### 添加新用户

管理员创建用户并分配角色：

```bash
olav admin "add-user alice --role user"
```

命令执行后会打印一次性令牌——请立即分享给用户，之后无法再次查看。

### 用户配置令牌

用户收到令牌后，保存到本地：

```bash
mkdir -p ~/.olav
echo "TOKEN_HERE" > ~/.olav/token
chmod 600 ~/.olav/token
```

令牌以 SHA256 哈希（加盐）形式存储在 `.olav/databases/users.duckdb` 中，明文令牌不会被保存。

---

## 授权：你能做什么

OLAV 使用固定的三级角色模型（RBAC），简单直观：

| 角色 | 能做什么 | 不能做什么 |
|------|---------|-----------|
| `admin` | 一切——管理用户、查看所有日志、修改配置、安装技能 | — |
| `user` | 运行查询、调用工具、查看自己的审计日志 | 安装/卸载技能、管理用户 |
| `readonly` | 只读查询 | 任何写入操作（修改数据、执行命令） |

权限系统基于 `(agent_id, skill_name, action)` 三元组匹配，支持精细到特定 Agent 和技能的访问控制。四种操作类型：

- **use**：运行查询、调用工具
- **mutate**：修改数据、执行写入操作
- **install**：安装/卸载技能
- **admin**：用户管理、配置修改

---

## 多用户并发安全

OLAV 支持多人同时使用同一个项目目录，每个资源都有明确的安全边界：

| 资源 | 存储位置 | 并发安全 | 说明 |
|------|---------|---------|------|
| 审计日志 | `.olav/databases/audit.duckdb` | ✅ | DuckDB 原子写入，每条记录标记 user_id |
| LLM 缓存 | `~/.olav/cache/` | ✅ | 用户隔离，互不影响 |
| 会话记录 | `~/.olav/sessions/` | ✅ | 用户隔离，24h 后自动过期 |
| 认证令牌 | `~/.olav/token` | ✅ | 仅当前用户可读（chmod 600） |

---

## 认证模式

根据团队规模和安全需求，选择合适的认证方式。在 `.olav/config/api.json` 的 `auth.mode` 中配置：

| 模式 | 适用场景 | 说明 |
|------|---------|------|
| `none` | 个人使用、本地开发 | **默认（开源版）** —— 操作系统用户即身份，且拥有完整管理员权限，无需令牌 |
| `token` | 小团队 | **企业版 (olav-ent)** —— OLAV 内置令牌认证，最简单的多用户方案 |
| `ldap` | 企业环境 | **企业版 (olav-ent)** —— 对接 LDAP 目录服务 |
| `ad` | — | 未实现 —— **fail-fast** 报错（不静默降级） |
| `oidc` | — | 未实现 —— **fail-fast** 报错（不静默降级） |

---

## 凭证安全：什么该提交，什么不该

| 内容 | 是否提交 Git | 原因 |
|------|------------|------|
| `.olav/workspace/` | ✅ 提交 | Agent 和 Skill 定义，无敏感信息 |
| `.olav/config/` | ❌ 加入 .gitignore | 包含 API 密钥和认证配置 |
| `.olav/databases/` | ❌ 加入 .gitignore | 包含审计日志和业务数据 |

!!! tip "审计日志的防篡改保护"
    OLAV 的审计系统会为每条日志生成 SHA256 摘要并写入审计清单文件（Audit Manifest），满足 NIST AU-9 防篡改要求。你可以通过内置的完整性验证功能检测日志是否被修改。

详见 [用户与角色参考 →](../reference/users-and-roles.md)

---

## 敏感数据脱敏

OLAV 在采集网络设备输出（running-config、路由表、SNMP 响应等）时，无法保证操作员没有让设备明文打印密码、SNMP community 或 BGP MD5 key。OLAV 通过**两层脱敏管道**确保凭证永远不会落到审计数据库或导出数据集中，同时保留拓扑相关数据以便 diff 和故障诊断仍然可用。

### 两层架构

| 层 | 实现 | 替换标记 | 触发时机 | 用途 |
|---|---|---|---|---|
| **采集层** | `olav.core.redaction.scrub`（封装 [netconan](https://github.com/intentionet/netconan)，Batfish 团队出品的 ~50 KB 纯 Python 库） | `netconanRemoved0`、`netconanRemoved1`、… | 采集边界（`netops_init/run.py:_collect_cmd`），以及 `AuditEventRecorder.record_message` 内部作为纵深防御 | 网络配置感知的脱敏 —— 内置识别 Cisco IOS / IOS-XR / NX-OS / Junos / Arista EOS 的密码、SNMP community、RADIUS / TACACS / IPSec PSK / BGP MD5 等语法 |
| **企业网关层** | `olav.core.audit_recorder.redact_sensitive`（5 条 regex） | `[REDACTED]` | `audit_dataset_export.redact_audit_run` 的 Layer 1，并在数据集写盘前作为最终逃逸检测器再次使用 | 双保险检查 —— 确保 `password` / `community` / `secret` / `key-string` / `pre-shared-key` 等明文关键字没有逃过导出流水线 |

两层有意使用不同标记 —— 企业网关层只识别 `[REDACTED]`，因此任何漏网的明文凭证都会触发硬性管道失败，而不是悄无声息地泄漏。

### 采集层脱敏范围

netconan 默认规则覆盖了主流网络厂商的凭证面：

- IOS `username … password 7 …` / `enable secret 5 …` 哈希
- SNMP community 串（`snmp-server community netops-r0 RO`）
- BGP MD5 key（`neighbor 10.1.12.2 password BGP_SHARED_KEY_xyz`）
- IPSec 预共享密钥
- TACACS+ / RADIUS 共享密钥
- Junos `encrypted-password`（整行删除）+ `authentication-key`（替换）

内置 55 条敏感项 regex + 5213 个保留词白名单 —— 运维可通过 `api.json` 扩展两类列表：

```json
{
  "redaction": {
    "enabled": true,
    "anon_ip": false,
    "extra_sensitive_words": ["community"],
    "extra_reserved_words": ["site-A-edge"]
  }
}
```

### 保留不动的内容

默认配置 (`anon_ip: false`) 下，脱敏会保留以下数据 —— 因为网络 diff 和诊断逻辑依赖它们：

- **IP 地址**（`10.1.12.2`、`192.168.10.0/24`）
- **主机名**（`hostname R1`）
- **ASN**（`router bgp 65000`、`remote-as 65001`）
- **BGP 邻居关系**
- **接口名**（`GigabitEthernet0/1`、`ge-0/0/0`）

如果需要更强的隐私（例如在把语料发送给第三方 LLM 之前做匿名化），设置 `anon_ip: true` 即可启用 [Crypto-PAn](https://en.wikipedia.org/wiki/Crypto-PAn) 保前缀的 IP 匿名化算法。

### Salt 管理

netconan 的密码重写以及（可选的）IP 匿名化都基于**每个 workspace 独立的 salt**：

- **位置**: `<workspace>/.redaction_salt`
- **权限**: `chmod 600`（自动设置）
- **格式**: 32 字节随机数，hex 编码（64 字符）
- **首次使用时自动生成**；后续调用保持稳定，使得相同输入永远产生相同的脱敏输出（**幂等性** —— 跨快照凭证 diff 的前提）
- **指纹**（`sha256(salt)` 的前 8 个 hex 字符）记录在 `Findings.salt_fingerprint` 中，用于审计交叉引用而不暴露 salt 本身

不同 workspace 的 salt 不同，因此即便两个 workspace 包含相同的密码，它们的脱敏数据集之间也无法交叉关联。

### 失败模式 —— 默认 fail-open

脱敏层用 `try/except` 包裹，所以缺少依赖或 pattern 不匹配永远不会阻塞采集：

| 场景 | 行为 |
|---|---|
| `OLAV_REDACTION=0` | 关闭；输入原样返回。WARNING 日志。 |
| 未安装 `netconan` | WARNING 日志；输入原样返回。安装方式：`pip install 'olav[redaction]'`。 |
| `scrub()` 抛异常 | `record_message` 捕获；原始内容写入审计 DB。WARNING 日志。 |
| 空输入 / 仅空白 | 原样返回，`total_replacements=0`。 |

如果你所处的环境对合规性敏感，请监控 `audit_recorder` logger 中的 `redaction skipped` 警告 —— 重复出现意味着 netconan 扩展没有装到该装的地方。

### 安装

脱敏扩展是可选的，以保持基础安装包小巧：

```bash
pip install 'olav[redaction]'
```

会拉取 `netconan>=0.13`。不装也能运行 OLAV —— 采集照常进行，但凭证会以明文形式进入审计 DB（企业数据集导出时 Layer 2 仍然生效）。

---

---

## 技能执行安全：execute_skill_script 机制

OLAV 是一个与**生产系统直接交互**的工具——它查询真实的网络设备、读写生产数据库、调用运维 API。这一定位决定了"执行安全"的威胁模型与通用代码沙箱截然不同。

### 错误的防护目标：容器隔离

一种直觉是用 Docker 容器把脚本隔离起来，但这对 OLAV 的场景是**对症下错药**：

```
Docker sandbox 防护的是：
  脚本 → 破坏宿主机文件系统 / 进程

OLAV 实际的威胁是：
  prompt injection / LLM 幻觉 → 调用了错误的生产操作
```

容器需要挂载数据库卷、打通网络才能访问生产系统，隔离墙实质上是空的。更重要的是，即使脚本跑在容器里，如果 LLM 被诱导调用了错误的脚本，生产事故照样发生。

### 正确的防护目标：限制 LLM 的操作空间

OLAV 通过 `execute_skill_script` 构建了三层针对生产系统的防护：

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: 路径围栏执行（execute_skill_script）        │
│  执行范围强制限定在 <skill>/scripts/*.py；            │
│  LLM 在正常工作时只"看到" SKILL.md 声明的脚本        │
│  （prompt 层过滤，非执行层注册校验）                  │
├─────────────────────────────────────────────────────┤
│  Layer 2: HITL 确认门                                │
│  写操作（write_profile、commit_to_memory 等）          │
│  必须经过用户显式确认                                 │
├─────────────────────────────────────────────────────┤
│  Layer 3: 不可变审计日志                              │
│  每次 execute_skill_script 调用写入 audit_tool_calls  │
│  包含 skill_name、script_name、args、时间戳            │
└─────────────────────────────────────────────────────┘
```

### execute_skill_script 的安全保证

| 维度 | deepagents LocalShellBackend | OLAV execute_skill_script |
|---|---|---|
| 执行范围 | 任意 shell 命令（无限制） | 仅 `<workspace>/<skill>/scripts/*.py`（执行层强制） |
| 脚本注册校验 | — | **prompt 层**：SkillsMiddleware 只将 `scripts:` 声明的条目注入 system prompt；执行层不做注册校验 |
| 跨 Agent 调用 | — | 允许：workspace 是平坦命名空间，任何持有此工具的 Agent 均可调用任意 skill |
| 参数传递 | shell 字符串（可注入） | JSON stdin / list argv（无元字符） |
| 符号链接逃逸 | 无检测 | resolve + prefix 验证 |
| 超时 | 无默认 | 120 s 默认，600 s 上限 |
| 审计 | 无 | langchain hook 写 `audit_tool_calls` |
| LLM 接口 | 拼接路径字符串（易出错） | 语义化参数（skill + script 名） |

!!! note "Layer 1 的精确语义"
    Layer 1 的防护分两个层面：  
    **执行层（强制）**：路径围栏确保脚本文件必须位于 `<workspace>/<skill>/scripts/` 内，无法逃逸到任意文件系统路径，无法注入 shell 元字符。  
    **prompt 层（语义）**：SkillsMiddleware 只把 SKILL.md `scripts:` 注册的条目注入 system prompt，正常工作的 LLM 因此只"知道"声明的脚本。执行层本身不额外校验注册状态，这与 OLAV 的 fail-open 设计哲学一致——工作区中的所有脚本均为 git 管控的 committed 代码，无法在不提交代码的情况下植入。

对小模型（如 gemma4-31b）来说，`execute_skill_script("audit-author", "list_profiles.py", {})` 比 `execute(command="python /abs/path/to/scripts/list_profiles.py")` 的错误率显著更低——后者要求模型记住并正确拼接文件系统路径。

### 第三方技能兼容：argv 模式

按 deepagents 规范编写的第三方脚本通常使用 `argparse` 而非 JSON stdin。在 `SKILL.md` 的 `scripts:` 条目中声明 `argv: true`，`execute_skill_script` 会自动切换为 `--key value` CLI 参数模式：

```yaml
scripts:
  - name: run_report
    file: run_report.py
    argv: true          # argparse 风格，安装时由 installer 自动检测并设置
```

两种模式下，路径围栏、list argv（无 shell 注入）、超时、审计保证完全相同。

使用 `olav agent install` 安装第三方技能时，installer sub-agent 会自动运行兼容性分析并修补 `SKILL.md`，无需手动配置。

!!! info "沙箱与执行安全"
    认证/授权只是防护的第一层。OLAV 还提供三层代码执行沙箱、Prompt 注入扫描、危险命令 HITL 审批等机制，详见 [Agent Harness →](agent-harness.md)。
