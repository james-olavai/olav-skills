# 连接外部服务

OLAV 可以连接任何提供 REST API 的外部服务。连接一次后，你就可以用自然语言查询，或为它生成自动化脚本。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-19 | `olav registry register <url>` 一行命令注册 OpenAPI 服务 | ✅ v0.10.0 |
    | C-L2-20 | DevOps agent 生成环境感知型自动化脚本 | ✅ v0.13.0 |

---

## 适用场景

你的团队可能使用了多种运维工具：NetBox（IPAM）、Zabbix（监控）、ServiceNow（工单）、自研平台等。每个工具都有自己的 API 和查询方式。OLAV 让你用一句话就能跨系统查询：

```bash
olav "机柜 A1 里有多少台设备？"        # 查 NetBox
olav "Zabbix 上有哪些活跃告警？"        # 查 Zabbix
olav "列出欧洲所有站点"                 # 查 NetBox
```

---

## 快速注册：一行命令

如果目标服务提供 OpenAPI（Swagger）规范，注册只需要一行命令：

```bash
olav registry register http://netbox.example.com/api/schema/
```

OLAV 会自动读取 OpenAPI schema，生成 API 参考文档，服务即可通过 `api_request` 查询。无需代码生成 — agent 直接根据参考文档构造 API 请求。

### 需要认证的服务

大多数服务需要 API Token 或其他认证方式：

```bash
olav registry register http://netbox.example.com/api/schema/ \
  --header "Authorization: Token YOUR_TOKEN"
```

OLAV 的服务注册支持多种认证方式（Bearer Token、API Key、Basic Auth、JWT），具体配置在 `.olav/config/services.yaml` 中管理。

### 管理已注册的服务

```bash
olav registry list                        # 列出所有已注册服务
olav registry status netbox               # 检查某个服务的可达性
olav registry refresh netbox              # 强制重新获取 schema
```

---

## 生成自动化脚本

连接服务后，DevOps agent 可以生成与你的基础设施交互的生产级脚本：

```bash
olav --agent devops "写一个脚本，拉取所有 NetBox 设备并导出为 CSV"
olav --agent devops "为 LON1 站点所有路由器写一个 Zabbix 维护窗口脚本"
```

DevOps agent 会读取 OLAV 数据库中的真实设备数据，生成包含 `--dry-run` 模式和错误处理的环境感知型脚本，并以可执行文件形式导出到 `exports/scripts/`。

详见 [DevOps Agent →](../netops/devops-agent/index.md) 和 [Infra Agent →](../netops/infra-agent/index.md)。

---

## 两种方式对比

| | `registry register` | DevOps agent |
|-|---------------------|--------------|
| 速度 | 秒级完成 | 一次查询即生成脚本 |
| 产出 | 通用 API 代理（自然语言查询） | 可执行 bash/Python 脚本 |
| 设备感知 | 否 | 是——读取 `netops.devices` |
| 适合 | 临时查询、快速检索 | 自动化工作流、CI/CD 集成 |

---

## NetBox CSV 同步（v0.21.1+） {#netbox-csv-sync}

第三种范式 —— 可审计的批量数据推送 —— 用一份 CSV 文件作为读端
（netops）与写端（services）之间的契约。CSV 可在 git diff 中
review、可跨环境重放、可在真正写入 NetBox 之前由人审核。

```
┌────────────┐  /export_netbox_csv  ┌──────────────┐  /import_netbox_csv   ┌─────────────┐
│   netops   │ ───────────────────▶ │ exports/.csv │ ────────────────────▶ │  services   │
│  (netops.  │  行 + tenant         │  (审计 gate  │  校验 + 推送          │  (NetBox    │
│  devices)  │  + 水印              │   via git    │  via /api/dcim/       │   REST)     │
│            │                      │   diff)      │  幂等 FK chain        │             │
└────────────┘                      └──────────────┘                       └─────────────┘
```

### Phase A —— 导出 CSV

```bash
olav --agent netops "/export_netbox_csv"
# → exports/netbox_devices.csv（header + 每台设备一行）
# 可选: --filename my_batch --tenant team-foo
```

11 列契约 —— `name, device_role, manufacturer, device_type,
primary_ip4, platform, site, status, tenant, snapshot_id,
exported_at` —— 文档化在
`netops/guides/netbox_csv_export.guide.yaml`（platform tier guide）。
`tenant` 列默认从 `netbox_tenant_default` team-tier guide 读取，
所以一条 `olav kb import-guides` 就能让之后所有导出同步更新。

### Phase B —— 干跑校验

```bash
olav --agent services "/import_netbox_csv"
# → exports/reports/netbox_import_dry_run.md
# 全部能创建则退出 0；任一行 validation 失败退出 1
```

校验项：必填字段、IPv4 可解析、status 在 NetBox 允许集合内、
`exported_at` ISO-8601、column drift。这是安全的 HITL gate ——
不发 HTTP、不动 NetBox、CI 里随便跑。

### Phase C —— 真正推送（`--write`）

干跑通过后，向已注册的 NetBox 推送：

```bash
export NETBOX_TOKEN="nbt_<your-token>"
olav --agent services "/import_netbox_csv --write \
  --endpoint http://localhost:8000 --token $NETBOX_TOKEN"
```

每行幂等 5 步 lookup-or-create：

1. **site** —— `GET /api/dcim/sites/?name=<X>`；缺则 POST
2. **manufacturer** —— `GET /api/dcim/manufacturers/?name=<X>`；缺则 POST
3. **device_type** —— `GET ?model=<X>&manufacturer_id=<Y>`；缺则 POST
4. **device_role** —— `GET /api/dcim/device-roles/?name=<X>`；缺则 POST
5. **platform** —— `GET /api/dcim/platforms/?name=<X>`；缺则 POST（列为空时跳过）
6. **device** —— `GET ?name=<X>&site_id=<Y>`；缺则带所有 FK 一并 POST

成功推送后再跑同一份 CSV 会得到 `0 created / N existed / 0 failed`
—— 安全到可挂 cron 自动跑。

每次 `--write` 调用都会落一行 `kb_audit/<ts>_netbox_push.yaml`
（`action: netbox_push / actor / csv_body_sha256 / endpoint /
results{created,existed,failed,skipped_validation}`），通过
sha256 把发送出去的字节绑定到对应的 CSV —— 即便 CSV 之后重新
生成，审计链也能找回原始内容。详见
[知识库 → kb_audit](knowledge-base.md#kb-audit-changelog)。

### 为什么用 CSV 中间格式？

* **审计闸门** —— CSV 推送前可在 git diff review；`kb_audit/` 行
  通过 sha256 钉死实际发送的字节。
* **解耦** —— netops 只需懂 OLAV `netops.devices` schema；services
  只需懂 NetBox API 怪癖（FK 顺序、slug 约定、status 枚举）。两个
  agent 都不学对方的领域。
* **可重放** —— 同一份 CSV 可对 staging 干跑、对 prod 真推，语义
  一致。
* **NetBox 前的 HITL** —— Phase A 和 Phase B 之间，操作员可以手工
  改 CSV 而无需触碰源头 `netops.devices`。
