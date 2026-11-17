# 可扩展性

OLAV NetOps 的架构原则:**加一个新厂商、加一个新发现协议、加一个新运维概念 —— 全部是 YAML 改动,从不改 Python 代码**。本页列出每条扩展路径、要改哪个文件、下游会发生什么。

---

## 原则:六份 YAML,零代码

所有领域知识都落在下列 YAML 里。Python 代码运行时读取,换值或加条目都不需要重打 wheel 或重启 agent。

| 文件 / 表 | 所属 | 用途 |
|---|---|---|
| Nornir inventory(任意后端) | Nornir 配置 | 设备清单 + `platform` + `hostname` + `environment` |
| `netops.commands`(DuckDB 表) | `/netops_init` Stage 0 | **每平台 CLI 命令的 SSOT。** `sync_commands` 扫描 ntc-templates 文件名(`<vendor>_<os>_<command>.textfsm`)自动填充，无需改 YAML —— 扩展 ntc-templates 覆盖即可。 |
| `.olav/workspace/netops/netops_init/config/user_commands.yaml`(+ 可选 `~/.olav/config/user_commands.yaml` 覆盖) | ops skill + 用户 | 仅做 backup 原始抓取(show run / show config)，无 TextFSM 模板，存到 `raw_output_store` 供 LLM 推理 |
| `.olav/workspace/netops/netops_init/config/discovery_protocols.yaml` | ops skill | 拓扑 ETL 要提取的邻居发现协议 |
| `.olav/workspace/netops/netops_init/config/platform_profiles.yaml` | ops skill | 厂商名 + show-version 字段提示 + Scrapli 映射 |
| `.olav/workspace/topology/recipes/builtin/*.yaml` + `~/.olav/config/recipes/user/*.yaml` | topology skill + 用户 | 归一概念 → 原始字段映射(驱动 SQL 视图) |
| `~/.olav/config/topology.yaml` | 用户 | 意图 —— 关心哪些关系 |

---

## 加新厂商

比如要把一台 **Palo Alto PAN-OS** 防火墙纳入 OLAV 管理。

**Step 1. 在 Nornir 声明设备。** 不管用哪种 inventory 后端(SimpleInventory YAML / NetBox / Ansible / Dict / 自定义):

```yaml
FW1:
  hostname: 10.0.0.10
  platform: paloalto_panos
  username: admin
  groups: [prod]
```

这就够了。下次 `/netops_init` 后 `netops.devices` 会有一行:

```
FW1 | paloalto_panos | Palo Alto | 10.0.0.10 | (model NULL) | (os_version NULL)
```

`vendor` 字段由规则推断得到 —— `paloalto_panos` → "Palo Alto",无需 YAML 编辑。

**Step 2(可选). 加 platform profile。** 想让 `model` / `os_version` 从 `show version` 抽取,在 `platform_profiles.yaml` 加一块:

```yaml
paloalto_panos:
  vendor: Palo Alto
  scrapli_platform: paloalto_panos
  model_fields: [MODEL, SYSTEM_MODEL]
  os_version_fields: [VERSION, SW_VERSION]
```

**Step 3(可选). 扩展命令目录。** `netops.commands` 在 `/netops_init` Stage 0 自动扫描 ntc-templates 文件名填充 —— 只要有 `paloalto_panos_show_system_info.textfsm` / `paloalto_panos_show_routing_route.textfsm` 这样的模板，对应命令就会自动进表，无需额外动作。没有模板的话，让 PaC-Learner(Stage 3.5b)在首次原始抓取时起草一个，或者把你自己的放到 `.olav/templates/paloalto_panos/show_system_info.textfsm`。对**永远不会解析**的命令(比如 `show config running`)，加到 `user_commands.yaml` 的 `backup_only` 下，让原文存进 `raw_output_store`:

```yaml
backup_only:
  paloalto_panos:
    - show config running
```

**Step 4(可选). 构建 recipe。** 通过 topology agent 让 LLM 起草 recipe:

```bash
olav --agent netops "discover bgp for paloalto_panos"
```

agent 从 `parsed_outputs` 采样,起草 recipe YAML,dry-run 视图验证,存入 `~/.olav/config/recipes/user/`。下次 `/netops_init` 自动加载,生成视图。

