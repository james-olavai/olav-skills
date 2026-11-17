# Core Agent

Core Agent 是 OLAV 统一的默认入口 — 提问即答。覆盖 80% 的日常网络查询，并在需要时自动委派给专用子 agent（writer、ops）。

!!! info "v0.15.0 — Core Agent 取代 Quick Agent"
    从 v0.15.0 起，原 *Quick Agent* workspace 已合并入 *Core Agent*。所有功能保留不变。`olav` 命令默认通过 `core` 路由。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-15 | `olav "R1 的 BGP 邻居？"` 从快照数据返回查询结果 | ✅ v0.10.0 |
    | C-NE-16 | 黑名单命令被 `execute_cli` 拒绝执行 | ✅ v0.10.0 |
    | C-NE-17 | `/learn_cmd "show version" --device R1` 执行命令并生成 TextFSM 模板 | ✅ v0.13.1 |
    | C-NE-18 | `take_snapshot` 触发增量采集，生成新 snapshot_id | ✅ v0.13.1 |
    | C-NE-19 | Core Agent 对复杂分析自动建议升级到 NetOps Agent | ✅ v0.13.1 |
    | C-V15-01 | `olav "<问题>"` 默认路由到 core agent（无需 `--agent` 参数） | ✅ v0.15.0 |
    | C-V15-02 | 语义路由器在置信度 > 0.85 时自动选择专用 agent | ✅ v0.15.0 |

    *说明：⚠️ (E2E门控) = 测试已存在但需要特殊环境变量，CI 不运行；⚠️ (手动) = 仅演示验证，无 CI 自动化测试*

---

## 何时使用 Core Agent

| 任务 | Core Agent | NetOps Agent |
|------|:----------:|:---------:|
| "有多少 BGP 邻居断开？" | ✅ 适合 | 杀鸡用牛刀 |
| "显示 R2 接口" | ✅ 适合 | 杀鸡用牛刀 |
| "昨天以来有什么变化？" | ✅ 可以 | 更好（用 Diff） |
| "模拟链路故障影响" | ❌ 不行 | ✅ 适合（Analysis） |
| "部署测试实验室" | ❌ 不行 | ✅ 适合（Lab） |

Core Agent 通过生成 SQL 查询快照数据库或执行单条 CLI 命令来处理简单查询。需要迭代、比较或模拟的问题会建议使用 NetOps Agent — v0.15.0 的语义路由可自动完成此切换。

---

## 查询示例

### 自然语言转 SQL

```bash
olav "R1 上的 BGP 邻居都建立了吗？"
```

OLAV 转换为：

```sql
SELECT neighbor_ip, neighbor_as, state
FROM v_bgp_neighbors_auto
WHERE device_name = 'R1' AND state != 'Established'
```

### 直接 SQL

```bash
olav "execute_sql: SELECT device_name, count(*) as cmd_count FROM netops.parsed_outputs GROUP BY 1"
```

### CLI 执行

```bash
olav "在 R2 上运行 'show ip route'"
```

Agent 在白名单中查找命令，通过 SSH 连接并返回输出。黑名单命令在建立任何连接之前即被拒绝。

---

## 快照采集

触发按需快照：

```bash
olav "对 R1 和 R2 进行快照采集"
```

这将为指定设备运行采集流水线，生成新的 `snapshot_id`，数据立即可查询。

---

## 学习新命令

当某条 show 命令没有 TextFSM 模板时，使用 `/learn_cmd` 教会 OLAV：

```bash
olav "/learn_cmd 'show ip bgp community' --device R1"
```

流程：

1. 通过 SSH 在设备上执行命令
2. 分析输出结构
3. 生成 TextFSM 模板
4. 保存到 `workspace/ops/config/templates/custom/`

模板无需重启即可热加载，供后续所有采集使用。

---

## 可用工具

Core Agent 可访问以下工具（通过 `inject_into_core` 从 `netops/tools/` 注入）：

| 工具 | 用途 |
|------|------|
| `execute_cli` | 在设备上执行单条 CLI 命令 |
| `search_commands` | 按平台和关键词搜索可用命令 |
| `diff_configs` | 比较两次快照之间的设备配置差异 |
| `take_snapshot` | 触发按需采集 |

以及所有**平台**核心工具（`execute_sql`、`format_and_export`、`olav_recall_memory` 等），始终可用。

---

## 升级提示

当 Core Agent 判断问题需要多步分析时，会提示：

> "该问题需要跨时间比较多个数据源，建议使用 NetOps Agent：`olav --agent netops '你的问题'`"

涉及模拟、漂移检测或多设备关联分析时会触发此提示。v0.15.0 启用语义路由后，此切换可自动完成。
