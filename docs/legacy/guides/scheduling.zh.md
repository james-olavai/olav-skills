# 定时任务调度

OLAV 用系统 cron 跑周期性 agent 任务 —— 每日快照、每周审计、每小时健康检查。所有 olav 管理的 cron 条目通过 `core/admin` 子 agent 的 `manage_cron` 工具集中维护。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L1-08 | `list_cron` / `add_cron` / `remove_cron` 三个 @tool 由 admin 子 agent 提供 | ✅ v0.17 |
    | C-L1-09 | `cron_schedules.yaml` 声明式批量应用调度 | ✅ v0.17 |
    | C-L1-10 | olav 管理的 cron 条目以 `# olav:agent\|instruction` 注释为隔离标记 | ✅ v0.17 |
    | C-L1-11 | `add_cron` 既能新增也能更新同 agent+instruction 的现有任务 | ✅ v0.17 |

---

## 工具位置

```
.olav/workspace/core/admin/tools/manage_cron.py    ← 实现
.olav/workspace/core/admin/SKILL.md                ← 注册到 admin 子 agent
.olav/workspace/core/AGENT.md                      ← admin 是 core 编排器的子 agent
```

调用路径：

```mermaid
graph LR
    User["olav '调度...'"] --> Core[core orchestrator]
    Core -->|olav_delegate| Admin[core/admin]
    Admin -->|@tool| MC[manage_cron]
    MC -->|python-crontab| Crontab[("system crontab")]
```

---

## 隔离设计

所有 olav 管理的 cron 条目带固定前缀的注释：

```
0 6 * * *  cd /home/yhvh/Olav && /home/yhvh/Olav/.venv/bin/olav --agent audit \
           --auto-approve "run wireless_health" \
           >> ~/.olav/logs/cron_audit.log 2>&1   # olav:audit|run wireless_health
```

- **`# olav:` 前缀** 是隔离标记。`list_cron` / `remove_cron` 只会列出/删除带这个前缀的条目，**绝不会**触碰用户自己手动加的 cron。
- **`agent|instruction` 部分** 是任务唯一键。同 `agent` + 同 `instruction` 重复 `add_cron` 走 update 路径（修改 schedule），不会重复添加。

---

## 命令式使用

### 列出所有 olav 管理的任务

```bash
olav "List olav scheduled jobs"
```

输出形如：

```json
{
  "count": 3,
  "jobs": [
    {"job_id": "olav:config|take snapshot",
     "agent": "config", "instruction": "take snapshot",
     "schedule": "0 2 * * *", "enabled": true,
     "command": "cd /home/yhvh/Olav && .venv/bin/olav --agent admin --auto-approve ..."},
    {"job_id": "olav:audit|run wireless_health",
     "agent": "audit", "instruction": "run wireless_health",
     "schedule": "0 6 * * *", "enabled": true, ...}
  ]
}
```

### 添加 / 更新一个任务

```bash
olav "Schedule wireless_health audit to run every day at 06:00"
```

admin 子 agent 解析为：

```python
add_cron(
    schedule="0 6 * * *",
    agent="audit",
    instruction="run wireless_health"
)
```

写入 crontab：

```
0 6 * * *  cd /home/yhvh/Olav && /home/yhvh/Olav/.venv/bin/olav --agent audit \
           --auto-approve "run wireless_health" \
           >> ~/.olav/logs/cron_audit.log 2>&1  # olav:audit|run wireless_health
```

!!! note "schedule 字段格式"
    标准 cron 5 字段表达式：`分 时 日 月 周`。常用：
    * `0 2 * * *` — 每天 02:00
    * `0 6 * * 1` — 每周一 06:00
    * `*/15 * * * *` — 每 15 分钟
    * `0 8-18 * * 1-5` — 工作日 08:00-18:00 整点

### 删除一个任务

```bash
olav "Remove the scheduled wireless_health audit"
```

→ `remove_cron(agent="audit", instruction="run wireless_health")` —— 按 agent+instruction 精确匹配，删除注释里带这两个字段的条目。

---

## `olav cron` 命令 —— 显式启用

OLAV 在 `olav init` 时会**声明**一组定时任务到 `cron_schedules.yaml`，但**绝不会自动把它们写进你的 crontab**。一个周期性的 `olav --auto-approve` 任务意味着每天一次 LLM 调用（而 `snapshot` 还会 SSH 到真实设备），所以启用始终是通过 `olav cron` 命令显式 opt-in：

```bash
olav cron enable            # 启用所有已声明的任务（逐条打印 + 一条披露说明）
olav cron enable reflect    # 只启用每日自我反思任务
olav cron list              # 列出当前已激活的 olav cron 任务
olav cron disable [name]    # 移除全部，或某一个 olav cron 任务
```

`olav cron` 解析 `cron_schedules.yaml` 并路由到 `core/admin` 的 `manage_cron` 脚本 —— crontab 机制与 undo journal 的唯一真相之源。它是上文那套隔离 + upsert 机制的一键入口。

