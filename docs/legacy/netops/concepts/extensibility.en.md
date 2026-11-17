# Extensibility

OLAV NetOps is designed so that adding a new vendor, a new discovery protocol, or a new operational concept is a **YAML change** — never a Python change. This page lists every extension path, what file you touch, and what happens downstream.

---

## Principle: a few YAML files + one DB table, no code

Every piece of domain knowledge lives in one of these places. Python code reads them at runtime; swapping values or adding entries never requires a rebuild or redeploy of olav-netops.

| File / table | Owner | Purpose |
|---|---|---|
| Nornir inventory (any backend) | Nornir config | Device list + `platform` + `hostname` + `environment` |
| `netops.commands` (DuckDB table) | `/netops_init` Stage 0 | **Per-platform CLI command SSOT.** Auto-populated by `sync_commands` scanning ntc-templates filenames (`<vendor>_<os>_<command>.textfsm`). No YAML — extend ntc-templates coverage and the commands show up automatically. |
| `.olav/workspace/netops/netops_init/config/user_commands.yaml` (+ optional `~/.olav/config/user_commands.yaml` override) | ops skill + user | Backup-only raw captures (show run / show config) that have no TextFSM template and live in `raw_output_store` for LLM reasoning |
| `.olav/workspace/netops/netops_init/config/discovery_protocols.yaml` | ops skill | Neighbour-discovery protocols the topology ETL extracts |
| `.olav/workspace/netops/netops_init/config/platform_profiles.yaml` | ops skill | Vendor name + show-version field hints + Scrapli mapping |
| `.olav/workspace/topology/recipes/builtin/*.yaml` + `~/.olav/config/recipes/user/*.yaml` | topology skill + user | Canonical concept → raw field mappings (drives SQL views) |
| `~/.olav/config/topology.yaml` | user | Intent — which relationships to care about |

---

## Add a new vendor

You have a new device — say a **Palo Alto PAN-OS** firewall — and want OLAV to manage it.

**Step 1. Declare the device in Nornir.** Whichever inventory backend you use (SimpleInventory YAML, NetBox, Ansible, Dict, or custom):

```yaml
FW1:
  hostname: 10.0.0.10
  platform: paloalto_panos
  username: admin
  groups: [prod]
```

That's the minimum. After the next `/netops_init`, `netops.devices` gets a row:

```
FW1 | paloalto_panos | Palo Alto | 10.0.0.10 | (model NULL) | (os_version NULL)
```

`vendor` is filled by rule-based inference — `paloalto_panos` → "Palo Alto". No YAML edit needed.

**Step 2 (optional). Add a platform profile.** If you want `model` / `os_version` populated from `show version`, add an entry to `platform_profiles.yaml`:

```yaml
paloalto_panos:
  vendor: Palo Alto
  scrapli_platform: paloalto_panos
  model_fields: [MODEL, SYSTEM_MODEL]
  os_version_fields: [VERSION, SW_VERSION]
```

**Step 3 (optional). Extend the command catalog.** `netops.commands` is populated automatically at `/netops_init` Stage 0 by scanning ntc-templates filenames — if there are templates named `paloalto_panos_show_system_info.textfsm` / `paloalto_panos_show_routing_route.textfsm`, the corresponding commands land in the table with no further action. If no template exists yet, let PaC-Learner (Stage 3.5b) draft one on first raw capture, or drop your own at `.olav/templates/paloalto_panos/show_system_info.textfsm`. For commands that will never parse (e.g. `show config running`), add them to `user_commands.yaml` under the `backup_only` list so the raw text is captured to `raw_output_store`:

```yaml
backup_only:
  paloalto_panos:
    - show config running
```

**Step 4 (optional). Build recipes.** Run the topology agent to draft recipes via LLM:

```bash
olav --agent netops "discover bgp for paloalto_panos"
```

The agent samples `parsed_outputs`, drafts a recipe YAML, validates with a dry-run view, and saves to `~/.olav/config/recipes/user/`. Next `/netops_init` picks it up and builds the view.

---

## Add a new discovery protocol

You run an IS-IS / FabricPath / proprietary overlay and want the topology map to include those adjacencies.

**Edit `discovery_protocols.yaml`** — nothing else:

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

After the next `/netops_init`, `netops.topology_links` gets rows with `discovery_protocol = 'ISIS'`, `link_type = 'L3'`. The `v_l2_links_auto` view keeps the L2-only rows; for L3 links, write a recipe for a new concept like `isis_neighbors`.

No built-in raw-output regex fallback for custom protocols — they rely on ntc-templates or PaC-Learner populating `parsed_outputs` first. If the command has no TextFSM template, auto-learn or PaC will draft one on first run.

---

## Add a new operational concept

Want to build a view over firewall rules, IPsec tunnels, SLA probes, CDN POP metrics, DWDM spans, anything? OLAV doesn't care what the concept is — just write a recipe.

**Create `~/.olav/config/recipes/user/firewall_rules_paloalto_panos.yaml`:**

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

The concept name (`firewall_rules`) only needs to match `[a-z][a-z0-9_]*`. Next `/netops_init` or `take_snapshot`:

1. `recipe_seeds` upserts the row into `view_recipes`
2. `view_builder` generates `CREATE OR REPLACE VIEW netops.v_firewall_rules_auto`
3. Agent can now query `SELECT * FROM netops.v_firewall_rules_auto WHERE source_zone = 'untrust'`

For cross-vendor concepts, ship one recipe per vendor (they merge into the same view via `UNION ALL`). For universal concepts — e.g. anything that projects directly from an existing table — use `command: '@<table>'` as a directive.

---

## Swap the Nornir inventory backend

Your hosts live in **NetBox** (or Ansible, or a custom API) and you don't want a second source of truth in `hosts.yaml`.

**Edit Nornir's `config.yaml`:**

```yaml
inventory:
  plugin: NetBoxInventory2
  options:
    nb_url: https://netbox.example.com
    nb_token: ${NETBOX_TOKEN}
```

Install `nornir-netbox`. That's it — OLAV's Device ETL, collection pipeline, and topology engine all read via `nr.inventory.hosts[name].platform / hostname / data`. They don't know or care where Nornir got the data from.

---

## Decision tree

```
Is it a new device you just want to manage?         → Nornir inventory
Is it a new CLI show command?                       → ship a ntc-templates .textfsm (auto-detected)
Is it a backup-only raw capture?                    → user_commands.yaml (backup_only list)
Is it a new neighbour-discovery protocol?           → discovery_protocols.yaml
Do you need model/version/Scrapli for a platform?   → platform_profiles.yaml
Is it a new queryable concept (view)?               → recipes/*.yaml (builtin or user)
Do you want Stage 3.6 to warn when a concept is
  missing recipes for your deployment's vendors?    → topology.yaml (protocols list)
Do you want to move devices to NetBox/Ansible?      → Nornir config.yaml (inventory.plugin)
```

None of these edits require a wheel rebuild or agent restart. `/netops_init` and `take_snapshot` re-read YAML on every run; `view_recipes` is UPSERTed on each pipeline pass; views are rebuilt with `CREATE OR REPLACE`.
