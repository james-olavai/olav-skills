# Paths, environment and local configuration

For reproducible native offline work, use a dedicated venv, an explicit new `OLAV_HOME` and a deliberate working directory. Set the environment before importing Python modules: several paths are resolved and cached at import time.

## Select one analysis root

```sh
export OLAV_HOME=/absolute/path/to/authorized-analysis
cd "$OLAV_HOME"
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills doctor
```

Create that root beforehand for ordinary calls; the bundled walkthrough can create its own absent/empty home. `doctor` checks the package, not the selected data's correctness. `-I` isolates Python import behavior, not filesystem permissions or the environment variables used by the package.

General config root resolution is: explicit `OLAV_HOME`, otherwise nearest ancestor of CWD with `.olav/`, otherwise CWD. **Bounded `query_batch` requires an explicit `OLAV_HOME`**, so it does not use that general fallback. Do not rely on a shell location to select confidential data implicitly.

| Default path relative to the root | Purpose |
| --- | --- |
| `.olav/databases/main.duckdb` | Native local analysis database |
| `.olav/config/` | Existing local JSON configuration, when present |
| `.olav/templates/` | Custom TextFSM and Python parser files |
| `exports/snapshots/` | Saved snapshot files used by filesystem helpers |
| `exports/` | Reports, diagrams and other artifacts |

Some scripts use relative artifact paths from CWD while others use configured root constants. Inspect actual returned `db_path`, `report_path` and `path`; do not infer every output location from this table.

## Know the active overrides

`AGENT_DIR` and `OLAV_PATHS_AGENT_DIR` affect parts of the retained path configuration. `OLAV_PATHS_DATABASES_DIR` changes the configured database directory for paths using that resolver; the native batch reader is deliberately bound to `OLAV_HOME/.olav/databases/main.duckdb`. `OLAV_PATHS_SNAPSHOTS_DIR` changes saved snapshot location. Avoid such overrides in the first walkthrough so different entry points read the same intended scope.

SQL execution reads `OLAV_SQL_MEMORY_LIMIT`, `OLAV_SQL_THREADS` and optional `OLAV_SQL_TEMP_DIR`; values are bounded/validated by the reader and do not grant external access or create an OS memory sandbox. Individual SQL request budgets are documented in [scripts](scripts.md). `OLAV_LLM_MODEL_TIER` or retained local config can lower/alter query presentation caps. These compatibility settings do not start or configure a model service in the standalone CLI.

The retained config loader can read local `api.json` (or legacy model JSON files), `runtime.json` and `tasks.json` if present. This is compatibility code, not evidence that Skills supplies the old platform features named in those files. The CLI does not load `.env`, prompt for model keys or require them for deterministic examples. Keep credentials in the host's controlled process environment only when that host actually needs them, and do not dump the environment into logs.

## Keep server profiles separate

A retained `.olav/config/ingest-storage.json` can select a host-configured server path. Malformed configured storage must not silently be treated as an empty native database. This manual's reproducible flow uses a new local root with no such inherited config; it does not document server provisioning or migrate API-client/DuckLake deployments. An inherited `OLAV_DB_PATH` is not a universal supported native override and can be rejected by configured readers.

Back up the intended analysis root and review filesystem access before importing. Data-root selection is not per-user ACL enforcement. See [safety](../safety.md) and [migration](../migration.md) before reusing an existing project.
