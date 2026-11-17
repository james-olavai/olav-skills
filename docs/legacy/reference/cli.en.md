# CLI Command Reference

All OLAV features are accessible via the command line.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-01 | `olav version` | ✅ v0.10.0 |
    | C-L2-02 | `olav list` | ✅ v0.10.0 |
    | C-L2-10 | `olav export claude-plugin` | ✅ v0.10.0 |
    | C-L2-13 | `olav init` | ✅ v0.10.0 |
    | C-L2-27 | `olav reset --agent` | ✅ v0.10.0 |
    | C-L2-14 | TUI interactive mode | ✅ v0.10.0 |

---

## Basic Usage

```
olav [options] [command] [query]
```

### Global Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--agent NAME` | `-a` | `core` | Specify which Agent to use |
| `--auto-approve` | | off | Automatically approve all tool calls (skip confirmation) |
| `--dangerously-skip-permissions` | | off | Skip permission checks (development only) |
| `--sandbox MODE` | | `none` | Sandbox mode: `none` (local) / `modal` / `daytona` / `runloop` (remote) |
| `--verbose` | `-v` | off | Show verbose output (for debugging) |
| `--no-splash` | | off | Skip startup banner |
| `--session ID` | | | Resume a previous session |
| `--version` | `-V` | | Show version number |

---

## Command List

### Basic Commands

| Command | Description |
|---------|-------------|
| `olav "your question"` | Natural language query (using the default Agent) |
| `olav --agent <name> "..."` | Query with a specific Agent (v0.18.1 top-level: `core` / `audit` / `netops` / `services`) |
| `olav` | Launch the interactive terminal (TUI) |
| `olav version` | Show the platform version |
| `olav list` | List all available Agents |
| `olav init` | Initialize an OLAV project in the current directory |

### Workspace Management

| Command | Description |
|---------|-------------|
| `olav workspace list` | List all workspaces (`*` marks the active one) |
| `olav workspace use <name>` | Switch the active workspace |
| `olav workspace status` | Show workspace status |

### Agent / Skill Management

| Command | Description |
|---------|-------------|
| `olav agent install <path\|URL>` | Install an agent extension package (workspace + tools) from a local directory, wheel, or Git repository |
| `olav agent install <path> --merge-into <ws>` | Append tools to an existing workspace instead of registering a new agent |
| `olav skills list` | List installed skills (skills are capability bundles *inside* an agent) |
| `olav skills info <name>` | View skill details (version, source, install time) |

!!! warning "v0.20.3 command rename"
    `olav skill install <path>` (**singular** `skill`) was removed in v0.20.3;
    **use `olav agent install <path>`** instead.  v0.21.0-rc4 ships a
    backward-compatibility shim: running the old command prints a yellow
    warning and forwards to the new one, so behaviour is identical.  The
    **plural** `olav skills` verb (managing the skill catalogue) is a
    separate command and is unaffected.

    **Migration:**
    ```bash
    # Old (v0.19):
    olav skill install /path/to/olav-netops/

    # New (v0.20.3+):
    olav agent install /path/to/olav-netops/

    # Both do the same: auto `pip install` the package's dependencies,
    # deploy its workspaces, and rebuild the router index
    # (no separate `olav refresh` needed).
    ```

### Service Registry

| Command | Description |
|---------|-------------|
| `olav registry register <name>` | Register an external service (auto-discovers OpenAPI schema and generates tools) |
| `olav registry refresh <name>` | Force re-fetch the schema of a registered service |
| `olav registry list` | List all registered external services |
| `olav registry status <name>` | Check the reachability of a registered service |

### Audit and Logs

| Command | Description |
|---------|-------------|
| `olav log list` | Operations from the last 24 hours |
| `olav log errors [--hours N]` | Show only error events |
| `olav log show <run-id>` | View full details of a specific operation |
| `olav log export sft\|trajectory\|atif` | Export audit data in training formats |

### Background Services

!!! info "Enterprise feature (olav-ent)"
    The **web service** (`olav service web`, HTTP API + browser UI, port 2280) and the **daemon accelerator** (`olav service daemon`) ship in the enterprise extension `olav-ent` (`pip install olav-ent`). In open-source `olav` / `olav-netops`, `olav service` provides the **syslog receiver only** (`olav service logs`).

| Command | Description |
|---------|-------------|
| `olav service web start [--port N]` | **Enterprise (olav-ent)** — Start the Web service (default port 2280) |
| `olav service daemon start` | **Enterprise (olav-ent)** — Start the Daemon accelerator service |
| `olav service logs start [--port N]` | Start the Syslog receiver service (default port 5514) |
| `olav service start --all` | Start all installed background services |
| `olav service stop --all` | Stop all background services |
| `olav service status` | View service status |

### User Management

!!! info "Enterprise feature (olav-ent)"
    Multi-user **token / server-token / LDAP authentication** — including `olav login`, `olav user create` / `olav admin add-user`, and per-user token management — ships in the enterprise extension `olav-ent`. Open-source OLAV defaults to `auth.mode=none`: identity comes from the OS user, who has full admin rights (no RBAC friction). The RBAC engine (roles + permission matrix) itself stays open-source.

OLAV users are aligned to Linux system users — `olav user create alice`
requires `pwd.getpwnam('alice')` to succeed first.  No parallel
identity store.  Per-user **encrypted tokens** (sha256 hash + salt
in `users.duckdb`, plaintext shown once at create) authenticate
against the web API.

| Command | Description |
|---------|-------------|
| `olav user create <name> [--role admin\|user\|readonly]` | Issue token for an existing Linux user.  `--no-verify` to skip the `getpwnam` check (container deployments). |
| `olav user list` | Tabular roster of OLAV-registered users |
| `olav user token <name>` | Rotate + print a fresh token (revokes the old hash) |
| `olav user revoke <name>` | Disable the user without deleting the row |

