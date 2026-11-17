# Self-Configuration & Recovery

OLAV is designed so that **you never have to hand-edit a config file to
change something, and never lose a working setup to a bad change**. This
page covers the conversational config tools, rollback, undo, and what to
do when something breaks mid-session.

---

## Change LLM / Embedding Config by Talking

Ask the **admin** agent (it routes config requests to its `editor`
sub-agent):

```bash
olav --agent admin "switch my LLM model to deepseek-chat"
olav --agent admin "update my LLM api key to sk-..."
olav --agent admin "switch embedding to api mode with key sk-..."
```

Or from inside the TUI: `/workspace admin`, then ask in plain language.

### Validate-Before-Commit

Every conversational config change is **tested against the live provider
before anything is written**:

- A working candidate → saved, active immediately (no restart needed),
  and the previous config is snapshotted first.
- A broken candidate (wrong key, unknown model, unreachable endpoint) →
  **rejected with the provider's real error**, and your current working
  config is untouched.

```
> update my LLM api key to sk-wrong-key
Rejected — candidate config failed to connect: Error code: 401 —
Authentication Fails... Current config unchanged.
```

This is why you should prefer the conversational path over editing
`api.json` by hand — a manual edit has no validation and can leave you
with a config that no longer starts.

### Rollback

Every applied change snapshots the previous config. One sentence undoes
the most recent change:

```bash
olav --agent admin "rollback my LLM config"
```

---

## Undo Agent Write-Actions

Beyond config, agent actions that write state — **workspace file writes**
and **cron schedule changes** — are journaled automatically. To revert
the most recent one:

```bash
olav --agent admin "undo my last change"
```

The agent can preview what would be undone before doing it. Undo is
single-step, most-recent-first: ask again to undo the action before that.

| Action | Undo behavior |
|--------|---------------|
| File overwritten | Previous content restored |
| File created | File removed |
| Cron job added | Job removed |
| Cron job schedule updated | Previous schedule restored |
| Cron job removed | Job re-added from the stored spec |

!!! note "Scope"
    Config changes use their own dedicated `rollback my LLM config` path
    (validated snapshots). Service deployments are not currently
    undoable.

---

## When Something Breaks Mid-Session

An LLM failure during use (expired key, exhausted quota, retired model)
does not strand you with a raw stack trace. The error is followed by the
next step:

```
Error: Error code: 401 - {'error': {'message': 'Authentication Fails...'}}
The LLM provider rejected the API key (invalid or expired) — run
`olav doctor` to diagnose. If this started after a config change, undo
it: olav --agent admin "rollback my LLM config"
```

### The Doctor

```bash
olav doctor           # from the shell
olav doctor --json    # machine-readable
/doctor               # from inside the TUI
```

Deterministic, zero-LLM probes — it works even when the LLM config is
exactly what's broken:

```
✓ scaffolding: .olav/ deployed
✓ llm: connected
⚠ embedding: sentence-transformers unavailable; cannot create local embeddings
    fix: check embedding.mode / embedding.api.* in .olav/config/api.json

overall: needs attention
```

Every failing check carries a `fix:` line — the goal is to tell you what
to **do**, not just what is broken.
