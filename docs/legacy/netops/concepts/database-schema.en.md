# Database Schema

All NetOps data lives in the `netops` DuckDB schema. Tables are auto-created when olav-netops is installed; views are rebuilt on each `/netops_init` run and after every `take_snapshot`.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NE-08 | `netops.devices` carries hostname, ip_address, platform, vendor | ✅ v0.10.0 |
    | C-NE-09 | `v_bgp_neighbors_auto` is built from `view_recipes` + `parsed_outputs` (pure SQL, zero LLM) | ✅ v0.11.0 |
    | C-NE-10 | `netops.topology_links` carries `discovery_protocol` to distinguish L2/L3 | ✅ v0.10.0 |
    | C-NE-11 | `netops.raw_output_store` preserves latest raw CLI output per device | ✅ v0.10.0 |
    | C-NE-12 | Builtin / user recipe YAML drives view generation; no hardcoded view DDL | ✅ v0.11.0 |

---

## Tables

### netops.devices

Network device inventory — written by the Device ETL stage of `/netops_init`. Nornir inventory is the authoritative source for `hostname`, `ip_address`, and `platform`; `show version` parse output enriches `model` / `os_version` via `platform_profiles.yaml`.

| Column | Type | Description |
|--------|------|-------------|
| `hostname` | VARCHAR | Device hostname — primary key |
| `ip_address` | VARCHAR | Management IP (Nornir `host.hostname` — the SSH target) |
| `platform` | VARCHAR | Netmiko platform string (e.g. `cisco_ios`, `juniper_junos`) |
| `site` | VARCHAR | Site/location tag (reserved; populated by user workflows) |
| `role` | VARCHAR | Device role (reserved; populated by user workflows) |
| `vendor` | VARCHAR | Display vendor — YAML profile if known, rule-inferred otherwise |
| `model` | VARCHAR | Hardware model (from `show version` via `platform_profiles.model_fields`) |
| `os_version` | VARCHAR | OS version (from `show version` via `platform_profiles.os_version_fields`) |
| `environment` | VARCHAR | Nornir `data.environment` tag (`lab` / `prod` / `staging` / `dev`) |
| `last_seen` | TIMESTAMP | Last UPSERT timestamp |
| `metadata` | JSON | Free-form per-device metadata |

### netops.parsed_outputs

Structured CLI output. One row per (device, command, snapshot). `raw_output` is nulled after ingest once the hash is stored in `raw_output_store`.

| Column | Type | Description |
|--------|------|-------------|
| `device_name` | VARCHAR | Source device hostname |
| `command` | VARCHAR | CLI command executed |
| `parsed_data` | JSON | Array of parsed records (TextFSM / ntc-templates / PaC Python parser) |
| `snapshot_id` | VARCHAR | Snapshot identifier |
| `raw_output` | TEXT | Legacy — nulled out during ingest migration |
| `raw_output_hash` | VARCHAR | FK into `raw_output_store.content_hash` (dedup) |
| `ingested_at` | TIMESTAMP | Ingest timestamp |

Unique key: `(device_name, command, snapshot_id)`.

### netops.raw_output_store

Latest raw CLI output per (device, command). One row per pair — overwritten on each snapshot.

| Column | Type | Description |
|--------|------|-------------|
| `device_name` | VARCHAR | Source device hostname |
| `command` | VARCHAR | CLI command |
| `raw_output` | TEXT | Full unprocessed CLI output |
| `snapshot_id` | VARCHAR | Snapshot identifier |
| `updated_at` | TIMESTAMP | Last overwrite timestamp |

Unique key: `(device_name, command)`.

### netops.topology_links

Neighbour relationships derived from CDP / LLDP (and any other discovery protocol declared in `discovery_protocols.yaml`). The Topology ETL runs after ingest on every snapshot.

