# Configuration

Each agent and skill has its own `config/` directory. Configuration travels with the skill — if you install or move a skill, its config goes with it.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-NE-12 | `nornir/hosts.yaml` defines the device inventory for collection | ✅ v0.10.0 |
    | C-NE-13 | `blacklisted_commands.yaml` supports regex patterns; invalid patterns are skipped with a warning | ✅ v0.10.0 |
    | C-NE-14 | `cron_schedules.yaml` customizes the trace_learner cron schedule | ✅ v0.10.0 |

---

## Configuration Architecture

```
.olav/config/                              ← Platform (4 files only)
├── api.json                               # LLM keys, model config
├── api.json.example                       # Template
├── services.yaml                          # Registered API services
└── approval_rules.yaml                    # HITL approval rules

.olav/workspace/
├── netops/config/                         ← NetOps orchestrator (shared)
│   ├── blacklisted_commands.yaml          # Command blacklist (all sub-agents)
│   └── netops_settings.yaml              # General NetOps settings
│
├── netops/collector/config/               ← collector sub-agent (SSH)
│   ├── nornir/
│   │   ├── hosts.yaml                    # Device inventory
│   │   ├── groups.yaml                   # Group credentials
│   │   ├── defaults.yaml                 # Global defaults
│   │   └── config.yaml                   # Nornir runner settings
│   └── default_commands.yaml             # Commands per platform
│
├── netops/analyzer/references/            ← analyzer sub-agent (change-plan drafter)
│   └── *.guide.yaml                      # Usage guides / expert knowledge
│
├── netops/simulator/references/           ← simulator sub-agent (Batfish)
│   └── *.guide.yaml                      # Batfish question recipes / guides
│
├── netops/topology/references/            ← topology sub-agent
│   └── *.guide.yaml                      # Topology recipes / guides
│
└── audit/profiles/                        ← Audit agent
    ├── bgp_health.md                     # BGP health check profile
    └── health_full_drift.md              # Full drift audit profile
```

**Principle:** Platform config in `.olav/config/`. Skill config in each skill's directory. No cross-skill config dependencies.

---

## Probe Agent — SSH & Device Collection

### nornir/hosts.yaml

Device inventory. One entry per device.

```yaml
R1:
  hostname: 192.168.100.101
  platform: juniper_junos
  groups:
    - core_routers

R2:
  hostname: 192.168.100.102
  platform: cisco_ios
  groups:
    - core_routers

SW1:
  hostname: 192.168.100.105
  platform: cisco_ios
  groups:
    - access_switches
```

