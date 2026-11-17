# Generating Automation Scripts

!!! info "Renamed capability"
    The "Creator Agent" page previously described a Skill Builder feature that generated editable Python tool code from OpenAPI schemas. That feature has been superseded in v0.13.0.

    The recommended paths are now:

    - **Quick API access** → `olav registry register <url>` *(unchanged — [Connect a Service](connect-a-service.md))*
    - **Custom automation scripts** → `olav --agent devops "write a script..."` *(new — [DevOps Agent](../netops/devops-agent/index.md))*

---

## What Changed

| Old approach (≤ v0.12) | New approach (v0.13+) |
|---|---|
| `olav --agent admin "create a skill for <url>"` | `olav --agent devops "write a script to..."` |
| Generated editable Python tool code | Generates executable bash/Python scripts |
| Required manual workspace management | Scripts exported to `exports/scripts/` automatically |
| Required knowing OpenAPI schema details | Agent reads real device/service data from database |

---

## The DevOps Agent: Script Generation

For generating scripts that interact with your registered services or network infrastructure:

```bash
olav --agent devops "write a script that queries all NetBox devices and exports their IPs to a CSV"
olav --agent devops "write a Zabbix maintenance window script for all core routers"
```

The DevOps agent:

1. **Queries your actual infrastructure data** (`netops.devices`, registered service metadata)
2. **Generates environment-aware scripts** using real device names, IPs, and credentials
3. **Includes safety features** — `--dry-run` mode, error handling, execution summary
4. **Exports to `exports/scripts/`** as executable files

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-20 | DevOps agent generates environment-aware automation scripts | ✅ v0.13.0 |

---

## For Quick API Access

If you simply want to query a REST API without writing code, `registry register` is still the fastest path:

```bash
olav registry register http://zabbix.internal/api_jsonrpc.php
olav --agent devops "show all Zabbix hosts with active alerts"
```

See [Connect a Service](connect-a-service.md) for the full workflow.
