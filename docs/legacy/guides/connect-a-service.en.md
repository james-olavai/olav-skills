# Connect an External Service

OLAV can connect to any external service that provides a REST API. Once connected, you can query it using natural language or generate automation scripts for it.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-19 | `olav registry register <url>` registers an OpenAPI service with a single command | ✅ v0.10.0 |
    | C-L2-20 | DevOps agent generates environment-aware automation scripts | ✅ v0.13.0 |

---

## Use Cases

Your team likely uses multiple operations tools: NetBox (IPAM), Zabbix (monitoring), ServiceNow (ticketing), in-house platforms, and more. Each tool has its own API and query methods. OLAV lets you query across systems with a single sentence:

```bash
olav "How many devices are in rack A1?"        # queries NetBox
olav "What active alerts are there in Zabbix?"  # queries Zabbix
olav "List all sites in Europe"                 # queries NetBox
```

---

## Quick Registration: One Command

If the target service provides an OpenAPI (Swagger) specification, registration takes just one command:

```bash
olav registry register http://netbox.example.com/api/schema/
```

OLAV automatically reads the OpenAPI schema, generates API reference documentation, and makes the service queryable via `api_request`. No code generation — agents use the reference to construct API calls directly.

### Services That Require Authentication

Most services require an API Token or other authentication method:

```bash
olav registry register http://netbox.example.com/api/schema/ \
  --header "Authorization: Token YOUR_TOKEN"
```

OLAV's service registration supports multiple authentication methods (Bearer Token, API Key, Basic Auth, JWT), configured in `.olav/config/services.yaml`.

### Managing Registered Services

```bash
olav registry list                        # list all registered services
olav registry status netbox               # check reachability of a service
olav registry refresh netbox              # force re-fetch the schema
```

---

## Generating Automation Scripts

After connecting a service, the DevOps agent can generate production-ready scripts that interact with your infrastructure:

```bash
olav --agent devops "write a script that pulls all NetBox devices and exports them to CSV"
olav --agent devops "write a Zabbix maintenance window script for all routers in site LON1"
```

The DevOps agent reads your actual device data from the OLAV database and generates environment-aware scripts with `--dry-run` mode and error handling. Scripts are exported to `exports/scripts/` as executable files.

See [DevOps Agent →](../netops/devops-agent/index.md) and [Infra Agent →](../netops/infra-agent/index.md) for details.

---

## Comparison of Approaches

| | `registry register` | DevOps agent |
|-|---------------------|--------------|
| Speed | Completes in seconds | Generates scripts in one query |
| Output | Generic API proxy for natural language queries | Executable bash/Python scripts |
| Device awareness | No | Yes — reads from `netops.devices` |
| Best for | Ad-hoc queries, quick lookups | Automated workflows, CI/CD integration |

---

## NetBox CSV Sync (v0.21.1+) {#netbox-csv-sync}

A third pattern — auditable bulk data push — uses a CSV file as the
contract between read (netops) and write (services) agents.  The CSV
is git-diffable, replayable across environments, and reviewable before
any push hits NetBox.

```
┌────────────┐  /export_netbox_csv  ┌──────────────┐  /import_netbox_csv   ┌─────────────┐
│   netops   │ ───────────────────▶ │ exports/.csv │ ────────────────────▶ │  services   │
│  (netops.  │  rows + tenant       │  (audit gate │  validate + push      │  (NetBox    │
│  devices)  │  + watermark         │   via git    │  via /api/dcim/       │   REST)     │
│            │                      │   diff)      │  with idempotent FKs  │             │
└────────────┘                      └──────────────┘                       └─────────────┘
```

### Phase A — Export the CSV

```bash
olav --agent netops "/export_netbox_csv"
# → exports/netbox_devices.csv (header + 1 row per device)
# Optional: --filename my_batch --tenant team-foo
```

The 11-column contract — `name, device_role, manufacturer, device_type,
primary_ip4, platform, site, status, tenant, snapshot_id, exported_at`
— is documented in `netops/guides/netbox_csv_export.guide.yaml` (a
platform-tier guide).  `tenant` defaults to whatever the
`netbox_tenant_default` team-tier guide says, so a single
`olav kb import-guides` updates every subsequent export.

### Phase B — Dry-run validate

```bash
olav --agent services "/import_netbox_csv"
# → exports/reports/netbox_import_dry_run.md
# exit 0 if all rows would create cleanly; exit 1 on validation failures
```

Validations: required fields present, IPv4 parses, status in NetBox's
allowed set, `exported_at` ISO-8601, column drift detected.  This is
the safe operator HITL gate — never touches HTTP, never modifies
NetBox, always free to run in CI.

### Phase C — Real push (`--write`)

Once the dry-run passes, push to a registered NetBox:

```bash
export NETBOX_TOKEN="nbt_<your-token>"
olav --agent services "/import_netbox_csv --write \
  --endpoint http://localhost:8000 --token $NETBOX_TOKEN"
```

Idempotent 5-step lookup-or-create per row:

1. **site** — `GET /api/dcim/sites/?name=<X>`; POST if missing
2. **manufacturer** — `GET /api/dcim/manufacturers/?name=<X>`; POST
3. **device_type** — `GET ?model=<X>&manufacturer_id=<Y>`; POST
4. **device_role** — `GET /api/dcim/device-roles/?name=<X>`; POST
5. **platform** — `GET /api/dcim/platforms/?name=<X>`; POST (skipped if column empty)
6. **device** — `GET ?name=<X>&site_id=<Y>`; POST with all resolved FKs

Re-running the same CSV after a successful push returns
`0 created / N existed / 0 failed` — safe to script against cron.

Every `--write` invocation drops a `kb_audit/<ts>_netbox_push.yaml`
row (`action: netbox_push / actor / csv_body_sha256 / endpoint /
results{created,existed,failed,skipped_validation}`) so the bytes that
were sent are tied back to the exact CSV via sha256, even after the
CSV is regenerated.  See [Knowledge Base → kb_audit](knowledge-base.md#kb-audit-changelog).

### Why a CSV intermediate?

* **Audit gate** — the CSV is reviewable in git diff before any push
  modifies NetBox; the `kb_audit/` row pins the bytes that were
  actually sent.
* **Decoupling** — netops only knows the OLAV `netops.devices`
  schema; services only knows NetBox's API quirks (FK order, slug
  conventions, status enum).  Neither agent learns the other's domain.
* **Replayability** — the same CSV can dry-run against staging and
  push to prod with identical semantics.
* **Pre-NetBox HITL** — operators can edit the CSV by hand between
  Phase A and Phase B if the export needs adjustment, without
  touching the source `netops.devices` table.
