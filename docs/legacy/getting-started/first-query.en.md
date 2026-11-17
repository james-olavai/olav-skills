# Run Your First Query

Once installed, there are three ways to interact with OLAV. Let's start with the simplest.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-02 | `olav list` lists all available Agents | ✅ v0.10.0 |
    | C-L2-14 | `olav` with no arguments launches TUI interactive mode | ✅ v0.10.0 |
    | C-L2-15 | `olav --session <id>` resumes a session | ✅ v0.10.0 |
    | C-L2-16 | `olav service web start` launches the Web UI | ✅ v0.10.0 |
    | C-L2-08 | `olav log list` shows operation history | ✅ v0.10.0 |

---

## CLI Mode: One-Liner Questions

The fastest way is to type a natural-language question directly on the command line:

```bash
olav "list all agents"
olav "what tables are in the database?"
olav "run a health check"
```

OLAV automatically routes your question to the most suitable Agent. The default `quick` Agent is ideal for fast lookups.

If you want to query information about **the OLAV platform itself**, use the built-in commands:

```bash
olav version                          # Check platform version
olav list                             # List all available Agents
olav --agent services "List registered services"   # View connected external services
```

You can also use `--agent` (or `-a`) to target a specific Agent directly:

```bash
olav --agent core "run df -h"                              # Execute a system command
olav --agent core "run this python: import sys; print(sys.version)"  # Execute Python code
```

---

## Interactive Mode: Multi-Turn Conversations

Run `olav` without arguments to enter the interactive terminal (TUI), which supports multi-turn conversations:

```bash
olav                      # Start with the default Agent
olav --agent admin       # Start with a specific Agent
olav --no-splash          # Skip the startup banner
```

In interactive mode, you can ask questions naturally as in a chat, and the Agent maintains context:

```
> How many Agents are available?
> What did I do in the last hour?
> Show recent errors
```

### The Welcome Screen Knows Your State

The TUI's first screen is not a random tip — it reflects what is actually
true about your installation right now:

- **Real findings**: *"⚠ Embedding backend unavailable — memory/recall
  features will be limited. Run `olav doctor` for details."* or *"netops
  has no device data yet — import a snapshot to get your first answers."*
- **Session continuity**: returning users see *"Welcome back — last time
  (2h ago): 'why is R1's BGP flapping?'"* so you can pick up where you
  left off.

When everything is healthy and there's nothing to say, you get a regular
usage tip instead.

### Slash Commands in Interactive Mode

| Command | Function |
|---------|----------|
| `/help` | Show all available commands |
| `/doctor` | Run the zero-LLM health check (scaffolding + LLM + embedding) without leaving the TUI |
| `/workspace <name>` | Switch to another agent workspace (e.g. `/workspace admin`) |
| `/clear` | Reset the current conversation and clear the screen |
| `/model <name>` | Temporarily switch the LLM model within the session (does not modify config files) |
| `/trace-review` | Manual one-shot review of recent failures (mostly superseded by always-on L1 capture — see [Self-Improving Loop](../concepts/self-improving-loop.md)) |
| `/quit` `/exit` `/q` | Exit interactive mode |

### Special Syntax in Interactive Mode

| Syntax | Function | Example |
|--------|----------|---------|
| `@file.txt` | Inject file contents into your prompt | `@config.yaml What's wrong with this config?` |
| `!command` | Execute a shell command directly (bypassing the Agent) | `!ls -la exports/` |

!!! tip "Self-improvement is automatic"
    OLAV captures every successful tool call into LanceDB memory as you use it (L1), and a curator periodically distils repeated patterns into reusable directives (L2). Both surface back into prompts via AutoRecall — so the system gets smarter from your usage without any manual review step. See [Self-Improving Loop](../concepts/self-improving-loop.md) for details and how to extend it with team-specific directives.

### Resuming a Previous Session

```bash
olav --session <session-id>
```

Sessions are stored in `~/.olav/sessions/` and expire after 24 hours by default.

---

## Web UI: Browser Access

!!! info "Enterprise feature (olav-ent)"
    The web UI and HTTP API ship in the enterprise extension `olav-ent` (`pip install olav-ent`). They are not part of the open-source install — use the CLI or interactive TUI above instead.

If you prefer a graphical interface or need to share with your team (with `olav-ent` installed):

```bash
olav service web start
```

Then open `http://localhost:2280` in your browser to use the same Agent capabilities as the CLI, with real-time streaming responses.

See [Background Services →](../guides/services.md) for details.

---

## Getting to Know Your Agents

Run the following command to see which Agents are available in your current workspace:

```bash
olav list
```

```
Available Agents:

  • config
    Config & System Agent — Service registration, data import, workspace health checks
    Location: .olav/workspace/config/

  • core
    Core OLAV platform agent — Code execution, SQL queries, shell commands
    Location: .olav/workspace/core/

  • quick  (active)
    Core Agent — Fast data queries, CLI execution, knowledge base search
    Location: .olav/workspace/quick/
```

Each Agent specializes in different tasks:

| Agent | What It Does Best | Use Cases |
|-------|-------------------|-----------|
| `core` *(default)* | Fast queries, single-step answers | "What tables are in the database?" "Show recent errors" |
| `config` | Manage service registration, workspace health checks | Connect a new API, diagnose workspace issues |
| `core` | Execute Python/SQL/Shell, web search | Data analysis, automation scripts, complex queries |

---

## Viewing Operation History

OLAV automatically logs every operation. View recent activity:

```bash
olav log list
```

```
Recent Audit Runs (last 24h):

  [2026-04-03 15:35:52] 7f2693b8  completed     agent=core
  [2026-04-03 15:35:25] 2e31144b  completed     agent=config
  [2026-04-03 15:34:20] 4e314755  completed     agent=quick
```

See [Audit and Logs →](../guides/audit-and-logs.md) for details.

**Next:** [How It Works →](how-it-works.md)
