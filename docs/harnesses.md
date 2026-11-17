# Use an existing AI harness

Skills are files that a harness discovers. They describe when to use a capability and how to invoke its deterministic scripts. They do not create an agent process, register an MCP server, select a model or grant execution permissions.

## Three different handoffs

| Handoff | What it contains | What the host still supplies |
| --- | --- | --- |
| `olav-skills install` | Seven discoverable skills, exact interpreter binding and ownership hashes | Discovery, model, tool execution, project/data permissions |
| `olav-skills run <skill> <script>` | Direct stdin JSON execution using the invoking environment | Explicit inputs, authorized data root and result interpretation |
| [Portable export](portable.md) | Hashed knowledge/resources for a bounded loader | Runtime, registered tools, authorization and execution policy |

Use the [installation commands](installation.md) for project or user scope. The supported paths are `.agents/skills` for Codex, `.claude/skills` for Claude Code and `.pi/skills` for Pi projects; Pi user scope is `.pi/agent/skills`. The installer requires an existing scope root. User and project copies may expose duplicate names; inspect the actual active harness configuration.

## Bind execution, then check discovery

Each installed skill has `olav-runtime.json` and `.olav-skills-install.json`. Its instructions name an absolute interpreter. Keep that venv at its recorded path; copying skills alone does not move the runtime. After a venv move or replacement, repeat the same installation command from the intended interpreter. Review conflicts rather than overwriting edited skill directories.

A useful local acceptance sequence is:

1. Run `version`, `doctor` and `list` with the bound interpreter.
2. Run the [offline walkthrough](offline-data.md) with a new synthetic home.
3. Verify your harness actually lists the seven skill names from the intended scope and refresh its session if required by that harness.
4. Separately ask ordinary questions against a known synthetic dataset. Inspect actual skill activation, tool inputs/outputs and the final answer. Keep failures and unknowns; successful discovery alone does not prove routing or reasoning.

For a host without standard discovery, export resources to a reviewed location and provide only the relevant SKILL.md to its model. Dispatch through the documented CLI, not an invented `execute_skill_script` API.

## Reading retained skill prose

Some distributed instructions retain historical platform terminology such as delegation, `task(...)`, `olav_recall_memory`, `format_and_export`, SSH collection or simulator references. The standalone pack's calling-convention note takes precedence for execution: load a sibling skill and invoke an actually listed Python script. There is no standalone implementation of those historical host tools. A mention in a reference does not establish availability or authorization.

The [compatibility matrix](reference/compatibility.md) records native discovery checks separately from six filesystem layouts and deterministic workflows. No cross-harness model-answer certification is implied. Schedule/background execution and approval mechanisms belong to your host configuration; this package does not install them.