| Column | Type | Description |
|--------|------|-------------|
| `link_id` | VARCHAR | Deterministic bidirectional ID (A↔B and B↔A collapse) — primary key |
| `source_device` | VARCHAR | Source device hostname |
| `source_interface` | VARCHAR | Source interface name |
| `destination_device` | VARCHAR | Destination device hostname |
| `destination_interface` | VARCHAR | Destination interface name |
| `discovery_protocol` | VARCHAR | `CDP` / `LLDP` / user-declared protocol name |
| `link_type` | VARCHAR | `L2` / `L3` / overlay / other (from protocol spec) |
| `link_status` | VARCHAR | `up` / `down` |
| `link_speed` | VARCHAR | Optional link speed |
| `first_seen` | TIMESTAMP | First observation |
| `last_seen` | TIMESTAMP | Most recent observation |
| `last_verified` | TIMESTAMP | Most recent verification pass |
| `status_changes` | INTEGER | Count of status transitions |
| `snapshot_id` | VARCHAR | Snapshot identifier of latest observation |
| `platform` | VARCHAR | Source-device platform |

### netops.oc_outputs

Strict OpenConfig JSON per (device, snapshot, module) — sparse, populated only when OC mappings are wired.

| Column | Type | Description |
|--------|------|-------------|
| `device_name` | VARCHAR | Source device hostname |
| `snapshot_id` | VARCHAR | Snapshot identifier |
| `oc_module` | VARCHAR | OpenConfig YANG module |
| `oc_data` | JSON | Structured OpenConfig data |
| `source_cmd` | VARCHAR | Originating CLI command |

Unique key: `(device_name, snapshot_id, oc_module)`.

### main.view_recipes

Recipe table driving all auto-generated views. Seeded from `recipes/builtin/*.yaml` (shipped with olav-netops) and `~/.olav/config/recipes/user/*.yaml` (user / agent-drafted).

| Column | Type | Description |
|--------|------|-------------|
| `command` | VARCHAR | CLI command the recipe maps from |
| `concept` | VARCHAR | Canonical concept (`bgp_neighbors`, `ospf_neighbors`, …) |
| `vendor_hint` | VARCHAR | Platform string or `universal` |
| `field_mappings` | JSON | Canonical → raw field name map |
| `filter_expr` | VARCHAR | Optional SQL filter clause |
| `discovered_at` | TIMESTAMP | Seed / discovery timestamp |

Unique key: `(command, concept, vendor_hint)`.

---

## Auto-Generated Views

Views are rebuilt by `view_builder.build_all_views` at the end of every `/netops_init` and post-hook-triggered on every `take_snapshot`. They read from `view_recipes` + `parsed_outputs` (or `topology_links` for L2) using pure SQL — no LLM runtime cost.

| View | Key Columns | Backed By |
|------|-------------|-----------|
| `v_bgp_neighbors_auto` | device, neighbor_ip, neighbor_as, local_as, router_id, state, uptime | `bgp_neighbors` recipes |
| `v_ospf_neighbors_auto` | device, neighbor_id, neighbor_ip, interface, state | `ospf_neighbors` recipes |
| `v_l2_links_auto` | source_device, source_interface, destination_device, destination_interface, discovery_protocol, link_status | Direct projection from `netops.topology_links` |

!!! tip "Adding a new view"
    Drop a recipe YAML in `.olav/workspace/topology/recipes/builtin/` (shipped) or `~/.olav/config/recipes/user/` (per-install). Any concept name matching `[a-z][a-z0-9_]*` is accepted — the next `/netops_init` or `take_snapshot` rebuilds `v_<concept>_auto` automatically. See [Extensibility](extensibility.md).

---

## Querying

All agents use `execute_sql` to query these tables:

```sql
-- Always qualify netops schema tables
SELECT * FROM netops.devices WHERE platform = 'cisco_ios';

-- Views live in netops schema too
SELECT * FROM netops.v_bgp_neighbors_auto WHERE state != 'Established';

-- Cross-snapshot comparison
SELECT * FROM netops.parsed_outputs
WHERE snapshot_id IN ('snap-001', 'snap-002')
  AND command = 'show bgp summary';
```

!!! warning "Schema prefix required"
    Every NetOps object lives in the `netops` DuckDB schema — tables and views alike. Use `netops.v_bgp_neighbors_auto`, never bare `v_bgp_neighbors_auto`. The only exception is `main.view_recipes`, which is a platform-level config table.
