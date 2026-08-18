# olav-skills

The OLAV expert workbench for Claude Code: give it a pile of dead CLI output and
it returns a queryable network model — and when it meets a command it has never
seen, it learns to parse it.

**Generated. Do not edit by hand.** Rebuilt from the olav repo with
`uv run python scripts/gen_olav_skills.py --write`.

## What is in it

| Agent | Scripts | References |
|---|---|---|
| `analyzer` | 7 | 7 |
| `importer` | 5 | 4 |
| `learner` | 2 | 2 |
| `netops` | 5 | 18 |
| `reporter` | 4 | 4 |
| `topology` | 5 | 3 |
| `writer` | 4 | 0 |

32 scripts across 7 agents.

## Install

```bash
pip install 'olav>=0.27.0' 'olav-netops>=0.27.0'
cp -r skills/* ~/.claude/skills/          # or into <project>/.claude/skills/
```

### Install (lean) — recommended

That line pulls **218 packages and 1.4 GB**: thirteen LangChain providers,
LangGraph, LanceDB + Arrow, scipy, sklearn, pandas. They are OLAV's agent runtime
and memory layer — and in Claude Code *you* are the agent runtime, so nothing here
uses them. Skip the dependency closure and install what the scripts actually
import:

```bash
pip install -r requirements-lean.txt
pip install --no-deps 'olav>=0.27.0' 'olav-netops>=0.27.0'
```

**127 MB**, and verified rather than assumed: all 36 files import, a collector
bundle lands (3 devices, 29 commands, 28 parsed, 13 views, 4 topology links), and
one real call per agent — devices, BGP topology, evidence search, a draw.io
diagram, a config diff, a parser-learning prep — returns the same answers as the
full install.

Three of the eight are there because leaving them out fails *silently*:

* **`ntc-templates`** — without it nothing parses. Raw output lands and zero rows
  become structured, which reads like an empty network rather than a missing
  library.
* **`netutils`** — without it interface names are not canonicalised, so `Gi1` and
  `GigabitEthernet1` become two ports on the same link.
* **`netconan`** — without it **redaction is skipped and credentials land on disk
  in plaintext**. OLAV prints a SECURITY warning and continues; on a laptop
  holding a customer's configs that is the wrong side to fail on. If you
  deliberately want no redaction, say so in `.olav/config/api.json`
  (`"redaction": {"enabled": false}`) rather than getting it by omission.

## Querying it

Every agent's scripts take one JSON object on stdin. For anything the typed
scripts do not cover, `analyzer/scripts/execute_sql.py` is the way in:

```bash
# no `sql` → get the schema back, then write your query against it
echo '{"query": "which devices are in here"}' | python analyzer/scripts/execute_sql.py

echo '{"query": "count", "sql": "SELECT count(*) FROM netops.devices"}' | python analyzer/scripts/execute_sql.py
```

Use it rather than your own `duckdb.connect()`. A snapshot you were handed is
usually the only copy, and this script will not write to it: SELECT runs
read-only, and a mutating statement comes back as
`status: "requires_approval"` without having run. It also caps how many rows
reach the model's context, spilling large results to `exports/queries/*.csv`.
`analyzer/scripts/search_logs.py` does the same for syslog Parquet, if a
`.olav/databases/syslogs/` came with the database.

Python and pip are the only requirements. Nothing here needs ContainerLab,
Batfish, a reachable device, or a network service.

## Data

These agents read a DuckDB database; they do not collect. Two ways to get one:

* **Copy one** — `main.duckdb` from an OLAV host into `.olav/databases/` of the
  directory you work in. Views travel inside the file.
* **Import a bundle** — collect with `olav-collector` (netmiko + pyyaml, runs on
  a laptop next to the devices), then `importer` lands it.

The database is resolved by `olav.core.config`, in this order: `OLAV_HOME` if
set, otherwise the nearest ancestor of the working directory that contains a
`.olav/` directory, otherwise the working directory itself.

**`OLAV_HOME` is the project root, not the `.olav` directory** — point it at
`/srv/site-a`, never at `/srv/site-a/.olav`. A wrong path does not raise:
scripts return an empty result, because a database that is not there reads as a
model with no devices in it. So if a query comes back empty, check the path
before you doubt the data:

```bash
python -c "from olav.core.config import MAIN_DB_PATH; print(MAIN_DB_PATH)"
```

## What this is not

It analyses a **snapshot**. There is no collection, no device access, no change
execution — it can tell you what to do and cannot do it. Verification is
structural (topology consistency, allocation conflicts), not behavioural: no
Batfish, no lab. A design that passes here is self-consistent, not proven to run.

## Agents referred to but not included

This wave is deliberately narrow. Where the prose points at an agent that
is not here, the capability exists in the full OLAV runtime — the pointer
is accurate, the agent is simply out of the box:

- `netops` mentions `admin`, `analyst`, `simulator`

## License

Business Source License 1.1 — see `LICENSE`, copied verbatim from the `olav`
repo, whose grant already covers agent skills. Non-production use is free;
production use is bounded by the Additional Use Grant in that file. Change
Date 2030-01-01, after which it becomes Apache 2.0.
