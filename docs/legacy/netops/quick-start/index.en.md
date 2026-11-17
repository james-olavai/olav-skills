# Quick Start

Get your network visible to OLAV in 5 minutes: install, configure devices, collect data, ask your first question.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NE-01 | `/netops_init --dry-run` validates environment + Nornir inventory | ✅ v0.17.0 |
    | C-NE-02 | `olav list` shows the 6 platform agents (admin, audit, core, devops, netops, services) after `olav agent install` | ✅ v0.18.0 |
    | C-NE-03 | `/netops_init` populates `netops.parsed_outputs` with platform-aware commands | ✅ v0.17.0 |
    | C-NE-04 | `/netops_init` generates topology + populates `netops.devices` | ✅ v0.17.0 |

    *Legend: ⚠️ (E2E-gated) = test exists but requires env-var flag, skipped in CI; ⚠️ (manual) = demo-verified only, no automated CI test*

---

## Step 1: Install OLAV + olav-netops

```bash
pip install olav olav-netops     # OLAV core + the network domain package
olav agent install olav-netops   # deploy the netops workspace and register the agent
```

`pip install` pulls both packages from PyPI. `olav agent install olav-netops` then resolves the installed package, deploys its workspace (the `netops` agent plus network-aware `audit`), and refreshes the agent routing table. (For development, point `olav agent install` at a local path or git URL instead: `olav agent install /path/to/olav-netops/`.)

!!! note "First run bootstraps core"
    On a brand-new project, run `olav` once first — it scaffolds the workspace, databases, and core agent, and prompts for your LLM API key. See [Installation](../../getting-started/installation.md) for the full core setup.

Verify:

```bash
olav list
```

Expected output — 6 top-level agents:

```
  • admin     — Platform self-management: health/log diagnostics, cron, skill install, workspace scaffolding
  • audit     — Compliance checks, drift detection, network health report generation
  • core      — Cross-domain: SQL, semantic recall, Python sandbox, web search, reports, cron
  • devops    — NetBox DCIM/IPAM, InfluxDB metrics, automation script library, HITL execution
  • netops    — Network troubleshooting: topology analysis, routing simulation, lab validation, probing
  • services  — Service integration: register APIs, deploy/stop containers, authenticated HTTP calls
```

---

## Step 2: Configure Your Devices

### Device inventory

```bash
cp .olav/workspace/netops/collector/config/nornir/hosts.yaml.example \
   .olav/workspace/netops/collector/config/nornir/hosts.yaml
nano .olav/workspace/netops/collector/config/nornir/hosts.yaml
```

Example `hosts.yaml`:

```yaml
R1:
  hostname: 192.168.100.101
  platform: juniper_junos
  groups:
    - border
    - juniper_junos

R2:
  hostname: 192.168.100.102
  platform: cisco_ios
  groups:
    - core
    - cisco_ios
```

### SSH credentials

```bash
cp .olav/workspace/netops/collector/config/nornir/defaults.yaml.example \
   .olav/workspace/netops/collector/config/nornir/defaults.yaml
chmod 600 .olav/workspace/netops/collector/config/nornir/defaults.yaml
nano .olav/workspace/netops/collector/config/nornir/defaults.yaml
```

```yaml
username: your_ssh_username
password: your_ssh_password
port: 22
```

!!! warning
    Nornir requires plain text passwords. `defaults.yaml` is gitignored — never commit it.

---

## Step 3: Run Initialization

```bash
olav --agent netops "/netops_init"
```

This runs a 4-stage pipeline:

| Stage | What happens |
|-------|-------------|
| 1. Environment Check | Validates Nornir config, SSH packages, inventory (group references verified) |
| 2. SSH Collection | Platform-aware commands: Junos devices get `show lldp neighbors`, `show interfaces terse`, etc.; IOS devices get `show cdp neighbors detail`, `show ip interface brief`, etc. |
| 3. Ingest + ETL | IngestManager writes to DuckDB → Topology ETL extracts CDP/LLDP links → Device ETL extracts hostname, IP, vendor, model, OS version |
| 4. Summary | Reports devices collected, commands, success/failure count, topology links |

!!! tip "Dry run first"
    Validates environment without SSH connections:
    ```bash
    olav --agent netops "/netops_init --dry-run"
    ```
    Checks: Nornir installed, hosts.yaml exists, group names match groups.yaml, inventory loads without error.

!!! info "Platform-aware collection"
    The pipeline automatically selects commands based on device platform:

    - **Cisco IOS**: 14 commands (show ip interface brief, show cdp neighbors detail, show running-config, ...)
    - **Juniper JunOS**: 11 commands (show interfaces terse, show lldp neighbors, show configuration, ...)
    - **Universal**: show version, show interfaces (all platforms)

---

## Step 4: Ask Your First Question

```bash
olav "How many devices are in my network?"
```

```bash
olav "Show me the topology"
```

```bash
olav "Are all BGP neighbors established?"
```

OLAV queries the collected data in DuckDB and returns structured answers. No device access is needed for queries — everything comes from the snapshot database.

---

## What's Next?

- [Basic Concepts](../concepts/index.md) — Understand how snapshots, templates, and agents work
- [Core Agent](../core-agent/index.md) — Daily queries and CLI execution
- [NetOps Agent](../netops-agent/index.md) — Deep analysis, simulation, and drift detection
