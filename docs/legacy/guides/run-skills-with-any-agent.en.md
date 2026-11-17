# Run Skills with Any Agent (pi-agent example)

`olav-skills` and `olav-presales-skills` are standalone, lean packages generated from the platform's workspace agents — a set of `SKILL.md` files plus their scripts and a small vendored runtime, with no dependency on a running OLAV install. Any client that follows the [Agent Skills](https://code.claude.com/docs/en/skills) discovery convention (a `SKILL.md` with `name` + `description`, `references/`, `scripts/`) can pick them up and drive them — Claude Code is one such client; [pi-agent](https://github.com/earendil-works/pi) is another, and this guide uses it as the worked example because it is a different vendor's runtime with its own model routing, which is a stronger proof that the packs carry their own dependency than testing against OLAV's own tooling would be.

Nothing here talks to an OLAV server. The agent runtime (pi-agent, in this walkthrough) supplies the model loop; the pack supplies the skills; a plain subprocess call is the only thing connecting them — the same `execute_skill_script` stdin-JSON protocol the packs use inside OLAV itself.

---

## What's in a lean pack

| Directory | Contents |
|---|---|
| `skills/<agent>/` | `SKILL.md` + `references/*.guide.yaml` + `scripts/*.py`, one per shipped agent |
| `runtime/` | The subset of `olav`/`olav_netops`/`olav_presales` the scripts actually import — no LLM client, no vector store |
| `requirements-lean.txt` | Third-party packages the runtime needs (DuckDB, PyYAML, Pydantic, …) |

`olav-skills` ships the netops read/report/plan agents (`analyzer`, `importer`, `learner`, `netops`, `reporter`, `topology`, `writer`); `olav-presales-skills` ships the presales workflow agents (`analyst`, `designer`, `explorer`, `presales`, `publisher`, `surveyor`). Both packs deliberately exclude anything that needs an embedding backend — a script whose only job is vector search over LanceDB (e.g. semantic document search) is left out rather than shipped broken, and it says so in the pack's own README.

---

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-lean.txt
pip install ./runtime
```

That's the whole install — no OLAV server, no API key, no embedding endpoint. `pip install ./runtime` matters: it registers `olav`/`olav_netops` as real importable packages, which is what lets every script (not just the ones with their own path-bootstrap fallback) resolve `from olav.core.config import ...` correctly.

Then point the agent runtime at the skills:

```bash
cp -r skills/* ~/.pi/agent/skills/          # pi-agent's discovery directory
# or: cp -r skills/* ~/.claude/skills/      # Claude Code
```

---

## Worked example: pi-agent

Install pi-agent (Node.js ≥ 18) and pick a model provider — the example below uses DeepSeek:

```bash
npm install -g @earendil-works/pi-coding-agent
export DEEPSEEK_API_KEY="<key>"     # or use `pi` interactively and `/login`
```

**1. Confirm skill discovery.** Non-interactive mode (`--print`) runs one turn and exits — useful for scripted checks:

```bash
pi --provider deepseek --print 'List every skill you can see available to you right now, one per line.'
```

A correct install answers with the agent directory names from the pack(s) you copied in — nothing more, nothing missing.

**2. Drive a single script.** Ask for a task by name; pi-agent finds the right skill, reads its `SKILL.md`, and runs the script itself:

```bash
pi --provider deepseek --print \
  'Using the analyzer skill, run its describe_table script on the table netops.devices and report exactly what it returns.'
```

Against an empty database this correctly returns a structured "table not found" error — the point is that the runtime imported cleanly and the script executed, not that there happened to be data.

**3. A full, multi-step task.** The packs are designed for this — give the agent a goal, not a step list, and let it plan:

```bash
pi --provider deepseek --print \
  'Using the reporter skill, investigate the current health of this network: device inventory, BGP sessions, OSPF adjacencies, and the CDP-derived topology. Synthesize the findings into a structured report and export it via format_and_export. Tell me the exact file path and paste the report.'
```

Given a DuckDB snapshot to read from (see [Collector →](../netops/netops-agent/collector.md) for how one gets populated), pi-agent plans the SQL evidence-gathering itself, cross-checks more than one view against each other, and writes the export — the same behavior the reporter agent has inside OLAV, running under a different vendor's model loop entirely.

---

## What still needs the full platform

- **Live device access.** The lean packs exclude `collector`'s device-facing scripts (`take_snapshot`, `execute_cli_parallel`) on purpose — a "lean, no-server" pack is not the place for netmiko/nornir and live SSH credentials. If you need an agent to actually collect from devices, install the full extras (`pip install 'olav[agent]' 'olav-netops[device]'`) instead, and copy the `collector` skill in alongside the lean ones — see [Collector →](../netops/netops-agent/collector.md).
- **Memory / recall.** Nothing in a lean pack touches LanceDB or an embedding endpoint. A script whose entire job is semantic search (project-document search, for instance) is excluded from the pack rather than shipped in a state that fails silently.
- **Cross-agent orchestration.** OLAV's own router picks one sub-agent per turn and enforces per-agent tool caps; a third-party agent runtime has no such router, so it is the model — and your prompt — deciding which skill to reach for. State the skill by name (as in the examples above) when precision matters.
