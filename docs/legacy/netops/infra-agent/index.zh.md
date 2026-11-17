# Infra Agent

!!! note "`infra` 是 `devops` 的子 agent，不是顶层 agent"
    没有 `olav --agent devops`。基础设施查询在 `devops` agent 下运行
    （`olav --agent devops`），由其 `infra` 子 agent 处理。下文中的
    `--agent devops` 应改为 `--agent devops`。

Infra Agent 查询外部基础设施管理系统 — NetBox、InfluxDB 以及通过 `olav registry register` 注册的任何 API。零专属工具，全部继承自 core 平台。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-36 | `olav registry register` 注册服务并生成 API reference markdown | ✅ v0.12.0 |
    | C-NE-37 | `olav --agent devops` 通过 `api_request` 查询已注册服务 | ✅ v0.13.1 |
    | C-NE-38 | `api_request` 对未注册服务返回清晰错误 | ✅ v0.12.0 |
    | C-NE-39 | 写操作需 `--enable-api-write` + dry-run + 不可跳过审批 | ✅ v0.13.1 |
    | C-NE-40 | `network_isolation=True` sandbox 对 HTTP 写操作 hard_block | ✅ v0.12.0 |

    *说明：⚠️ (E2E门控) = 测试已存在但需要特殊环境变量，CI 不运行；⚠️ (手动) = 仅演示验证，无 CI 自动化测试*

---

## 架构

Infra 是**零工具 agent**，全部继承自 core：

| 继承的工具 | 用途 |
|-----------|------|
| `api_request` | 查询任何已注册服务（GET） |
| `format_and_export` | 导出脚本到 `exports/scripts/` |
| `execute_sql` | 查询 DuckDB 快照数据 |

---

## 注册服务

Infra 查询服务前需先注册：

```bash
olav registry register http://netbox:8000 --auth-type bearer --token-env NETBOX_TOKEN
```

执行流程：
1. 写入 `services.yaml`（默认只读）
2. 解析 OpenAPI schema
3. 生成精简 API 参考到 `infra/references/<service>_api.md`

---

## 查询服务

```bash
olav --agent devops "列出 NetBox DC1 站点所有设备"
```

Agent 转换为：

```python
api_request(service="netbox", path="/api/dcim/devices/", params={"site": "DC1"})
```

认证由平台自动处理。

---

## 批量变更脚本（Changeset 模式）

写操作生成脚本而非直接执行：

```bash
olav --agent devops "把 DC1 所有 planned 设备更新为 active"
```

Agent 流程：
1. `api_request` GET 读取当前状态
2. 计算 changeset（实测状态与目标状态做 diff）+ 验证冲突
3. `format_and_export` 导出到 `exports/scripts/changeset-netbox-<date>.sh`

```bash
# 审查后手动执行
NETBOX_TOKEN=abc123 bash .olav/exports/scripts/changeset-netbox-2026-04-08.sh
```

Agent **不执行**写 API 调用。五层安全防御确保即使 LLM 尝试也被阻止。

---

## 安全

| 层 | 防御 |
|----|------|
| 系统提示 | "NEVER attempt method != GET" |
| `api_request` | `confirmed=False` → 需审批 |
| `sandbox_guard` | network_isolation 下 hard_block |
| `unshare --net` | 物理网络隔离 |
| `services.yaml` | `readonly_only: true` |
