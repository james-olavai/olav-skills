# Services Agent

The Services Agent wraps the common "talk to an external service" surface
— registering new services, deploying / stopping service containers, and
issuing authenticated HTTP calls to things like NetBox, InfluxDB, Gitea,
or ContainerLab.

!!! note "Top-level agent since Round 16 (v0.18.0)"
    Pre-v0.18, `deploy_service` / `stop_service` / `api_request` lived
    on `core/` as first-class tools. The v0.18.1 spec (ADR-0004) split
    them into a dedicated `services/` top-level agent so core's tool
    surface could shrink to 7 cross-domain capabilities (ADR-0006).
    The underlying tools are unchanged; only the orchestrator surface
    moved.

---

## Invocation

```bash
olav --agent services "register a new InfluxDB at http://..."
olav --agent services "deploy netbox"
olav --agent services "stop netbox"
olav --agent services "GET /api/v2/query?q=..."
```

Services has 4 advertised tools:

| Tool | Purpose |
|---|---|
| `register_service` | Add a new service to `.olav/config/services.yaml` (idempotent; refuses to overwrite). Supports `none` / `bearer` / `basic` auth via `auth_token_env`. |
| `deploy_service` | Start a registered service (typically a container). |
| `stop_service` | Stop a registered service. |
| `api_request` | Authenticated HTTP call to any service in `services.yaml`. Response is compact-by-default; lists truncated to `_COMPACT_LIST_CAP` for small models. |

---

## services.yaml format

```yaml
# .olav/config/services.yaml
services:
  - name: netbox
    endpoint: http://netbox.local:8000
    auth_type: bearer
    auth_token_env: NETBOX_API_TOKEN     # resolved at request time

  - name: influxdb
    endpoint: http://influx.local:8086
    auth_type: bearer
    auth_token_env: INFLUXDB_TOKEN

  - name: gitea
    endpoint: http://gitea.local:3000
    auth_type: basic
    auth_token_env: GITEA_BASIC_CREDS
```

`auth_token_env` names the env var that holds the token — resolved at
call time, not at service registration. Tokens are never persisted in
the YAML file.

---

## Escalation from core

When the core agent receives a query that matches service-integration
keywords, the orchestrator escalates via `--agent services`:

| Query contains | Routes to |
|---|---|
| "register service", "services.yaml", "new service" | services |
| "deploy service", "start netbox", "bring up influxdb" | services |
| "stop service" | services |
| "call NetBox API", "query InfluxDB", "GET from Gitea" | services |

---

## Governance

`services/` is the first **approved precedent** for the
new-top-level-agent extension policy (`docs/adr/0004` in the source
repo) — it meets the four criteria (independent domain, ≥3 domain-
specific tools, independent lifecycle, governance-pinned).
`tests/governance/test_services_agent.py` holds 14 pins covering
AGENT.md + SKILL.md + tool surface + symlink wiring back to core's
canonical tool files (ARCH-20 Phase 2 dedup pattern).
