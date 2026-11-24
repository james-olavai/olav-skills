# Install Olav Skills with pip

Install the single **`olav-skills`** package from PyPI, then bind its bundled
skills to your harness. The package contains the Python tools and all seven
skills. You do not need to clone a repository, copy skill folders by hand or
install the retired `olav` / `olav-netops` packages.

Use Python 3.11 or newer. Create and activate a dedicated environment first
(the following examples use a POSIX shell):

```sh
python3 -m venv ~/.venvs/olav-skills
. ~/.venvs/olav-skills/bin/activate
```

## 1. Install the package

```sh
python -m pip install olav-skills==0.2.2
```

Run subsequent commands in the environment where you installed 0.2.2. For a
supplied wheel, follow the [local wheel walkthrough](release-candidate.md).

## 2. Bind the skills to your harness

Choose the command for the harness you use:

```sh
# Codex
olav-skills install --harness codex --scope user

# Claude Code
olav-skills install --harness claude-code --scope user

# Pi
olav-skills install --harness pi --scope user
```

Pip installs the runtime and bundled resources into the venv. This step copies
the seven skill directories to the harness discovery path and records the venv's
absolute Python interpreter. Keep that environment at its current path and
refresh the harness session if needed to discover the skills.

Check the installation, then follow the [offline data walkthrough](offline-data.md):

```sh
olav-skills version
olav-skills doctor
olav-skills list
```

The CLI does not load `.env`, configure a model or run an agent session.

## Project scope and discovery layout

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
rejected for project scope. Project scope requires `--project`. The installer
prepares those layouts. Actual discovery and tool permissions depend on the
harness configuration. [Compatibility and tested versions](reference/compatibility.md)

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

## Upgrade, roll back or remove

Upgrade through pip in the same activated environment, then repeat your
harness binding command. To select the latest published version for Codex:

```sh
python -m pip install --upgrade olav-skills
olav-skills install --harness codex --scope user
```

For Claude Code or Pi, repeat the corresponding command from step 2. The pip
upgrade alone does not refresh skills already copied to discovery directories.
Keep the original scope root. Only unchanged, installer-owned skill directories
are refreshed; preserve and review any local modifications first.

For a staged upgrade, install the target version in a new venv, check it, then
bind from that venv to the same scope root. Retain the previous venv until
validation completes. To roll back, rerun the binding from the retained venv,
then check the version and interpreter in each copied skill's
`olav-runtime.json`. If both versions were installed in the same venv,
reinstall the reviewed prior version before rebinding. Database and artifact
writes are outside the scope of package and skill rollback.

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

## Optional: offline installation and source development

For an offline or reviewed-artifact installation, verify the supplied SHA-256
and install the local wheel **instead of** the PyPI command in step 1:

```sh
python -m pip install /absolute/path/to/olav_skills-0.2.2-py3-none-any.whl
```

Its dependencies must also be available in your offline environment. Bind the
harness as in step 2. A wheel contains the same Python code and seven skills.
Source contributors can use `python -m pip install .` from a checkout; this is
not required for normal installation. Release verification uses a fresh,
non-editable wheel environment. Dependencies are declared in `pyproject.toml`;
there is no dependency on DeepAgents or LangGraph.
