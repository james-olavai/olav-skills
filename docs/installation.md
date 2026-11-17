# Install and bind standard skills

This guide covers version `0.1.1` of the single `olav-skills` distribution.
It has its own distribution history and does not follow the legacy platform's
semantic version.

Use Python 3.11 or newer and a dedicated virtual environment:

```sh
python3 -m venv /absolute/path/to/skills-venv
/absolute/path/to/skills-venv/bin/python -m pip install olav-skills==0.1.1
/absolute/path/to/skills-venv/bin/olav-skills version
/absolute/path/to/skills-venv/bin/olav-skills doctor
/absolute/path/to/skills-venv/bin/olav-skills list
```

For a reviewed local wheel, verify its supplied SHA-256 and replace the install
command with:

```sh
/absolute/path/to/skills-venv/bin/python -m pip install /absolute/path/to/olav_skills-0.1.1-py3-none-any.whl
```

For source development, `python -m pip install .` remains available, but release
verification must use the wheel in a fresh, non-editable environment. The wheel
contains deterministic Python code plus all seven standard skill directories;
it does not depend on `olav`, `olav-netops`, DeepAgents or LangGraph. The eight
third-party dependencies are declared in `pyproject.toml`. No `.env` is loaded
by the CLI; it does not configure models or run agent sessions.

## Harness discovery layout

Bind an existing project explicitly, or select user scope explicitly:

```sh
/absolute/path/to/skills-venv/bin/olav-skills install --harness codex --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness codex --scope user --home /absolute/user-home
/absolute/path/to/skills-venv/bin/olav-skills install --harness claude-code --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness claude-code --scope user --home /absolute/user-home
/absolute/path/to/skills-venv/bin/olav-skills install --harness pi --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness pi --scope user --home /absolute/user-home
```

| Harness | Project layout | User layout |
| --- | --- | --- |
| Claude Code | `<project>/.claude/skills/<skill>` | `~/.claude/skills/<skill>` |
| Codex | `<project>/.agents/skills/<skill>` | `~/.agents/skills/<skill>` |
| Pi | `<project>/.pi/skills/<skill>` | `~/.pi/agent/skills/<skill>` |

Pi user scope targets the default agent directory only. The installer does not
read `PI_CODING_AGENT_DIR`; a harness configured with that override will not
discover this default user installation automatically. Use project scope or
explicitly manage the custom harness configuration.

`--home /absolute/home` can explicitly bind an alternative user home; it is
rejected for project scope. Project scope requires `--project`. These are
installation layout adapters. Package tests verify layouts, not model behavior. Separate native no-model discovery
checks passed for Codex 0.169.0 and Pi 1.3.0; Claude Code 2.1.323 was checked
for filesystem layout only. See [compatibility](reference/compatibility.md).
Install does not change harness permissions.

Each installed skill includes `olav-runtime.json`, an exact absolute Python
interpreter in its instructions, and `.olav-skills-install.json` with scope,
version and content hashes. Keep that virtual environment at its bound path.
After moving or replacing it, rerun install from the desired interpreter.
Repeated installation/upgrades replace only unchanged files owned by this
installer at the same scope binding. A preexisting same-name skill, symlink,
local edit or additional user file fails with exit code 2 and is preserved.
Resolve that conflict explicitly; there is no force-overwrite switch. Whole
installations are preflighted for conflicts, but an operating-system failure
partway through may leave some skills upgraded; rerunning is safe.

## Upgrade and remove

For each of the three harnesses, install a reviewed replacement wheel into a
new dedicated venv, run `version`/`doctor`, then repeat that harness's original
`install` command from the new venv. Keep the scope root identical. Version and
Python path are refreshed only for unchanged, installer-owned skill directories.
A moved venv also requires reinstalling the bindings. Preserve the previous
venv until validation completes so it can reinstall its own matching resources.
The wheel install alone does not update already copied skills.

When a same-name directory is unowned, edited or has extra files, stop and review
its contents. Back it up or move that exact directory outside the discovery tree,
then rerun installation; do not remove a whole `.agents`, `.claude` or `.pi`
directory. Project and user scope can both expose the same skill name; inspect
your harness's active configuration and remove duplicate registrations explicitly.
Installer metadata is ownership evidence, not a security boundary against a user
who can edit it.

There is no CLI `uninstall` or `remove` command. To retire an installation, inspect
`olav-runtime.json` and `.olav-skills-install.json` in each of the seven skill
directories. After confirming ownership and preserving local changes, remove only
those exact directories from the table above. This is a manual filesystem step;
never remove unrelated skills or the entire discovery root. `python -m pip
uninstall olav-skills` removes the Python distribution only, leaving copied skill
directories and analysis data intact. Remove stale skills before deleting their
venv. Data under `OLAV_HOME` and generated reports require their own explicit
retention/deletion decision.

## Export and execute

```sh
/absolute/path/to/skills-venv/bin/olav-skills export /absolute/new-directory
export OLAV_HOME=/absolute/path/to/your-authorized-analysis
printf '%s' '{"sql":"SELECT hostname FROM netops.devices ORDER BY hostname"}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer execute_sql
```

Export creates the seven standard directories without requiring a harness. It
uses the same ownership and local-edit checks for repeat exports. Direct script
execution remains available with the bound interpreter, for example
`/absolute/path/to/skills-venv/bin/python /absolute/new-directory/analyzer/scripts/execute_sql.py`.
Both paths accept stdin JSON and preserve script stdout and exit status.
`run` accepts only a known skill and a Python script basename present in that
skill's packaged `scripts/` directory; paths and traversal are rejected.
This dispatch validation is not a sandbox: the trusted scripts have the
filesystem access of their process and require appropriately authorized data.

Create the analysis database with the [offline walkthrough](offline-data.md) before
executing the SQL example above. Set `OLAV_HOME` explicitly to your authorized project root before data work.
The existing `.olav` formats and root resolution semantics are retained; export
and installation do not grant access to data or modify those settings. Doctor
reports the interpreter, resource location and missing base dependencies; a
successful doctor is not evidence of correct business answers or valid data.

## Troubleshooting

- `doctor` reports missing modules/resources: confirm the absolute interpreter
  belongs to the dedicated venv and reinstall the reviewed wheel there. Do not
  repair a legacy production environment in place.
- `unowned destination`, binding mismatch, symlink or local-edit refusal: inspect
  the exact reported directory; preserve it before resolving the conflict. There
  is no `--force` option.
- Skill installed but unavailable: verify the selected project/user layout,
  actual harness configuration and its refresh/restart procedure. The package
  installer does not start or configure the harness or grant execution tools.
- Wrong database or missing tables: check explicit `OLAV_HOME` before launching
  Python and remove unintended environment overrides from the child environment.
  Inspect the ingest `db_path`, parse report and query errors. A successful
  process exit alone does not prove a valid answer.
- Do not put credentials in stdin examples, skill instructions or report logs.
  This package does not load `.env`; models and their credentials belong to the
  host harness. Direct script execution has process filesystem permissions and
  is not a sandbox. Import and artifact creation write files.
