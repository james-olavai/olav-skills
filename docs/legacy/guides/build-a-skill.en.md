# Build a Custom Skill

A Skill is how you extend the capabilities of an OLAV Agent. When the built-in tools don't meet your needs, you can write your own skill — a set of Python functions that teach the Agent new operations.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-04 | `olav agent install <path>` installs a Skill from a local directory | ✅ v0.10.0 |
    | C-L2-25 | `olav agent install <git-url>` installs from a Git repository | ✅ v0.10.0 |
    | C-L2-06 | `olav agent install --merge-into` appends tools to an existing workspace | ✅ ⚠️ v0.10.0 |
    | C-L2-37 | `requires_packages` declaration automatically creates an isolated venv for dependencies | ✅ v0.10.0 |

---

## When Do You Need a Custom Skill?

- You have internal APIs (ticketing system, CMDB, monitoring platform) that you want OLAV to query
- You want to package common operations tasks (bulk configuration, health check scripts)
- You have specific data-processing logic that needs automation

---

## Minimal Skill Structure

A skill requires only two files:

```
my-skill/
├── MANIFEST.yaml    ← Metadata: name, version, route keywords
└── tools.py         ← Tool functions: Python functions the Agent can call
```

### MANIFEST.yaml — Tell OLAV What This Skill Is

```yaml
kind: Agent
name: my-skill
version: "0.1.0"
description: "Tools for querying the ticketing system"
route_keywords:      # When a user's question contains these keywords, auto-route to this skill
  - ticket
  - jira
  - issue
  - 工单
```

`route_keywords` is important — OLAV's semantic router uses these keywords to decide which Agent/Skill to dispatch a question to.

### tools.py — Define the Tools the Agent Can Call

```python
from langchain_core.tools import tool

@tool
def get_open_tickets(team: str) -> list[dict]:
    """Query all open tickets for a given team.

    Args:
        team: Team name, e.g. "platform" or "network"
    """
    import requests
    resp = requests.get(
        "https://jira.internal/api/search",
        params={"jql": f"assignee in membersOf('{team}') AND status != Closed"},
        headers={"Authorization": "Bearer YOUR_TOKEN"}
    )
    return resp.json()["issues"]

@tool
def get_ticket_detail(ticket_id: str) -> dict:
    """Get detailed information for a single ticket.

    Args:
        ticket_id: Ticket ID, e.g. "OPS-1234"
    """
    import requests
    resp = requests.get(
        f"https://jira.internal/api/issue/{ticket_id}",
        headers={"Authorization": "Bearer YOUR_TOKEN"}
    )
    return resp.json()
```

!!! tip "Tool function docstrings are critical"
    The LLM uses the function's docstring to decide when to call a tool and how to pass parameters. Writing clear descriptions and parameter explanations significantly improves Agent accuracy.

---

## Harness Capability Opt-In (v0.20+)

Declare these flags in the `metadata:` block of your `SKILL.md` to enable the corresponding Harness middleware for that agent. No changes to the system prompt are required.

```yaml
---
name: my-skill
description: "..."
tools:
  - execute_skill_script
  - write_todos             # required when enable_todo_list is set
metadata:
  rubric_middleware: true   # output self-eval + auto-retry
  enable_todo_list: true    # multi-step write workflow progress tracking
  type: agent
  version: 1.0.0
  category: my-category
---
```

| Flag | Extra tool needed | Recommended when |
|---|---|---|
| `rubric_middleware: true` | none | Agent produces structured reports or change plans with a fixed template |
| `enable_todo_list: true` | add `write_todos` to `tools:` | Agent runs multi-step write workflows (API writes, bulk changes) |

See [Agent Harness — Quality Assurance Middleware →](../concepts/agent-harness.md#quality-assurance-middleware-v020)

---

## Installing a Skill

### Install from a Local Directory

```bash
olav agent install ./my-skill/
```

Output:
```
installed my-skill v0.1.0 → .olav/workspace/my-skill/
```

Verify:

```bash
olav skill list                  # list all installed skills
olav skills info my-skill        # view skill details
```

### Install from a Git Repository

```bash
olav agent install https://github.com/your-org/my-skill
```

The repository root must contain a `MANIFEST.yaml` or `workspace.yaml`.

### Append to an Existing Skill

If you want to add new tools to an existing Agent instead of creating a new one:

```bash
olav agent install ./extra-tools/ --merge-into my-skill
```

This appends the `tools.py` from `extra-tools/` into the `my-skill` workspace.

---

## Using a Skill

Once installed, just ask in natural language:

```bash
olav "How many open tickets does the platform team have?"
olav "Show details for OPS-1234"
```

OLAV automatically routes questions to your skill based on `route_keywords`.

---

## Declaring Python Dependencies

If your skill needs additional Python packages, declare them in `MANIFEST.yaml`:

```yaml
requires_packages:
  - requests>=2.31.0
  - paramiko
```

When OLAV installs the skill, it automatically creates an isolated virtual environment (`.venv/`), installs the declared packages, and does not affect the main environment.

---

## Uninstalling a Skill

Simply delete the workspace directory:

```bash
rm -rf .olav/workspace/my-skill/
```

---

## Advanced: Skill Development Tips

1. **Use clear function names**: `get_open_tickets` is better than `query_data` — the LLM can more accurately understand when to call it
2. **Write thorough docstrings**: Include a description, parameter explanations, and return value format
3. **Cover common phrasings in route_keywords**: Users might say "ticket", "issue", or "工单" — cover them all
4. **Handle errors gracefully**: Return meaningful error messages instead of letting the Agent face raw 500 errors
5. **Start small**: Begin with 1-2 tool functions, verify they work, then expand

---

## Injecting Tools into Core Agent

By default, a skill's tools are only available when that skill's workspace is active. With `inject_into_core`, you can make tools available to the **Core Agent** regardless of the active workspace — ideal for frequently-used tools that should always be at hand.

### Configuration

In your skill's `workspace.yaml`, add an `inject_into_core` block:

```yaml
name: my-skill
version: 1.0.0
description: My custom skill
route_keywords:
  - ticket
  - issue

inject_into_core:
  tools:
    - execute_cli          # inject this tool into Core Agent
    - search_commands
  description: "My skill tools injected into Core Agent"
```

### How It Works

When `olav agent install my-skill` runs, OLAV:

1. Reads the `inject_into_core` block from `workspace.yaml`
2. Appends the listed tool functions to `.olav/workspace/core/AGENT.md`
3. Updates `.olav/workspace/core/SKILL.md` with an injection manifest

The tools then appear in Core Agent's tool list on the next invocation.

### Removing Injected Tools

When a skill is uninstalled, its injected tools are automatically removed from Core Agent. You can also remove them manually:

```bash
olav skill uninstall my-skill
```

### Best Practices

- Only inject tools that are **universally useful** — avoid cluttering Core Agent with niche domain tools
- Keep injected tool names unique — conflicts with existing tools will be skipped with a warning
- Use `inject_into_core` for tools like `execute_cli`, `search_commands`, or custom data-access helpers