---

## 加新发现协议

你跑 IS-IS / FabricPath / 自有 overlay,想让拓扑图把这些邻居也收入。

**只改 `discovery_protocols.yaml`** —— 别的不改:

```yaml
protocols:
  isis:
    name: ISIS
    link_type: L3
    commands:
      - show isis adjacency
      - show clns neighbors
    local_interface_fields: [interface, local_interface, INTERFACE]
    neighbor_device_fields: [system_id, neighbor, SYSTEM_ID]
    neighbor_interface_fields: [interface, neighbor_interface]
```

下次 `/netops_init` 后 `netops.topology_links` 就会有 `discovery_protocol = 'ISIS'` 的行,`link_type = 'L3'`。`v_l2_links_auto` 视图只保留 L2 链路;L3 视图要另写一个 `isis_neighbors` 概念的 recipe。

自定义协议没有内建的 raw-output regex 兜底 —— 它们依赖 ntc-templates 或 PaC-Learner 先填 `parsed_outputs`。若命令没有 TextFSM 模板,首次运行时 auto-learn 或 PaC 会起草一个。

---

## 加新运维概念

想给防火墙规则、IPsec 隧道、SLA 探针、CDN POP 指标、DWDM 光放段 —— 任何东西 —— 建视图?OLAV 不在乎概念是什么,只要写一份 recipe。

**建 `~/.olav/config/recipes/user/firewall_rules_paloalto_panos.yaml`:**

```yaml
- command: show security policy
  concept: firewall_rules
  vendor_hint: paloalto_panos
  field_mappings:
    rule_name: NAME
    source_zone: FROM
    destination_zone: TO
    action: ACTION
    source_address: SRC_ADDR
    destination_address: DST_ADDR
    service: SERVICE
  filter_expr: "action != 'deny'"
```

concept 名字任意,只需匹配 `[a-z][a-z0-9_]*`。下次 `/netops_init` 或 `take_snapshot`:

1. `recipe_seeds` UPSERT 到 `view_recipes`
2. `view_builder` 生成 `CREATE OR REPLACE VIEW netops.v_firewall_rules_auto`
3. agent 可以直接查 `SELECT * FROM netops.v_firewall_rules_auto WHERE source_zone = 'untrust'`

跨厂商概念:每厂商写一份 recipe,合到同一个视图里(内部走 `UNION ALL`)。想直接投影到已有表的通用概念:`command` 写成 `'@<table>'` 形式的指令。

---

## 换 Nornir inventory 后端

设备清单在 **NetBox**(或 Ansible,或自建 API)里,不想在 `hosts.yaml` 里再维护一份。

**改 Nornir 的 `config.yaml`:**

```yaml
inventory:
  plugin: NetBoxInventory2
  options:
    nb_url: https://netbox.example.com
    nb_token: ${NETBOX_TOKEN}
```

装 `nornir-netbox`,完事。OLAV 的 Device ETL、采集 pipeline、拓扑引擎全部通过 `nr.inventory.hosts[name].platform / hostname / data` 读取 —— 它们不知道也不关心 Nornir 是从哪个后端拿的数据。

---

## 决策树

```
新设备要纳管?                         → Nornir inventory
要采集新的 CLI show 命令?             → 发一份 ntc-templates .textfsm(自动识别)
要采集纯备份的原始命令?               → user_commands.yaml(backup_only 列表)
要收集新的邻居发现协议?               → discovery_protocols.yaml
要一个平台的 model/version/Scrapli?   → platform_profiles.yaml
要建一个新的可查询概念(视图)?        → recipes/*.yaml(builtin 或 user)
要在 Stage 3.6 提醒缺失的 recipe?     → topology.yaml(protocols 列表)
想把设备源切到 NetBox/Ansible?       → Nornir config.yaml(inventory.plugin)
```

上述任何一种修改都不需要重打 wheel、不需要重启 agent。`/netops_init` 和 `take_snapshot` 每次运行时都重读 YAML;`view_recipes` 每次都 UPSERT;视图用 `CREATE OR REPLACE` 重建。
