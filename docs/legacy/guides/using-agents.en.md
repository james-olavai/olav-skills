# Using Agents

At the core of OLAV are multiple AI Agents working together — each Agent focuses on a different domain. You can let OLAV choose automatically, or specify one manually.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-02 | `olav list` lists all available Agents | ✅ v0.10.0 |
    | C-L2-03 | `olav workspace list/use` switches the active workspace | ✅ v0.10.0 |
    | C-L2-26 | `olav --auto-approve` skips tool call confirmation | ✅ v0.10.0 |

---

## View Available Agents

```bash
olav list
```

Example output:
```
Available Agents:

  • config
    Config & System Agent — service registration, data import, workspace health checks
    Location: .olav/workspace/config/

  • core
    Core OLAV platform agent — code execution, SQL queries, platform operations
    Location: .olav/workspace/core/

  • core  (active)
    Core Agent — fast data queries, CLI execution, knowledge base search
    Location: .olav/workspace/core/
```

Compact view:

```bash
olav workspace list
```

```
* core                   -       (user)    ← * indicates currently active
  config                -       (user)
  core                  -       (user)
  venv-test             v1.0.0  (managed) ← managed = installed via olav agent install
```

---

## Built-in Agents at a Glance

| Agent | Role | Use Cases |
|-------|------|-----------|
| `core` *(default)* | Fast query assistant | "What tables are in the database?" "Show recent errors" — one-step queries |
| `config` | System administrator | Register external services, generate skills, workspace health checks |
| `core` | Full-stack engineer | Execute Python/SQL/Shell, web search, complex data analysis |

---

## Automatic Routing (Default Behavior)

When `--agent` is not specified, OLAV automatically selects the most suitable Agent based on the content of your question:

```bash
olav "list all agents"               # → auto-routes to Core Agent
olav "what tables are in the database?"  # → auto-routes to Core Agent
olav "run a health check"            # → auto-routes to Core Agent
```

Routing is based on the `route_keywords` defined in each Agent's `MANIFEST.yaml`. The default active `core` Agent is ideal for fast lookups.

!!! note "Platform commands bypass routing"
    Built-in commands like `olav version`, `olav list`, and `olav log list` execute directly and do not go through Agent routing.

---

## Manually Specifying an Agent

When you know exactly which Agent you want to use:

```bash
olav --agent services "list registered services"
olav --agent core "what tables are in the database?"
olav --agent core "search: deployment procedure"
```

Shorthand:

```bash
olav -a config "list registered services"
```

---

## Interactive Mode

Launch an interactive terminal with multi-turn conversation support, where the Agent maintains context:

```bash
olav               # start with the default Agent
olav --agent admin # start with a specific Agent
```

In interactive mode, the Agent remembers previous conversation content, so you can naturally ask follow-up questions:

```
> What tables are in the database?
> Which table has the most rows?
> Export those statistics to CSV
```

---

## Switching the Active Agent

The active Agent is the one used by default when you don't specify `--agent`:

```bash
olav workspace list           # view all Agents, * marks the currently active one
olav workspace use config     # switch to the config Agent
olav workspace use core      # switch back to the Core Agent
```

---

## Switching Agents in the Web UI (v0.21.0+)

!!! info "Enterprise feature (olav-ent)"
    The Web UI and its HTTP API (`/agents`, `/threads`, `/runs/stream`) ship in the enterprise extension `olav-ent` (`pip install olav-ent`). In open-source OLAV, switch agents from the CLI/TUI with `--agent` instead (see the sections above).

The Web UI's left sidebar footer has an agent dropdown listing every
top-level agent returned by the `/agents` endpoint:

```
┌────── Sidebar ──────┐
│  + New Chat        │
│  Thread list…       │
│                     │
│  Memory Graph ↗     │
│  ● API healthy      │
│  [ agent-select ▼ ] │   ← click to switch
│      core (default) │
│      audit          │
│      command_learner│
│      netops         │
│      topology       │
└─────────────────────┘
```

### Semantics

1. **`core` is always first** — v0.21.0-rc4 pins the platform agent at
   index 0 server-side, so the browser's auto-select + the `(default)`
   label always land on `core`, regardless of how many extension
   agents are installed.
2. **Each thread is bound to one agent** — the dropdown value at the
   moment a thread is created is stored with the thread; switching
   back to an older thread restores its original agent in the dropdown.
   Every follow-up message in the same thread therefore goes to the
   same agent — no accidental cross-agent bleed.
3. **State survives a page refresh** — `currentThreadId`, the thread
   list, and each thread's `agentId` are persisted to
   `localStorage` under the key `olav.webui.state.v1`.  Hit F5 and the
   dropdown restores the last selection.

### Two typical workflows

**A — start a new conversation with a specific agent**

1. Pick the agent from the dropdown (e.g. `netops`)
2. Click **+ New Chat**
3. Chat as usual — the whole thread runs against `netops`

**B — switch agent mid-thread**

1. Change the dropdown (e.g. `core` → `audit`)
2. The next message goes to `audit`, and the thread's binding is
   **permanently updated** to `audit`
3. If you leave and return, the dropdown auto-restores to `audit`

### Under the hood

The browser sends the current dropdown value as `assistant_id` in every
`POST /threads/<id>/runs/stream` request; the server's `stream_run()`
resolves it via `get_agent(assistant_id)`, which hits a per-id cache
(not the pre-rc2 singleton).  `OlavRunContext(agent_id=...)` is then
attached to the graph invocation, and audit-event `agent_id` records
the dropdown's selection verbatim.

Verify with curl:

```bash
TOKEN=$(cat .olav/databases/.auth_token)
TID=$(curl -sS -X POST http://localhost:2280/threads \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["thread_id"])')

# Explicitly route to netops:
curl -sS -X POST "http://localhost:2280/threads/$TID/runs/stream" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"assistant_id":"netops","input":{"messages":[{"role":"user","content":"list devices"}]}}'

# Confirm the netops graph actually handled it:
python3 -c "import duckdb; c=duckdb.connect('.olav/databases/audit.duckdb', read_only=True); \
print(c.execute(\"SELECT agent_id, event_type FROM audit_events \
WHERE run_id=(SELECT run_id FROM audit_events WHERE event_type='user_input_received' \
ORDER BY timestamp DESC LIMIT 1)\").fetchall())"
# → [('netops', 'user_input_received'), ('netops', 'assistant_output_final')]
```

---

## Skipping Tool Confirmation

By default, the Agent asks for your confirmation before calling a tool. If you trust the operation (e.g., read-only queries), you can skip it:

```bash
olav --auto-approve "run health check"
```

!!! warning "Use with caution"
    `--auto-approve` automatically approves all tool calls, including write operations. It is recommended to use this only when you are certain the query is read-only.