Supported `platform` values: `cisco_ios`, `cisco_nxos`, `arista_eos`, `juniper_junos`, etc. ([NTC-Templates naming](https://github.com/networktocode/ntc-templates))

### nornir/defaults.yaml

Global SSH credentials used for all devices (unless overridden per host or group).

```bash
# Copy the example file and fill in your credentials
cp defaults.yaml.example defaults.yaml
chmod 600 defaults.yaml
```

```yaml
username: your_ssh_username
password: your_ssh_password
port: 22

connection_options:
  netmiko:
    extras:
      device_type: cisco_ios
```

!!! warning "Plain text passwords"
    Nornir does **not** support environment variable expansion (`${VAR}`) in YAML files. Passwords must be written in plain text. **Never commit `defaults.yaml` to version control** — it is gitignored by default. Use `chmod 600` to restrict file permissions.

### nornir/groups.yaml

Group-level settings (platform, role). Credentials can be set per group to override defaults:

```yaml
cisco_ios:
  platform: cisco_ios
  connection_options:
    netmiko:
      extras:
        device_type: cisco_ios

juniper_junos:
  platform: juniper_junos
  connection_options:
    netmiko:
      extras:
        device_type: juniper_junos
```

To use different credentials per group, add `username`/`password` fields:

```yaml
lab_devices:
  username: lab_admin
  password: lab_password
  data:
    role: test
```

### default_commands.yaml

Commands run on every device during collection.

```yaml
- show version
- show running-config
- show interfaces
- show ip interface brief
- show clock
```

---

## NetOps Orchestrator — Shared Config

### blacklisted_commands.yaml

Regex patterns that block command execution across all agents. Even whitelisted commands are rejected if they match.

```yaml
- "reload"
- "write erase"
- "conf(igure)? t(erminal)?"
- "delete"
```

Invalid patterns are skipped with a warning log.

### netops_settings.yaml

General NetOps runtime settings.

---

## Infra Agent — API References

References are **auto-generated** by `olav registry register`. Do not edit manually — re-run register to update after API schema changes.

```bash
olav registry register http://netbox:8000     # generates netbox_*_api.md
olav registry register influxdb_netops        # generates influxdb_*_api.md
```

Each reference contains endpoint paths, parameters, and return fields in a condensed format the agent reads as static context.

---

## DevOps Agent — Script References

References provide context for script generation:

| File | Purpose |
|------|---------|
| `BASELINE_SCHEMA.md` | Database schema reference for environment discovery |
| `OLAV_PLATFORM_HEALTH.md` | Platform health check patterns |

Add custom references to teach the DevOps agent about your environment:

```bash
# Example: add Ansible patterns
cp my-ansible-guide.md .olav/workspace/devops/references/ansible_patterns.md
```

The agent loads all files in `references/` as static context.

---

## Audit Agent — Profiles

Profiles define reusable health checks. Created by the Designer, executed by the Auditor.

```bash
olav --agent audit "Create a BGP health check profile"
# → saves to audit/profiles/bgp_health.md

olav --agent audit "Run the bgp_health audit"
# → reads profile, executes SQL, renders Markdown report
```

Profile format: YAML frontmatter (jobs + queries) + Markdown body (correlation prompt).

---

## Scheduling

OLAV manages scheduled tasks via system crontab.

### Natural Language Control

```bash
olav "schedule a daily snapshot at 2am"
olav "show all scheduled jobs"
olav "cancel the weekly audit"
```

### cron_schedules.yaml

Declarative schedule definitions:

```yaml
schedules:
  snapshot:
    cron: "0 2 * * *"
    agent: config
    instruction: "take snapshot"

  trace_learner:
    cron: "0 3 * * *"
    agent: config
    instruction: "run trace learner"

  audit_weekly:
    cron: "0 6 * * 1"
    agent: audit
    instruction: "generate weekly compliance report"
```

Apply all:
```bash
olav --agent admin --auto-approve "apply cron schedules"
```

---

## Platform Config (.olav/config/)

Platform-level config (not skill-specific):

| File | Purpose |
|------|---------|
| `api.json` | LLM provider keys, model selection, timeouts |
| `services.yaml` | Registered API services (endpoints, auth, readonly flags) |
| `approval_rules.yaml` | HITL approval rules for dangerous operations |
| `api.json.example` | Template for new installations |

!!! warning "Never commit api.json"
    Contains LLM API keys and service credentials. Already in `.gitignore`.

---

## Skill Structure — How Agents Are Registered

### Workspace Directory

Each agent lives in `.olav/workspace/<name>/` with a standard structure:

```
.olav/workspace/netops/               ← Agent name
├── AGENT.md                       # Agent declaration (name, description)
├── SKILL.md                       # Tools, intents, static_context, metadata
├── MANIFEST.yaml                  # Route keywords, version, requirements
├── prompts/
│   └── system.md                  # System prompt (agent behavior)
├── tools/                         # Python @tool files
├── config/                        # Agent-specific configuration
├── references/                    # Static context files (loaded by agent)
├── analyzer/                      # Subagent (nested workspace) — default
├── simulator/                     # Subagent
├── collector/                     # Subagent
├── topology/                      # Subagent
├── importer/                      # Subagent
└── learner/                       # Subagent
```

### AGENT.md

Declares the agent for the platform. Minimal format:

```yaml
---
name: netops
description: "Network operations — change planning, simulation, drift detection"
subagents:
  - path: ./analyzer/SKILL.md
  - path: ./simulator/SKILL.md
  - path: ./collector/SKILL.md
  - path: ./topology/SKILL.md
  - path: ./importer/SKILL.md
  - path: ./learner/SKILL.md
system_prompt_file: prompts/system.md
---
```

### SKILL.md

Declares tools, metadata, and static context:

```yaml
---
name: analyzer
description: "Change-plan drafter"
metadata:
  version: 1.1.0
  type: agent
  network_isolation: "true"
tools:
  - execute_sql
static_context:
  - path: ./references/ROUTING_EXPERT_GUIDE.md
---
```

- `tools:` — List of tool names. Must match `.py` files in `tools/`.
- `static_context:` — Files loaded into the agent's context at startup.
- `metadata.network_isolation` — "true" for compute-only agents.

### MANIFEST.yaml

Used by `olav agent install` for routing and dependency checks:

```yaml
kind: Agent
name: netops
version: "0.25.0"
description: "Operations Agent"
route_keywords:
  - troubleshoot
  - bgp
  - ospf
requires:
  - olav>=0.24
```

### PLATFORM.md

The platform agent registry. Located at `.olav/workspace/PLATFORM.md`:

```yaml
---
active: core
agents:
  - admin
  - audit
  - core
  - devops
  - netops
  - services
---
```

- `active:` — Default agent when no `--agent` flag is used.
- `agents:` — List of registered agents. `olav agent install` adds to this list automatically.
- `olav list` reads this file.

---

## Installing a Skill (olav-netops)

```bash
# From local directory
olav agent install /path/to/olav-netops/

# From Git URL
olav agent install https://github.com/james-olavai/olav-netops
```

The skill's `workspace.yaml` declares where workspace files live:

```yaml
name: netops
version: "0.25.0"
source: .olav/workspace/netops    # copy from this subdirectory
requires:
  packages:
    - nornir>=3.3.0
    - nornir-netmiko>=1.0.0
```

After install:
1. Workspace files copied to `.olav/workspace/netops/`
2. Agent registered in `PLATFORM.md`
3. Missing packages reported as warnings

### Post-Install Setup

```bash
# 1. Copy config templates
cp .olav/workspace/netops/collector/config/nornir/hosts.yaml.example \
   .olav/workspace/netops/collector/config/nornir/hosts.yaml
# Edit with your device inventory

# 2. Initialize netops
olav --agent netops "/netops_init --dry-run"   # verify environment
olav --agent netops "/netops_init"              # collect device data
```

---

## Registering an API Service

```bash
olav registry register http://netbox:8000
```

This:
1. Adds service to `.olav/config/services.yaml`
2. Parses OpenAPI schema
3. Generates reference markdowns in `infra/references/`
4. Service is immediately queryable via `api_request`

### services.yaml format

```yaml
services:
  netbox:
    endpoint: http://netbox:8000
    auth:
      type: bearer
      token_env: NETBOX_TOKEN        # reads from environment variable
    readonly_only: true               # default: no writes allowed
    reference_generation:
      groups:
        - tag: dcim
        - tag: ipam
      output_dir: .olav/workspace/infra/references
```

- `readonly_only: true` — Service is read-only. Write attempts are blocked.
- `token_env` — Environment variable name (not the actual token).
- `reference_generation` — Controls which API groups get reference docs.
