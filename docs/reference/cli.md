# Management CLI reference

This reference describes `olav-skills` **0.1.1**. Use the executable in the selected venv, or `/absolute/path/to/skills-venv/bin/python -m olav_skills`. The available management commands are:

```text
olav-skills [-h] {version,list,doctor,export,install,run} ...
```

| Command | Input | Output/effect |
| --- | --- | --- |
| `version` | None | Plain version string |
| `list` | None | JSON version, interpreter, seven skill paths and public script names |
| `doctor` | None | JSON resource/dependency checks and `ok` |
| `export DESTINATION` | Reviewed destination | Write standard skills with interpreter/ownership binding |
| `install` | Explicit harness and scope | Copy discoverable skill directories at that scope |
| `run SKILL SCRIPT` | One JSON object on stdin | Dispatch the packaged script using the current interpreter |

`doctor` returns 0 when base dependencies/resources are present and 1 when they are missing. Management validation/filesystem failures normally return 2 and a stderr message; argument parsing also uses normal argparse failures. `run` forwards script stdout, stderr and exit status. Inspect script content/status as well as process success.

## Install arguments

```text
olav-skills install --harness {claude-code,codex,pi}
                    --scope {project,user} [--project PATH] [--home PATH]
```

Project scope requires `--project` and rejects `--home`. User scope rejects `--project`; `--home` overrides the actual user home. The scope root must exist. There is no implicit “current project” installation. See [installation](../installation.md) for all six commands, directory layout, local-edit conflicts, upgrades and removal.

`export` and `install` bind an absolute interpreter and content ownership manifest. Repeating them is permitted only against matching installer-owned, unchanged destinations. No force overwrite, removal or uninstall command exists.

## Script dispatch

```sh
export OLAV_HOME=/absolute/path/to/authorized-analysis
printf '%s\n' '{"operation":"context"}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer query_batch
```

Use `list` to discover names. The dispatcher accepts a known skill and Python script basename with optional `.py`; it rejects path traversal and a script outside that skill's packaged directory. It does not interpret natural-language arguments. A script's domain parameters go in stdin JSON, not management flags. Run management `--help` for CLI usage; many entry scripts do not implement `--help` themselves.

Name validation is not a sandbox. The scripts still run with process permissions. `OLAV_HOME` selection and result interpretation remain the caller's responsibility.

## Portable export is a separate module

```sh
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills.portable --out /absolute/new-directory
```

This knowledge-only exporter is not a `portable` management subcommand. It requires installed resources and a new canonical destination; see [its full contract](../portable.md). The CLI contains no chat, model setup, collector, service, schedule, simulator, general database administration or automatic migration command.
