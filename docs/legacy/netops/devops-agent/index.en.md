# DevOps Agent

The DevOps Agent writes production-quality automation scripts tailored to your actual infrastructure. Unlike generic code assistants, it knows your devices, services, topology, and credentials configuration.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NE-41 | DevOps agent queries `netops.devices` before generating any script | ✅ v0.13.1 |
    | C-NE-42 | Generated bash scripts include `--dry-run` + error handling + summary | ✅ v0.13.1 |
    | C-NE-43 | Scripts use real device names and IPs from the database, not placeholders | ✅ v0.13.1 |
    | C-NE-44 | Scripts are exported to `exports/scripts/` as executable files | ✅ v0.13.1 |

    *Legend: ⚠️ (E2E-gated) = test exists but requires env-var flag, skipped in CI; ⚠️ (manual) = demo-verified only, no automated CI test*

---

## How It Works

```bash
olav --agent devops "Write a script to backup running-config from all routers"
```

The agent:

1. **Discovers your environment** — queries `netops.devices` for real device names, IPs, platforms
2. **Generates a complete script** — tailored to your actual devices (e.g., Cisco IOS vs Juniper Junos commands)
3. **Exports to file** — saved to `exports/scripts/` as an executable `.sh` or `.py` file
4. **Reports** — tells you the file path, how to dry-run, and how to execute

---

## Environment Awareness

The agent reads your infrastructure before writing any code:

| Data Source | What It Provides |
|-------------|-----------------|
| `netops.devices` | Device names, IPs, platforms, roles, sites |
| `netops.topology_links` | Network topology (who connects to whom) |
| `services.yaml` | Registered service URLs and auth config |
| `infra/references/*.md` | API endpoint documentation |

**Scripts contain your real data.** When you have R1 (192.168.100.101, Cisco IOS) and R2 (192.168.100.102, Juniper Junos), the generated script includes platform-specific commands for each.

---

## Script Standards

Every generated script includes:

- `#!/bin/bash` + `set -euo pipefail`
- Environment variable validation (`${NETBOX_TOKEN:?required}`)
- Dependency checks (`command -v curl || exit 1`)
- `--dry-run` flag (print what would happen without executing)
- Per-operation error handling
- Idempotent behavior (check-before-create)
- Summary at the end (success/failed/skipped counts)

---

## Use Cases

| You Say | Agent Generates |
|---------|----------------|
| "Backup all router configs" | Bash: SSH per device, platform-aware show commands, save to files |
| "Import devices into NetBox" | Bash: curl POST per device, --dry-run, skip existing |
| "Collect interface counters to InfluxDB" | Python: query DB + format line protocol + POST to InfluxDB |
| "Write Ansible playbook for OSPF" | YAML: playbook with host vars from database |
| "Pre-check script for site migration" | Bash: verify BGP/OSPF state, backup configs, check routes |

---

## DevOps vs Other Agents

| | DevOps | Infra | Ops |
|---|:---:|:---:|:---:|
| **Output** | Script files | Query results | Operational actions |
| **Executes writes** | No (user runs scripts) | Yes (with --enable-api-write) | Yes (SSH/CLAB) |
| **Environment aware** | Full (DB + services + topology) | Services only | Devices only |
| **Use case** | Automation, migration, bulk ops | API queries, small updates | Troubleshooting, simulation |
