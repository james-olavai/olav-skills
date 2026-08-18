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
python -m venv .venv && . .venv/bin/activate
pip install ./runtime
cp -r skills/* ~/.claude/skills/          # or into <project>/.claude/skills/
```

That is the whole install. **This pack depends on no OLAV distribution**: it
carries the library code it needs in `runtime/` — 44 modules, copied from the olav
repo and generated, never hand-edited — and `runtime/pyproject.toml` declares only
third-party packages. Nothing is fetched from PyPI's `olav`, so a refactor over
there cannot change what a published pack does.

Seven third-party packages, ~120 MB installed. Three of them fail *silently* if
you drop them, which is why they are pinned rather than suggested:

* **`ntc-templates`** — without it nothing parses. Raw output lands and zero rows
  become structured, which reads like an empty network rather than a missing
  library.
* **`netutils`** — without it interface names are not canonicalised, so `Gi1` and
  `GigabitEthernet1` become two ports on the same link.
* **`netconan`** — without it **redaction is skipped and credentials land on disk
  in plaintext**. If you deliberately want no redaction, say so in
  `.olav/config/api.json` (`"redaction": {"enabled": false}`) rather than getting
  it by omission.

**Use a virtualenv.** `runtime/` provides the import names `olav` and
`olav_netops`, so it must not share an environment with a platform install of
OLAV itself.

Verified rather than assumed, in an environment with no OLAV package present: all
36 files import, a collector bundle lands (3 devices, 29 commands, 28 parsed, 13
views, 4 topology links), and one real call per agent — devices, SQL, BGP topology,
evidence search, a draw.io diagram, a config diff, bundle validation — returns what
a full platform install returns.

### If you already run the OLAV platform

Then you do not need `runtime/`: the same scripts work against the installed
packages.

```bash
pip install 'olav>=0.28.0' 'olav-netops>=0.28.0'
```

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