Legacy verbose form (`olav admin add-user / list-users / rotate-token
/ revoke-token`) is still supported and dispatched to the same
implementation.

### Knowledge Base Management

| Command | Description |
|---------|-------------|
| `olav kb import-guides [<dir>]` | Prime every `*.guide.yaml` under a workspace into LanceDB (idempotent; orphan-prune removes rows whose source YAML was deleted) |
| `olav kb list-guides [--tier vendor\|platform\|team\|user] [--agent <name>]` | Tabular view of active guides + their `source_tier` |
| `olav kb show <intent> [--agent <name>]` | Print the YAML source (use for promotion: copy → git PR → merge → re-prime) |
| `olav kb remove <intent> --reason "<text>" [--agent <name>] [--keep-yaml]` | Tombstone the YAML (`<intent>.guide.yaml.removed`) + delete LanceDB row + drop a `kb_audit/<ts>_remove_<intent>.yaml` row.  `--reason` required (escape: `OLAV_KB_REMOVE_ALLOW_NO_REASON=1`) |
| `olav kb status` | Knowledge store statistics |
| `olav kb export [--dir <path>]` | Export vault to Obsidian markdown |
| `olav kb search <query>` | Search the unified knowledge store |

### Schema Evolution

| Command | Description |
|---------|-------------|
| `olav config evolve --list` | View pending schema evolution proposals |
| `olav config evolve --approve <id>` | Approve and apply a schema evolution |

### Other

| Command | Description |
|---------|-------------|
| `olav export claude-plugin` | Export Claude plugin artifacts |
| `olav reset --agent <name>` | Reset an Agent's conversation history |

### Diagnostic & Discovery Commands

Round 25 (ARCH-12), Round 45 (ARCH-11 explainability), and Round 47
(ARCH-13) landed three user-level shortcuts that talk to the data
surface without running the agent loop.

| Command | Description |
|---------|-------------|
| `olav catalog` | List the data-model topics (Device Inventory / Routing & BGP / Topology / …). |
| `olav catalog <topic>` | Drill into a topic: lists the underlying tables + example queries. |
| `olav catalog describe <table>` | Full schema for a table — columns, recipe mappings, samples. |
| `olav explain "<token>"` | Resolve a citation token from an audit report (e.g. `[src: netops.parsed_outputs#snap_a; device=R1; row=4]`) back to the raw DuckDB row(s). Read-only. |
| `olav diff <snap1> <snap2>` | Cross-snapshot diff aggregator over parsed_outputs / topology_links / raw_output_store / oc_outputs. Flags: `--table`, `--device`, `--max-rows`, `--json`. |

Examples:

```bash
# What can I query about BGP?
olav catalog "Routing & BGP"
olav catalog describe netops.parsed_outputs

# Verify an audit finding's source row
olav explain "[src: netops.parsed_outputs#snap_20260418_0201; device=R1; row=4]"

# What changed between two snapshots?
olav diff snap_20260418_0100 latest --device R1 --max-rows 10
olav diff snap_a snap_b --table parsed_outputs --json
```

All three commands are auth-gated but read-only; they don't invoke an
LLM or modify state.

---

## Interactive Mode (TUI)

Run `olav` without a query argument to launch the interactive terminal:

```bash
olav                      # Use the default Agent (core)
olav --agent audit        # Use a specific Agent (e.g. audit / netops / services)
olav --no-splash          # Skip the startup banner
olav --session <id>       # Resume a previous session
```

### Slash Commands in Interactive Mode

#### Built-in CLI verbs

| Command | Description |
|---------|-------------|
| `/help` | Show the list of available commands |
| `/clear` | Reset the conversation and clear the screen |
| `/model <name>` | Temporarily switch the LLM model within the session (does not modify config files) |
| `/trace-review` | Analyze failures from the last 7 days and automatically extract lessons learned into memory |
| `/quit` `/exit` `/q` | Exit interactive mode |

#### Agent-scoped slash commands

These commands are deterministic Python wrappers shipped by specific
agents.  Invoke as `olav --agent <name> "/<command>"` or via
`/<command>` inside an interactive session running that agent.

| Slash | Agent | Description |
|---|---|---|
| `/netops_init` | `netops` | Full discovery pipeline — SSH all devices, parse, ETL into DuckDB, build per-command auto-views |
| `/export_netbox_csv [--filename X] [--tenant T]` | `netops` | Export `netops.devices` to a NetBox-import-compatible CSV under `exports/`.  See the [connect-a-service guide](../guides/connect-a-service.md#netbox-csv-sync) for the round-trip workflow. |
| `/import_netbox_csv [--csv X] [--report Y] [--write --endpoint URL --token TOK]` | `services` | Validate (default) or push (with `--write`) a NetBox-shaped CSV.  Idempotent 5-step lookup-or-create chain.  Drops a `kb_audit/<ts>_netbox_push.yaml` row per write call. |

### Special Syntax in Interactive Mode

| Syntax | Description |
|--------|-------------|
| `@file.txt` | Inject file contents into the current prompt |
| `!command` | Execute a shell command directly (bypassing the Agent) |

---

## Comparison of the Three Interfaces

| Interface | How to Start | Best For |
|-----------|-------------|----------|
| **CLI** | `olav "query"` | Script integration, one-off queries, automation pipelines |
| **TUI** | `olav` (no arguments) | Multi-turn conversations, exploratory analysis, debugging |
| **Web UI** *(Enterprise · olav-ent)* | `olav service web start` → `http://localhost:2280` | Browser access, team sharing |
