# olav-skills

The OLAV expert workbench for Claude Code: give it a pile of dead CLI output and
it returns a queryable network model — and when it meets a command it has never
seen, it learns to parse it.

**Generated. Do not edit by hand.** Rebuilt from the olav repo with
`uv run python scripts/gen_olav_skills.py --write`.

## What is in it

| Agent | Scripts | References |
|---|---|---|
| `analyzer` | 5 | 7 |
| `importer` | 5 | 4 |
| `learner` | 2 | 2 |
| `topology` | 5 | 3 |

17 scripts across 4 agents.

## Install

```bash
pip install 'olav>=0.27.0' 'olav-netops>=0.27.0'
cp -r skills/* ~/.claude/skills/          # or into <project>/.claude/skills/
```

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

- `analyzer` mentions `reporter`

## License

Business Source License 1.1 — see `LICENSE`, copied verbatim from the `olav`
repo, whose grant already covers agent skills. Non-production use is free;
production use is bounded by the Additional Use Grant in that file. Change
Date 2030-01-01, after which it becomes Apache 2.0.
