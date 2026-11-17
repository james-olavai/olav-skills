# 数据库结构

所有 NetOps 数据都位于 `netops` DuckDB schema。表在 olav-netops 安装时自动创建,视图在每次 `/netops_init` 结束和每次 `take_snapshot` 后重建。

!!! abstract "特性声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-08 | `netops.devices` 带 hostname、ip_address、platform、vendor | ✅ v0.10.0 |
    | C-NE-09 | `v_bgp_neighbors_auto` 由 `view_recipes` + `parsed_outputs` 构建(纯 SQL,零 LLM) | ✅ v0.11.0 |
    | C-NE-10 | `netops.topology_links` 带 `discovery_protocol` 区分 L2/L3 | ✅ v0.10.0 |
    | C-NE-11 | `netops.raw_output_store` 保留每设备的最新原始 CLI 输出 | ✅ v0.10.0 |
    | C-NE-12 | builtin / user recipe YAML 驱动视图生成,无硬编码视图 DDL | ✅ v0.11.0 |

---

## 表

### netops.devices

设备清单,由 `/netops_init` 的 Device ETL 阶段写入。Nornir inventory 是 `hostname` / `ip_address` / `platform` 的**权威源**;`show version` 的解析结果经 `platform_profiles.yaml` 提取 `model` / `os_version`。

| 列 | 类型 | 说明 |
|----|------|------|
| `hostname` | VARCHAR | 设备主机名 —— 主键 |
| `ip_address` | VARCHAR | 管理 IP(Nornir `host.hostname`,SSH 目标) |
| `platform` | VARCHAR | Netmiko 平台字符串(如 `cisco_ios`、`juniper_junos`) |
| `site` | VARCHAR | 站点标签(预留,由用户流程填充) |
| `role` | VARCHAR | 角色标签(预留) |
| `vendor` | VARCHAR | 显示厂商名:YAML profile 若已知,否则规则推断 |
| `model` | VARCHAR | 型号(来自 `show version` + `platform_profiles.model_fields`) |
| `os_version` | VARCHAR | OS 版本(来自 `show version` + `platform_profiles.os_version_fields`) |
| `environment` | VARCHAR | Nornir `data.environment` 标签(`lab`/`prod`/`staging`/`dev`) |
| `last_seen` | TIMESTAMP | 最近 UPSERT 时间 |
| `metadata` | JSON | 设备级自由元数据 |

### netops.parsed_outputs

结构化 CLI 输出。每 (device, command, snapshot) 一行。`raw_output` 在 ingest 后会置空,原文 hash 移到 `raw_output_store`。

| 列 | 类型 | 说明 |
|----|------|------|
| `device_name` | VARCHAR | 来源设备主机名 |
| `command` | VARCHAR | 执行的 CLI 命令 |
| `parsed_data` | JSON | 解析记录数组(TextFSM / ntc-templates / PaC Python parser) |
| `snapshot_id` | VARCHAR | Snapshot 标识 |
| `raw_output` | TEXT | 历史遗留,ingest 后置空 |
| `raw_output_hash` | VARCHAR | 外键指向 `raw_output_store.content_hash`(去重) |
| `ingested_at` | TIMESTAMP | 入库时间 |

唯一键:`(device_name, command, snapshot_id)`。

### netops.raw_output_store

每 (device, command) 最新的原始 CLI 输出,每次快照覆盖。

| 列 | 类型 | 说明 |
|----|------|------|
| `device_name` | VARCHAR | 来源设备主机名 |
| `command` | VARCHAR | CLI 命令 |
| `raw_output` | TEXT | 完整未处理 CLI 输出 |
| `snapshot_id` | VARCHAR | Snapshot 标识 |
| `updated_at` | TIMESTAMP | 最近覆盖时间 |

唯一键:`(device_name, command)`。

### netops.topology_links

CDP / LLDP(以及 `discovery_protocols.yaml` 声明的任何发现协议)提炼出的邻居关系。Topology ETL 在每次 snapshot ingest 后运行。

