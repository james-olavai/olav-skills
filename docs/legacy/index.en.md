# OLAV Platform 🐺

> **O**nline **L**ogical **A**nalytical **V**ertex for Agentic Operations
>
> Control your infrastructure with natural language.

**v0.26.0** — [Get Started →](getting-started/installation.md)

!!! abstract "Feature Claims · [C-L1-01 ✅ v0.10.0]"
    This is a Level 1 platform claim. See [Claim Registry →](reference/claim-registry.md) for verification status.

---

## What is OLAV?

OLAV is an AI-native operations platform. Describe what you need in plain language, and OLAV automatically selects the right AI Agent to complete the task — querying data, executing commands, analyzing logs, generating reports — no SQL or CLI flags to memorize.

```bash
olav "list all agents"
olav "what tables are available?"
olav --agent core "run a health check"
olav log list   # view all operation history
```

---

## Core Capabilities

| Capability | Description |
|-----------|-------------|
| **Natural Language Queries** | Ask in everyday language; OLAV translates to SQL / API calls and returns results |
| **Multi-Agent Collaboration** | Specialized Agents for different domains (quick queries, config management, code execution), auto-routed to the best fit |
| **Connect Any API** | Register any OpenAPI service with one command, query it in natural language immediately |
| **Full Audit Trail** | Every operation automatically recorded; view with `olav log list/errors/show` |
| **Self-Improving** | Learns from past errors; Agents get better over time |
| **Team Collaboration** *(Enterprise)* | Multi-user token / LDAP authentication and shared workspaces ship in [olav-ent](getting-started/installation.md). Open-source OLAV is single-user (OS identity = admin); the RBAC engine itself stays open-source |
| **Multi-Layer Cache** | LLM SQLiteCache + SemanticCache; 0 tokens and <1ms on cache hit (measured 2000x+ speedup) |
| **Agent Harness** | Three-layer code sandbox + HITL dangerous command approval + prompt injection scanning + full audit trail |

---

## Three Ways to Use

=== "CLI"
    ```bash
    olav "search knowledge for deployment procedure"
    ```
    Best for scripting and quick one-off queries.

=== "Interactive Terminal (TUI)"
    ```bash
    olav
    ```
    Multi-turn conversations for exploration and analysis.

=== "Web UI · Enterprise"
    ```bash
    pip install olav-ent          # Web UI + HTTP API ship in the enterprise extension
    olav service web start
    # Open http://localhost:2280
    ```
    Browser-based team interface with real-time streaming — **requires [olav-ent](getting-started/installation.md)**. In open-source OLAV, `olav service` provides the syslog receiver only.

---

## Quick Navigation

<div class="grid cards" markdown>

- :rocket: **[Getting Started](getting-started/installation.md)** — Install and run your first query in 5 minutes
- :electric_plug: **[Guides](guides/connect-a-service.md)** — Connect services, build skills, manage workspaces
- :bulb: **[Concepts](concepts/agents-and-skills.md)** — Understand Agents, Skills, and Workspaces
- :books: **[Reference](reference/cli.md)** — CLI commands, configuration, HTTP API

</div>
