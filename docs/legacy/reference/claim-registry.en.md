# Feature Claim Registry

This page is the single index of all Feature Claims for the OLAV platform. Each Claim corresponds to a verifiable platform capability.

!!! info "Enterprise-tier claims (moved to olav-ent in 0.25.0)"
    Several claims below describe capabilities that shipped in `olav-ent` starting in **0.25.0** and are **no longer part of the open-source `olav` / `olav-netops` install**:
    the web service, HTTP API and browser UI (**C-L2-16, C-L2-29, C-L2-30, C-L2-42, C-L4-07**),
    the daemon accelerator (**C-L2-17**),
    and multi-user token / LDAP authentication and user management (**C-L2-23, C-L2-28, C-L2-44, C-L4-01, C-L4-02**).
    Open-source `olav service` provides the syslog receiver only; identity defaults to `auth.mode=none` (OS user = admin). Install `olav-ent` (`pip install olav-ent`) for these features.

**Methodology:** Doc-Driven Testing → Fresh Venv Validation (updated v0.17.0)

```
Document claim → Build wheel → Fresh venv install → CLI verification → Verified or Narrowed
```

> **v0.17.0 audit:** 12 of 118 claims (10%) that were ✅ in dev environment **failed** in fresh venv.
> All claims are now re-verified from `pip install dist/*.whl` in a clean environment.
> See `dev_docs/45. CLAIM_AUDIT_v0.17.md` for the full audit report.

**Status legend:**

- ✅ Verified — Passed CLI end-to-end verification, recorded in `dev_docs/23. CLI_VERIFICATION_LOG.md`
- ⬜ Pending — Code implemented, awaiting CLI verification
- ⚠️ Narrowed — Available under limited conditions; Claim scope has been narrowed
- 🔶 Env-Blocked — Requires an external environment (running service, CLAB, etc.) to verify

---

## Level 1 — Platform Core Capabilities

| ID | Claim | Doc Page | Status |
|----|-------|----------|--------|
| C-L1-01 | OLAV lets you control infrastructure with natural language | [Home](../index.md) | ✅ | v0.10.0 |

---

## Level 2 — Feature-Level Claims