| 列 | 类型 | 说明 |
|----|------|------|
| `link_id` | VARCHAR | 确定性双向 ID(A↔B 与 B↔A 归一)—— 主键 |
| `source_device` | VARCHAR | 源设备主机名 |
| `source_interface` | VARCHAR | 源侧接口名 |
| `destination_device` | VARCHAR | 目的设备主机名 |
| `destination_interface` | VARCHAR | 目的侧接口名 |
| `discovery_protocol` | VARCHAR | `CDP`/`LLDP`/用户声明的协议名 |
| `link_type` | VARCHAR | `L2`/`L3`/overlay/其他(来自协议 spec) |
| `link_status` | VARCHAR | `up`/`down` |
| `link_speed` | VARCHAR | 可选速率 |
| `first_seen` | TIMESTAMP | 首次观察时间 |
| `last_seen` | TIMESTAMP | 最近观察时间 |
| `last_verified` | TIMESTAMP | 最近验证时间 |
| `status_changes` | INTEGER | 状态变化次数 |
| `snapshot_id` | VARCHAR | 最近观察到的 snapshot |
| `platform` | VARCHAR | 源设备平台 |

### netops.oc_outputs

每 (device, snapshot, module) 一行的严格 OpenConfig JSON,稀疏表,仅在 OC 映射接通后填充。

| 列 | 类型 | 说明 |
|----|------|------|
| `device_name` | VARCHAR | 来源设备主机名 |
| `snapshot_id` | VARCHAR | Snapshot 标识 |
| `oc_module` | VARCHAR | OpenConfig YANG 模块 |
| `oc_data` | JSON | OpenConfig 结构化数据 |
| `source_cmd` | VARCHAR | 源 CLI 命令 |

唯一键:`(device_name, snapshot_id, oc_module)`。

### main.view_recipes

视图生成的驱动表。Seed 自 `recipes/builtin/*.yaml`(随 olav-netops 发行)和 `~/.olav/config/recipes/user/*.yaml`(用户 / agent 起草)。

| 列 | 类型 | 说明 |
|----|------|------|
| `command` | VARCHAR | recipe 对应的 CLI 命令 |
| `concept` | VARCHAR | 归一概念(`bgp_neighbors`、`ospf_neighbors` 等) |
| `vendor_hint` | VARCHAR | 平台字符串或 `universal` |
| `field_mappings` | JSON | 归一字段 → 原始字段名映射 |
| `filter_expr` | VARCHAR | 可选 SQL 过滤表达式 |
| `discovered_at` | TIMESTAMP | seed / 发现时间 |

唯一键:`(command, concept, vendor_hint)`。

---

## 自动生成的视图

视图在 `/netops_init` 结束时由 `view_builder.build_all_views` 重建,`take_snapshot` 的 post-hook 也会触发重建。读取 `view_recipes` + `parsed_outputs`(L2 视图直投 `topology_links`),纯 SQL,零 LLM 开销。

| 视图 | 主要列 | 数据源 |
|------|--------|--------|
| `v_bgp_neighbors_auto` | device、neighbor_ip、neighbor_as、local_as、router_id、state、uptime | `bgp_neighbors` recipes |
| `v_ospf_neighbors_auto` | device、neighbor_id、neighbor_ip、interface、state | `ospf_neighbors` recipes |
| `v_l2_links_auto` | source_device、source_interface、destination_device、destination_interface、discovery_protocol、link_status | 直投 `netops.topology_links` |

!!! tip "加新视图"
    在 `.olav/workspace/topology/recipes/builtin/`(内建)或 `~/.olav/config/recipes/user/`(单装)放一份 recipe YAML。concept 名可任意(需匹配 `[a-z][a-z0-9_]*`)。下次 `/netops_init` 或 `take_snapshot` 会自动建 `v_<concept>_auto` 视图。详见 [可扩展性](extensibility.md)。

---

## 查询

所有 agent 通过 `execute_sql` 查这些表:

```sql
-- netops schema 必须带前缀
SELECT * FROM netops.devices WHERE platform = 'cisco_ios';

-- 视图也在 netops schema 下
SELECT * FROM netops.v_bgp_neighbors_auto WHERE state != 'Established';

-- 跨 snapshot 对比
SELECT * FROM netops.parsed_outputs
WHERE snapshot_id IN ('snap-001', 'snap-002')
  AND command = 'show bgp summary';
```

!!! warning "必须带 schema 前缀"
    所有 NetOps 对象(表和视图)都在 `netops` DuckDB schema 下。用 `netops.v_bgp_neighbors_auto`,不要用裸 `v_bgp_neighbors_auto`。唯一例外是 `main.view_recipes`,属于平台级配置表。
