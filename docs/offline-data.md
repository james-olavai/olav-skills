# First offline analysis: validate, import, query and report

This guide targets `olav-skills` 0.1.1 installed with
`python -m pip install olav-skills==0.1.1` or from a reviewed local wheel.
No model, collector connection or credentials are needed for the synthetic
example. Use the dedicated environment from
[installation](installation.md). Examples use POSIX shell paths; substitute your
actual absolute venv and a new analysis directory.

## Run the complete synthetic example

The wheel and sdist include `examples/synthetic_walkthrough.py` and
`examples/synthetic_2host_bundle`. In an installed wheel they live under
`olav_skills/examples`. Execute the installed copy, from outside the
source checkout:

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
export PYTHONDONTWRITEBYTECODE=1
"$SKILLS_PYTHON" -I -B -m olav_skills.examples.synthetic_walkthrough --home "$OLAV_HOME"
```

The directory supplied to `--home` must be absent or empty. The example creates it, pins `OLAV_HOME` in a
clean child environment, then invokes the installed CLI with stdin JSON. It
validates the bundle, imports it, asserts the exact device set `{R1, R2}` rather
than only a count, renders a Mermaid topology artifact, finds one R1 `show
version` evidence record, and writes a Markdown report from those verified
results. The input contains six command outputs and a single recorded CDP link.
The example uses no model and does not grant access to your network.

Inspect these outputs under the selected home:

| Output | What to check |
| --- | --- |
| `validate.json` | `ok: true`, counts and content hash |
| `import.json` | `hosts: 2`, `commands: 6`, actual `db_path`, `snapshot_id`, parse report |
| `devices.json` | Exactly R1 and R2, `has_more: false` |
| `topology.json` | `status: ok`, two hosts, one edge, existing artifact `path` |
| `evidence.json` | One matching R1 record, source identity/hash and excerpt bounds |
| `exports/reports/synthetic_inventory.md` | Fixture scope, snapshot and artifact references |
| `*.stderr.log` | Per-step diagnostics, kept separately from JSON stdout |

The importer also returns an import-report path. That report describes parsing
coverage, including unsupported/unparsed commands. The example's inventory
Markdown is composed by its Python code; it is not an independent report-writing
API or a model-generated analysis. Missing commands, unknown roles and timestamps
remain unknown; this fixture does not establish current live network state.

## Repeat individual calls

After the complete example, keep the same `SKILLS_PYTHON` and `OLAV_HOME`.
To locate the installed fixture and validate it without writing to the database:

```sh
BUNDLE=$("$SKILLS_PYTHON" -I -B -c 'from importlib.resources import files; print(files("olav_skills") / "examples/synthetic_2host_bundle")')
"$SKILLS_PYTHON" -I -B -c 'import json,sys; print(json.dumps({"path":sys.argv[1]}))' "$BUNDLE" | "$SKILLS_PYTHON" -I -B -m olav_skills run importer validate_bundle
```

Validation supports directory bundles. It checks manifest schema, per-command
content SHA-256 and counts; inspect the JSON `ok`, `errors` and `warnings` fields.
The validator's process exit alone does not indicate `ok`. A content hash checks
consistency, not sender identity or authorization. For a ZIP handoff, unpack it
safely into a new directory after reviewing paths and permissions, then validate
that directory. Do not assume the directory validator supports ZIP input merely
because the importer can read ZIPs.

For a new authorized bundle, point `BUNDLE` at its reviewed absolute directory and
select a separate, explicit analysis root before importing. Do not import again
into an existing user's data just to try the command:

```sh
export OLAV_HOME=/absolute/path/to/new-authorized-analysis
mkdir -p "$OLAV_HOME"
"$SKILLS_PYTHON" -I -B -c 'import json,sys; print(json.dumps({"path":sys.argv[1]}))' "$BUNDLE" | "$SKILLS_PYTHON" -I -B -m olav_skills run importer ingest_snapshot
```

Check the returned database path, bundle hash, snapshot ID and parse report.
Import writes the database, derived views and reports. Bundled templates may be
staged for review; import does not silently activate them. These commands target
the native local DuckDB flow; server/DuckLake deployment maintenance has separate
contracts.

For the synthetic home produced by the complete example, direct read-only SQL is:

```sh
printf '%s\n' '{"sql":"SELECT hostname FROM netops.devices ORDER BY hostname","max_rows":10}' | "$SKILLS_PYTHON" -I -B -m olav_skills run analyzer execute_sql
```

Use the actual `snapshot_id` returned from import for topology and evidence.
The following command retrieves it from the example's saved `import.json`, then
passes it as JSON without shell interpolation into JSON strings:

```sh
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"concept":"all","snapshot_id":sid}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run topology query_topology
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"source":"command_output","pattern":"Hostname","device":"R1","commands":["show version"],"snapshot":sid,"limit":5}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run reporter query_evidence
```

A missing topology layer is a collection gap, not proof that the protocol has no
neighbors. Snapshot IDs and default `latest` selection do not prove a collection
instant or real-time freshness. Prefer explicit snapshot IDs and carry actual
collection-time evidence into reports.

## Bring your own offline capture

The collector is a separate delivery unit. Run its documented collection process
where device access is authorized; this package neither connects to devices nor
supplies a collector command. Transfer only an authorized, reviewed bundle to the
analysis machine using your approved transport. Preserve its manifest and command
bytes. Review redaction and provenance before sharing: a manifest's redaction
flag alone does not prove that secrets were removed.

The directory contract is `manifest.yaml` plus `devices/<hostname>/_meta.yaml`
and per-command `.txt` files. Use the distributed synthetic bundle to inspect
format, not to disguise real captures as synthetic. A human-readable bundle name
or declared count does not establish exact inventory membership. Validate first,
then ingest in a new root, review parser coverage and query results.

Do not inherit unintended `OLAV_DB_PATH`, server connection settings or another
project's config. Set `OLAV_HOME` before importing Python data modules. Retain
read-only source bundles and back up the selected analysis root as appropriate;
no command here converts a historical production database in place.

## Errors and result boundaries

- Missing/invalid bundle: inspect validator `errors`; do not proceed on `ok: false`.
  Import rejection uses structured status/error fields and may exit nonzero.
- Missing table/view or empty evidence: distinguish absent source, parser gaps,
  a wrong analysis root and a successful empty result. Unknown is not zero.
- SQL: supply `sql` for deterministic execution. Natural-language `query` is not
  a substitute for a local SQL generator. Mutations and external file access
  are refused by the reader; this is not a general SQL administration interface.
- Bounded output: inspect `has_more`, truncation, limits and count semantics.
  A preview or bounded CSV is not automatically a full export. Narrow filters or
  use supported pagination and stable ordering; never infer exact membership
  from an incomplete page or matching aggregate counts.
- JSON stdout and process exits vary by script. Inspect both the response and
  stderr, and validate the resulting artifact or semantic result. Exit zero and
  `doctor` success do not establish correct business conclusions.

The filesystem workflow can be tested without an AI harness. Harness discovery,
natural-language skill selection, model reasoning and live data accuracy require
separate evidence and are not established by this tutorial.