### Basic Commands

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-01 | CLI correctly reports version and system info | `olav version` | [Installation](../getting-started/installation.md) | ✅ | v0.10.0 |
| C-L2-02 | CLI lists all available Agents with descriptions | `olav list` | [First Query](../getting-started/first-query.md) | ✅ | v0.10.0 |
| C-L2-03 | List and switch the active workspace | `olav workspace list/use` | [Manage Workspaces](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-13 | Initialize project directory structure + admin user + embedding model | `olav init` | [Installation](../getting-started/installation.md) | ✅ | v0.17.0 |
| C-L2-27 | Reset Agent conversation history | `olav reset --agent <name>` | [CLI Commands](cli.md) | ✅ | v0.10.0 |

### Skill Management

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-04 | Install a Skill from a local directory (single or multi-workspace) | `olav agent install <path>` | [Build a Skill](../guides/build-a-skill.md) | ✅ | v0.17.0 |
| C-L2-25 | Install a Skill from a Git URL | `olav agent install <url>` | [Build a Skill](../guides/build-a-skill.md) | ✅ | v0.10.0 |
| C-L2-06 | Append tools to an existing workspace | `olav agent install --merge-into` | [Build a Skill](../guides/build-a-skill.md) | ✅ ⚠️ | v0.10.0 |
| C-L2-36 | List and view installed skills | `olav skills list/info` | [Manage Workspaces](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-37 | Skills declaring Python dependencies auto-create an isolated venv | `requires_packages` in MANIFEST | [Build a Skill](../guides/build-a-skill.md) | ✅ | v0.10.0 |

### Service Registry

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-19 | Register an OpenAPI service with one command | `olav registry register <url>` | [Connect a Service](../guides/connect-a-service.md) | ✅ | v0.10.0 |
| C-L2-20 | DevOps agent generates environment-aware automation scripts | `olav --agent netops "write script..."` | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.17.1 |

### Audit and Logs

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-08 | Query audit logs and list recent operations | `olav log list/errors` | [Audit and Logs](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-22 | Export audit data in training formats | `olav log export trajectory/sft/atif` | [Audit and Logs](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-12 | Multi-user concurrent audit with no write conflicts | 5 concurrent `olav log list` | [Audit and Logs](../guides/audit-and-logs.md) | ✅ | v0.10.0 |

### Background Services

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-16 | Web service provides browser UI and REST API (HTTP + HTTPS cookie support) | `olav service web start` | [Background Services](../guides/services.md) | ✅ | v0.17.0 |
| C-L2-17 | Daemon accelerates CLI responses | `olav service daemon start` | [Background Services](../guides/services.md) | ✅ | v0.10.0 |
| C-L2-18 | Syslog receiver accepts network device logs (SO_REUSEADDR) | `olav service logs start` | [Background Services](../guides/services.md) | ✅ | v0.17.0 |

### HTTP API

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-29 | SSE streaming query endpoint | `POST /runs/stream` | [HTTP API](http-api.md) | ✅ | v0.10.0 |
| C-L2-30 | Multi-turn conversation thread management | `POST /threads` | [HTTP API](http-api.md) | ✅ | v0.10.0 |

### Interactive Mode

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-14 | TUI interactive mode supports multi-turn conversations (via deepagents-code Textual) | `olav` (no arguments) | [First Query](../getting-started/first-query.md) | ✅ | v0.17.0 |
| C-L2-15 | Resume a previous session | `olav --session <id>` | [First Query](../getting-started/first-query.md) | ✅ | v0.10.0 |
| C-L2-24 | /trace-review analyzes failure patterns and writes to memory | `/trace-review` | [Self-Improving Loop](../concepts/self-improving-loop.md) | ⚠️ | v0.10.0 — TUI migration pending |
| C-L2-26 | Skip tool call confirmation | `olav --auto-approve` | [Using Agents](../guides/using-agents.md) | ✅ | v0.10.0 |

### Knowledge Base and Memory

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-21 | Knowledge base indexes documents and supports semantic search | `olav "index document..."` | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.17.1 |

### Users and Authentication

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-23 | Admin creates/manages users and tokens | `olav admin "add-user..."` | [Users and Roles](users-and-roles.md) | ✅ | v0.10.0 |
| C-L2-28 | Supports multiple authentication modes | `auth.mode` config | [Security Model](../concepts/security-model.md) | ✅ | v0.10.0 |

### Core Agent Tools

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-31 | Core Agent executes Python code | `olav --agent core "run python:..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-32 | Core Agent executes SQL queries | `olav --agent core "what tables..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-33 | Core Agent web search | `olav --agent core "search:..."` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-34 | Core Agent executes shell commands | `olav --agent core "run: df -h"` | [Core Agent](../guides/core-agent.md) | ✅ | v0.10.0 |
| C-L2-35 | Core agent workspace health check | `olav "health check"` | [Core Agent](../guides/core-agent.md) | ✅ | v0.17.1 |

### Export and Integration

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-10 | Export Claude-compatible plugin | `olav export claude-plugin` | [CLI Commands](cli.md) | ✅ | v0.10.0 |
| C-L2-11 | Auto-truncate large API responses | Python SDK | [CLI Commands](cli.md) | ✅ | v0.10.0 |

### Configuration

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L2-38 | Assign different LLM models to different Agents | `agent_overrides` in api.json | [Configuration](configuration.md) | ✅ | v0.10.0 |
| C-L2-39 | Environment variables override config file | `OLAV_LLM_*` | [Configuration](configuration.md) | ✅ | v0.10.0 |
| C-L2-40 | `olav log show <run-id>` displays full event sequence | `olav log show <id>` | [Audit and Logs](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-41 | Service management: status / start --all / stop --all | `olav service status/start --all/stop --all` | [Background Services](../guides/services.md) | ✅ | v0.10.0 |
| C-L2-42 | Web API introspection: /openapi.json, /docs, /threads/search | `GET /openapi.json` `GET /docs` | [HTTP API](http-api.md) | ✅ | v0.10.0 |
| C-L2-43 | `olav workspace status` shows Agent status | `olav workspace status` | [Manage Workspaces](../guides/manage-workspace.md) | ✅ | v0.10.0 |
| C-L2-44 | Admin rotate-token and add-user --expires | `olav admin "rotate-token <n>"` | [Users and Roles](users-and-roles.md) | ✅ | v0.10.0 |
| C-L2-45 | Audit database queryable directly via DuckDB | `duckdb .olav/databases/audit.duckdb` | [Audit and Logs](../guides/audit-and-logs.md) | ✅ | v0.10.0 |
| C-L2-46 | `/model <name>` switches LLM model within a session | TUI: `/model gpt-4o-mini` | [CLI Reference](cli.md) | ✅ | v0.10.0 |
| C-L2-47 | `@file.txt` file content injection | TUI: `@/path/to/file.txt` | [CLI Reference](cli.md) | ✅ | v0.10.0 |
| C-L2-48 | `!command` shell passthrough | TUI: `!echo hello` | [CLI Reference](cli.md) | ✅ | v0.10.0 |
| C-L2-49 | `--sandbox modal/daytona/runloop` remote sandbox flag | `olav --sandbox modal version` | [Agent Harness](../concepts/agent-harness.md) | ✅ | v0.10.0 |
| C-L2-50 | `olav registry list/refresh/status` | `olav registry list` | [Connect a Service](../guides/connect-a-service.md) | ✅ | v0.10.0 |
| C-L2-51 | `olav config evolve --list/--approve` | `olav config evolve --list` | [CLI Reference](cli.md) | ✅ | v0.10.0 |

---

## NetOps Extension Claims

### Quick Start

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-01 | `/netops_init --dry-run` passes environment checks + validates Nornir inventory | `/netops_init --dry-run` | [Quick Start](../netops/quick-start/index.md) | ✅ | v0.17.0 |
| C-NE-02 | `olav list` includes all 9 agents (core + 8 netops) after single `skill install` | `olav list` | [Quick Start](../netops/quick-start/index.md) | ✅ | v0.17.0 |
| C-NE-03 | `/netops_init` populates `netops.parsed_outputs` with platform-aware commands | `/netops_init` | [Quick Start](../netops/quick-start/index.md) | ✅ | v0.17.0 |
| C-NE-04 | `/netops_init` generates topology links + populates `netops.devices` | `/netops_init` | [Quick Start](../netops/quick-start/index.md) | ✅ | v0.17.0 |

### Concepts

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-05 | Snapshots produce unique `snapshot_id` shared across tables | `execute_sql` | [Concepts](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-06 | Custom TextFSM templates take priority over NTC built-ins | manual test | [Concepts](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-07 | `CommandRegistry.reload()` picks up new templates without restart | Python API | [Concepts](../netops/concepts/index.md) | ✅ | v0.10.0 |
| C-NE-08 | `netops.devices` populated by Device ETL from parsed_outputs (hostname, ip, platform, vendor, model, os_version) | `SELECT * FROM netops.devices` | [Database Schema](../netops/concepts/database-schema.md) | ✅ | v0.17.0 |
| C-NE-09 | BGP data queried directly from `netops.parsed_outputs` (no auto-generated views) | `SELECT parsed_data FROM netops.parsed_outputs WHERE command='show ip bgp summary'` | [Database Schema](../netops/concepts/database-schema.md) | ✅ | v0.17.0 |
| C-NE-10 | `netops.topology_links` includes `discovery_protocol` | `SELECT discovery_protocol FROM netops.topology_links` | [Database Schema](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-11 | `netops.raw_output_store` preserves latest raw CLI output | `SELECT * FROM netops.raw_output_store LIMIT 5` | [Database Schema](../netops/concepts/database-schema.md) | ✅ | v0.10.0 |
| C-NE-12 | `nornir/hosts.yaml` defines device inventory | manual test | [Configuration](../netops/concepts/configuration.md) | ✅ | v0.10.0 |
| C-NE-13 | `blacklisted_commands.yaml` supports regex; invalid patterns skipped | manual test | [Configuration](../netops/concepts/configuration.md) | ✅ | v0.10.0 |
| C-NE-14 | `cron_schedules.yaml` customizes trace_learner schedule | `crontab -l` | [Configuration](../netops/concepts/configuration.md) | ✅ | v0.10.0 |

### Core Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-15 | Natural language query returns results from snapshot data | `olav "R1 BGP neighbors?"` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-16 | Blacklisted commands rejected by `execute_cli` | manual test | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-17 | `/learn_cmd` executes command and generates TextFSM template | `/learn_cmd "show version" -d R1` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |
| C-NE-18 | `take_snapshot` triggers collection + auto-ingest into DB | `take_snapshot` | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.17.0 |
| C-NE-19 | Core Agent suggests Ops Agent for complex analysis | complex query | [Core Agent](../netops/core-agent/index.md) | ✅ | v0.10.0 |

### Ops Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-20 | Ops routes analysis queries to Analysis subagent | `olav --agent netops "analyze..."` | [Ops Agent](../netops/netops-agent/index.md) | ✅ | v0.10.0 |
| C-NE-21 | Ops routes device check queries to Probe subagent | `olav --agent netops "check..."` | [Ops Agent](../netops/netops-agent/index.md) | ✅ | v0.10.0 |
| C-NE-22 | What-If simulation recalculates connectivity after link failure | `olav --agent netops "simulate..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-23 | analyzer is read-only (`execute_sql` / `generate_change_plan`), no live device access | SKILL.md inspection | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-24 | `execute_cli_parallel` validates whitelist before execution | manual test | [Collector](../netops/netops-agent/collector.md) | ✅ | v0.10.0 |
| C-NE-25 | Device names restricted to `[a-zA-Z0-9_\-.]` | manual test | [Collector](../netops/netops-agent/collector.md) | ✅ | v0.10.0 |
| C-NE-26 | `diff_topology_drift` detects link state changes | `olav --agent netops "what changed..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |
| C-NE-27 | `diff_sql_state` compares any table between snapshots | `olav --agent netops "compare..."` | [Analyzer](../netops/netops-agent/analyzer.md) | ✅ | v0.10.0 |

### Lab Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-28 | `deploy_lab` builds ContainerLab topology from snapshot data | `olav --agent netops "deploy lab"` | Lab Agent (enterprise) | 🔶 | v0.17.1 |
| C-NE-29 | Lab agent pushes SRL config and verifies BGP/OSPF convergence | `olav --agent netops "push config and verify"` | Lab Agent (enterprise) | 🔶 | v0.17.1 |
| C-NE-30 | `deploy_lab` auto-handles SRL topology.yml quirks and veth injection | internal tool | Lab Agent (enterprise) | 🔶 | v0.10.0 |
| C-NE-31 | Lab agent has no `execute_cli` — no live device access | code inspection | Lab Agent (enterprise) | ✅ | v0.10.0 |

### Audit Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-32 | Designer guides profile creation interactively | `olav --agent audit "create profile..."` | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.17.1 |
| C-NE-33 | Auditor executes profile and outputs Markdown report | `olav --agent audit "run..."` | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.10.0 |
| C-NE-34 | Designer validates table/column existence before SQL | internal validation | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.10.0 |
| C-NE-35 | `analyze_thresholds` provides P50/P90/P95 recommendations | internal tool | [Audit Agent](../netops/audit-agent/index.md) | ✅ | v0.10.0 |

### Infra Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-36 | `olav registry register` registers service and generates API reference | `olav registry register <url>` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.12.0 |
| C-NE-37 | Ops agent queries registered services via `api_request` | `olav --agent netops "query service..."` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.17.1 |
| C-NE-38 | `api_request` returns clear error for unregistered services | `api_request("nonexistent", ...)` | [Infra Agent](../netops/infra-agent/index.md) | ✅ | v0.12.0 |
| C-NE-39 | Write operations require `--enable-api-write` + dry-run + mandatory approval | `--enable-api-write` | [Infra Agent](../netops/infra-agent/index.md) | 🔶 | v0.12.0 |
| C-NE-40 | `network_isolation=True` sandbox hard-blocks HTTP write operations | sandbox test | [Infra Agent](../netops/infra-agent/index.md) | ✅ | v0.12.0 |

### DevOps Agent

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-NE-41 | Ops agent queries `netops.devices` before generating scripts | `olav --agent netops "write script..."` | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.17.1 |
| C-NE-42 | Generated scripts include `--dry-run` + error handling + summary | script inspection | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |
| C-NE-43 | Scripts use real device names/IPs from database, not placeholders | script inspection | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |
| C-NE-44 | Scripts exported to `exports/scripts/` as executable files | file check | [DevOps Agent](../netops/devops-agent/index.md) | ✅ | v0.13.0 |

---

## Level 3 — Knowledge Store (UKS)

### Top-Level Capability

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L3-UKS | Unified Knowledge Store: single LanceDB table for documents, memories, and graph nodes | `olav kb status` | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Schema & Migration

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-01 | Memory table has `origin`, `confidence`, `tags` columns | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-02 | `add_memory()` uses correct defaults when new params omitted | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-03 | Old-schema tables auto-migrate on first access | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-09 | `migrate_memory_table()` infers `origin`/`confidence` from `metadata.source` | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-25 | `migrate_kb_chunks()` moves `kb_chunks` rows to the unified memory table | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-26 | `from olav.core.knowledge` raises `ImportError` after deletion | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-27 | `search_knowledge_lancedb` tool file is deleted | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Capture & Decay

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-04 | Extracted items contain a `tags` array | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-05 | `capture` writes `origin="agent"`, `confidence=importance` from LLM response | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-06 | `document` origin does NOT decay | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-07 | `agent` origin decays normally | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-08 | High-frequency recall (`access_count >= 5`) halves the decay rate | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Graph

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-10 | `materialize_graph()` returns a dict with `nodes` and `edges` keys | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-11 | Vector similarity creates implicit `similar` edges with a `weight` field | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-12 | Shared tags create explicit edges through entity nodes | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-15 | `graspologic` unavailable → `cluster_knowledge()` returns `None` | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Export & Sync

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-13 | `export_obsidian()` generates `.md` files with YAML frontmatter | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-14 | `export_visjs()` generates an HTML file with vis.js content | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-17 | `sync_from_files()` UPDATEs entries that have changed on disk | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-18 | `sync_from_files()` INSERTs entries for new files | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-19 | `sync_from_files()` DELETEs entries when the file is removed | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-20 | `sync_from_files(dry_run=True)` does NOT modify data | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### Import & Search

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-21 | Importing a `.md` file creates a memory entry with `origin='document'`, `confidence=1.0` | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-22 | `hybrid_search()` returns results from both `agent` and `document` origins | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-28 | After migrating `kb_chunks`, `search_by_vector()` returns entries with `origin='document'` | unit test | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

### CLI Commands

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-KB-16 | `olav kb export` command is available and exits successfully | `olav kb export` | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-23 | `olav kb status` outputs knowledge store statistics | `olav kb status` | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |
| C-KB-24 | `olav kb graph` generates an HTML file | `olav kb graph` | [Knowledge Base](../guides/knowledge-base.md) | ✅ | v0.13.0 |

---

## Level 4 — Multi-User & Security

| ID | Claim | CLI Command | Doc Page | Status | Version |
|----|-------|-------------|----------|--------|---------|
| C-L4-01 | `olav init` auto-creates admin user + writes token to `~/.olav/token` + sets `auth.mode=token` | `olav init` | [Users and Roles](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-02 | `olav admin add-user` validates that the Linux user exists before creating a token | `olav admin add-user <name>` | [Users and Roles](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-03 | `olav sessions` lists current-user sessions from the audit database | `olav sessions` | [Users and Roles](users-and-roles.md) | ✅ | v0.14.0 |
| C-L4-04 | `olav --resume <thread_id>` resumes a session across interfaces | `olav --resume <id>` | [Users and Roles](users-and-roles.md) | ⬜ | v0.14.0 |
| C-L4-05 | Thread ownership: non-owner requests return HTTP 403; admin bypasses | `POST /runs/stream` | [Security Model](../concepts/security-model.md) | ✅ | v0.14.0 |
| C-L4-06 | `search_logs` tool queries syslog Parquet files via DuckDB | agent tool | [Background Services](../guides/services.md) | ✅ | v0.14.0 |
| C-L4-07 | API server emits startup warning when `auth.mode=none` | `olav service web start` | [Security Model](../concepts/security-model.md) | ✅ | v0.14.0 |

---

## v0.17.0 New Capabilities

| ID | Claim | CLI Command | Status | Version |
|----|-------|-------------|--------|---------|
| C-L2-52 | `olav init` auto-downloads embedding model silently | `olav init` | ✅ | v0.17.0 |
| C-L2-53 | `olav agent install` supports multi-workspace via `workspaces:` list | `olav agent install <path>` | ✅ | v0.17.0 |
| C-L2-54 | `olav agent install` auto-runs `pip install -e` when `pyproject.toml` present | `olav agent install <path>` | ✅ | v0.17.0 |
| C-L2-55 | `POST /reload` hot-reloads agent singleton after skill install | `POST /reload` | ✅ | v0.17.0 |
| C-L2-56 | `GET /agents` returns dynamic agent list from workspace registry | `GET /agents` | ✅ | v0.17.0 |
| C-L2-57 | `GET /memory/graph` serves knowledge graph vis.js page | `GET /memory/graph` | ✅ | v0.17.0 |
| C-L2-58 | CIDR allowlist middleware for web API | api.json `security.allowed_cidrs` | ✅ | v0.17.0 |
| C-L2-59 | Semantic cache with auto-invalidation on data ingest | `olav "query"` (2nd time) | ✅ | v0.17.0 |
| C-L2-60 | Embedding dimension auto-detected from model probe | `detect_embedding_dim()` | ✅ | v0.17.0 |
| C-L2-61 | Single-query mode uses native langgraph streaming (no deepagents-code dependency) | `olav "query"` | ✅ | v0.17.0 |
| C-NE-45 | Platform-aware SSH collection (Junos + IOS command lists) | `/netops_init` | ✅ | v0.17.0 |
| C-NE-46 | Device ETL extracts IP from `show ip interface brief` / `show interfaces terse` | `/netops_init` | ✅ | v0.17.0 |
| C-NE-47 | Dry-run validates full Nornir inventory (catches group reference mismatches) | `/netops_init --dry-run` | ✅ | v0.17.0 |
| C-L2-62 | 3-agent architecture: core, ops, audit (no standalone shortcuts) | `olav list` → 3 agents | ✅ | v0.17.1 |
| C-L2-63 | `olav.tools` package — platform-agnostic SQL/API/memory implementations | `from olav.tools.sql import query_duckdb` | ✅ | v0.17.1 |
| C-NE-48 | `olav_netops.tools` package — platform-agnostic SSH/TextFSM implementations | `from olav_netops.tools.ssh import execute_on_device` | ✅ | v0.17.1 |
| C-L2-64 | Semantic router auto-rebuilds index when agent count changes | `olav refresh` after skill install | ✅ | v0.17.1 |

---

## Statistics

| Status | Count |
|--------|-------|
| ✅ Verified (wheel) | 129 |
| ⬜ Pending | 1 |
| ⚠️ Narrowed | 1 |
| 🔶 Env-Blocked | 6 |
| **Total** | **137** |

---

## Verification Process

```bash
# For each ⬜ Claim:
# 1. Run the CLI command
olav <command for the claim>

# 2. Record the result in dev_docs/23. CLI_VERIFICATION_LOG.md
# 3. Pass → change status to ✅, add version number
# 4. Fail → fix code, narrow the claim, or remove the Claim

# For 🔶 Claims: set up the environment first (LLM API Key / running external service)
```
