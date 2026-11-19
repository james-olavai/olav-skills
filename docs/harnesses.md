# Use an existing AI harness

The seven standard skills describe when to use each capability and how to
invoke the standalone Python runtime. Your existing harness handles model
selection, discovery configuration, tool invocation and permissions. The same
runtime also runs directly from the CLI.

If your administrator has set up the private integration, see how to select your tenant, ask questions and download reports. [Open WebUI guide](openwebui.md)

## Install with pip, then bind

Install Olav Skills 0.2.0 in a dedicated Python environment, then choose the
binding for your harness. [Installation](installation.md)

```sh
olav-skills install --harness codex --scope user
olav-skills install --harness claude-code --scope user
olav-skills install --harness pi --scope user
```

The binding copies the seven skills and records the interpreter path. Repeat
it explicitly from the intended environment after changing the package version
or moving the venv.

## Three different handoffs

| Handoff | What it contains | What the host still supplies |
| --- | --- | --- |
| `olav-skills install` | Seven discoverable skills, exact interpreter binding and ownership hashes | Discovery, model, tool execution, project/data permissions |
| `olav-skills run <skill> <script>` | Direct stdin JSON execution using the invoking environment | Explicit inputs, authorized data root and result interpretation |
| [Portable export](portable.md) | Hashed knowledge/resources for a bounded loader | Runtime, registered tools, authorization and execution policy |

Use the [installation commands](installation.md) for project or user scope. The supported paths are `.agents/skills` for Codex, `.claude/skills` for Claude Code and `.pi/skills` for Pi projects; Pi user scope is `.pi/agent/skills`. The installer requires an existing scope root. User and project copies may expose duplicate names; inspect the actual active harness configuration.

## Check each step in your harness

Each installed skill has `olav-runtime.json` and `.olav-skills-install.json`. Its instructions name an absolute interpreter. Keep that venv at its recorded path; copying skills alone does not move the runtime. After a venv move or replacement, repeat the same installation command from the intended interpreter. Review conflicts rather than overwriting edited skill directories.

1. Check the copied manifests, then verify the harness lists all seven skills
   from the intended scope. Refresh the session if your harness requires it.
2. Run `version`, `doctor` and `list` from the bound environment, then check
   tool results against the known fixture. [Offline walkthrough](offline-data.md)
3. If the harness is configured to invoke tools, verify a specified call with
   known inputs and check the actual result. Then test ordinary user questions
   separately against the same known data. Inspect the activated skill, tool
   arguments, evidence and final answer.

The tested Pi configuration failed natural-question qualification, including
held-out wording in the local 0.2.0 test. Codex and Claude Code
natural questions were untested. [Compatibility](reference/compatibility.md)

For a host without standard discovery, export resources to a reviewed location and provide only the relevant SKILL.md to its model. Dispatch through the documented CLI, not an invented `execute_skill_script` API.

## Reading retained skill prose

Some distributed instructions retain historical platform terminology such as delegation, `task(...)`, `olav_recall_memory`, `format_and_export`, SSH collection or simulator references. The standalone pack's calling-convention note takes precedence for execution: load a sibling skill and invoke an actually listed Python script. There is no standalone implementation of those historical host tools. A mention in a reference does not establish availability or authorization.

Scheduling, background execution and approval mechanisms belong to your host configuration.
