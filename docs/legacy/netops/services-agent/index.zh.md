# Services Agent

Services Agent 封装了"对接外部服务"这一常见需求 — 注册新服务、启停服务
容器、向 NetBox / InfluxDB / Gitea / ContainerLab 等发认证 HTTP 调用。

!!! note "Round 16 起的顶层 agent（v0.18.0）"
    v0.18 之前，`deploy_service` / `stop_service` / `api_request` 作为
    一等公民工具挂在 `core/` 下。v0.18.1 spec（ADR-0004）把它们拆进
    独立的 `services/` 顶层 agent，让 core 的工具面收敛到 7 个跨域能力
    （ADR-0006）。底层工具不变 — 仅编排器 surface 迁移。

---

## 调用

```bash
olav --agent services "注册一个新的 InfluxDB，地址 http://..."
olav --agent services "部署 netbox"
olav --agent services "停掉 netbox"
olav --agent services "GET /api/v2/query?q=..."
```

Services 声明 4 个工具：

| 工具 | 用途 |
|---|---|
| `register_service` | 向 `.olav/config/services.yaml` 添加服务条目（幂等；不覆盖已有）。支持 `none` / `bearer` / `basic` 认证，通过 `auth_token_env` 读 token。 |
| `deploy_service` | 启动已注册服务（通常是容器）。 |
| `stop_service` | 停止已注册服务。 |
| `api_request` | 对 `services.yaml` 中任意服务发认证 HTTP 调用。默认 compact 返回；长列表按 `_COMPACT_LIST_CAP` 截断以照顾小模型。 |

---

## services.yaml 格式

```yaml
# .olav/config/services.yaml
services:
  - name: netbox
    endpoint: http://netbox.local:8000
    auth_type: bearer
    auth_token_env: NETBOX_API_TOKEN     # 调用时解析

  - name: influxdb
    endpoint: http://influx.local:8086
    auth_type: bearer
    auth_token_env: INFLUXDB_TOKEN

  - name: gitea
    endpoint: http://gitea.local:3000
    auth_type: basic
    auth_token_env: GITEA_BASIC_CREDS
```

`auth_token_env` 指向保存 token 的 env var — 调用时才解析，注册时不读。
Token 从不写入 YAML 文件。

---

## core 的升级路径

当 core agent 收到服务集成相关关键词时，编排器升级为 `--agent services`：

| 查询包含 | 路由到 |
|---|---|
| "注册服务"、"services.yaml"、"新服务" | services |
| "部署服务"、"启动 netbox"、"拉起 influxdb" | services |
| "停止服务" | services |
| "调 NetBox API"、"查 InfluxDB"、"从 Gitea GET" | services |

---

## 治理

`services/` 是新顶层 agent 扩展政策（源仓库 `docs/adr/0004`）的第一个
**已批准先例** — 满足四条准入标准（独立 domain、≥3 个 domain-specific
tool、独立生命周期、治理 pin 完备）。`tests/governance/test_services_agent.py`
14 条 pin 守护 AGENT.md + SKILL.md + 工具表面 + 回指 core canonical
工具文件的 symlink 布局（ARCH-20 Phase 2 去重模式）。