`olav init` 和 `olav doctor` 结尾都会打印一行反映当前状态的 cron 提示：

```
○ optional — enable daily self-reflection via 'olav cron enable reflect'   # 一个都没激活时
✓ 5 scheduled job(s) active                                                # 已启用时
```

!!! note "cron 行自建日志目录"
    每条生成的 cron 行在写入前都会自建 `~/.olav/logs/`（它的重定向目标）—— 所以任务不会仅仅因为目录被清掉或是全新安装就静默丢失日志。

---

## 声明式使用 — `cron_schedules.yaml`

适合 IaC 风格：所有调度从一个 yaml 配置文件批量应用。

### 配置文件位置

```
.olav/workspace/ops/netops_init/config/cron_schedules.yaml
```

### 示例

```yaml
# Cron schedule configuration for olav scheduled tasks.
# Apply with: olav --agent core "apply cron schedules"
#
# All jobs call: olav --agent <agent> --auto-approve "<instruction>"

schedules:
  snapshot:
    cron: "0 2 * * *"
    agent: config
    instruction: "take snapshot"
    description: "Daily network snapshot — SSH collect + parse + diff all devices"

  trace_learner:
    cron: "0 3 * * *"
    agent: config
    instruction: "run trace learner"
    description: "Learn from network traces and update command template registry"

  wireless_health_daily:
    cron: "0 6 * * *"
    agent: audit
    instruction: "run wireless_health profile"
    description: "Daily wireless health — AP concentration / migration debt / stale APs"

  physical_layer_daily:
    cron: "0 7 * * *"
    agent: audit
    instruction: "run physical_layer_health profile"
    description: "Daily physical layer — input errors / CRC / link-up-proto-down"

  audit_weekly:
    cron: "0 6 * * 1"
    agent: audit
    instruction: "generate weekly compliance report"
    description: "Weekly audit — BGP health, config drift, compliance check"
```

### 批量应用

```bash
olav --agent core "apply cron schedules"
```

admin 子 agent 调 `apply_cron_schedules()`，对每条 yaml 入口都做 add-or-update。已有匹配 `agent+instruction` 的会更新 schedule，没有的会新增。

!!! tip "yaml 是真相之源"
    建议把所有调度写到 cron_schedules.yaml 进 git，新装环境一次 `apply_cron_schedules()` 全部装上。命令式 `add_cron` 适合临时调试。

---

## 端到端示例：把今天的巡检挂上去

我们前面通过 Explorer → Audit 把 wireless 和 physical layer 的发现固化成了两个 v4.0 profile：

- `audit/profiles/wireless_ap_concentration.md`（单 job，AP 集中度）
- `audit/profiles/physical_layer_health.md`（三 job：top input errors / CRC dominated / link-up-proto-down）

挂调度只需要一句：

```bash
olav "Schedule wireless_ap_concentration daily at 6am
      and physical_layer_health daily at 7am"
```

或者声明式，编辑 `cron_schedules.yaml` 加两条然后 `olav --agent core "apply cron schedules"`。

每天早晨 06:00-07:30 自动产出：

```
~/.olav/logs/cron_audit.log                                # 运行日志
exports/audit_reports/wireless_ap_concentration_<date>.md  # AP 集中度健康
exports/audit_reports/physical_layer_health_<date>.md      # 三段物理层健康（含 Priority 2b follow-up）
```

每份报告里 Critical/Warning 行直接是可执行的 `olav -a netops "Investigate ..."` 命令 —— 运维拷到终端就能往下查。

---

## 与 Audit Agent 的关系

参见 [Audit Agent](../netops/audit-agent/index.md) 的"完整生命周期"段落。本指南是该 lifecycle **第三段（Scheduling）的详细说明**。

| 阶段 | 谁负责 | 频次 |
|---|---|---|
| Explorer 发现 | netops/explorer + 人类决策 | 一次性 |
| Profile 固化 | audit/author | 一次性 |
| **Cron 挂调度** | **core/admin (`manage_cron`)** | **一次性** |
| 周期巡检执行 | cron → audit/runner | 每天 / 每周 |

---

## 安全注意

1. **`--auto-approve` 标志**：cron 触发的 audit/runner 跑在无人值守模式 → 所有 HITL 关都被绕过。Profile 内 SQL 应**绝对只读**（SELECT，不允许 DDL/DML）。author 子 agent 写 profile 时会做 schema 验证 + per-SQL 校验，能拦下 INSERT/UPDATE/DELETE。
2. **`--agent` 标志**：cron 命令固定用 `--agent <name>` 限制入口。不要给 `--agent core`（编排器有 admin/remote 写权限的递归路径）。`--agent audit` / `--agent admin` 是安全的子集。
3. **日志位置**：`~/.olav/logs/cron_<agent>.log`。建议配 logrotate 保留 30 天。
4. **PATH 问题**：cron daemon 默认 PATH 极简，所以 `manage_cron` 生成的命令会用完整路径 `cd /path/to/olav && /path/to/.venv/bin/olav ...`，不依赖 shell PATH 解析。
