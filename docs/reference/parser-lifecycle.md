# Parser versions and offline reparse

Version management and offline reparse require Olav Skills 0.2. The earlier
`learner finish_learn` workflow is available in 0.1.1.

## Validate, activate and roll back

Use an explicitly authorized absolute project path. Set `OLAV_HOME` to the
same project before normal parsing or import. The management command takes one
JSON object through stdin and includes the canonical platform and command in its
response. For example, in an existing scratch project:

```sh
export OLAV_HOME=/absolute/scratch/project
printf '%s\n' '{"project":"/absolute/scratch/project","platform":"cisco_ios","command":"show widget state","operation":"create","version":"v1","dsl":"textfsm","source":"Value NAME (\\S+)\n\nStart\n  ^widget ${NAME}$$ -> Record\n","training_samples":["widget draft"]}' |
  olav-skills run learner manage_parser
printf '%s\n' '{"project":"/absolute/scratch/project","platform":"cisco_ios","command":"show widget state","operation":"validate","version":"v1","holdout":[{"raw_output":"widget alpha","expected_rows":[{"name":"alpha"}]},{"raw_output":"no widgets","expected_rows":[]}]}' |
  olav-skills run learner manage_parser
printf '%s\n' '{"project":"/absolute/scratch/project","platform":"cisco_ios","command":"show widget state","operation":"activate","version":"v1"}' |
  olav-skills run learner manage_parser
```

Inspect with `operation: "inspect"` and an optional `version`. Roll back with
`operation: "rollback"` and `target: "previous"` (the default) or
`target: "legacy"`. Each selection is scoped to one project, platform and
command. Activating changes future normal parsing for that scope.
Rolling back to legacy restores the existing Python, custom TextFSM and
bundled-template lookup. Existing parser files and parsed rows remain intact.
A managed parser's empty result ends lookup. Invalid active metadata or parser
execution errors surface as failures.

A version label fixes the parser source and declared training sample hashes.
Validation uses two to sixteen distinct holdouts and compares the ordered JSON
records, including spelling, field types and normalized field values. Include
at least one independently expected nonempty result. Newline and outer
whitespace variants count as the same input when screening for duplicate
samples. The digest is a local integrity check. It does not establish where an
operator got the samples or whether they cover the fleet. Inspect validation
for sample and expected hashes, row counts and any failure diagnostic.
A successful validation remains fixed for its version. A failed validation
returns `invalid` and may be retried with corrected evidence. Use a new
version to change source.

Managed Python supports pure `parse(raw)` functions, restricted builtins, and
selected functions from `re`, `json`, `ipaddress`, `collections` and
`itertools`. AST and sandbox checks, a stripped child environment and worker
resource limits apply. This profile is narrower than the legacy learning path.
It is intended for reviewed parser source in a controlled local project and
does not supply container or operating-system isolation.

## Reparse a stored snapshot

```sh
printf '%s\n' '{"project":"/absolute/scratch/project","platform":"cisco_ios","command":"show widget state","snapshot_id":"lab-recording-03","version":"v1","output":"widgets-v1.json"}' |
  olav-skills run learner reparse_snapshot
```

You can reparse with any validated version, whether active or inactive. The
command reads the project’s native `.olav/databases/main.duckdb` with
external access and disk spill disabled. For an exact snapshot present in
`netops.ingest_sources`, it verifies stored record keys and raw hashes. When
that snapshot is absent from the ledger, the native `netops.raw_output_store`
can supply only records still carrying the matching last-writer label.
Earlier overwritten raw bytes are unavailable through this command.
`coverage.source_scope` distinguishes those sources. Full snapshot collection
coverage remains unknown.

Snapshot labels, device identities and bundle identifiers preserve their exact
stored spelling, including case. The command and platform follow the same
canonical scope used to manage the parser.

The new JSON in `.olav/derived-reparses/` records the input snapshot label,
device, command, source, UTF-8 raw hash, selected version, parser source hash,
validation digest and parsed rows. Capture time comes from stored evidence.
Date-shaped snapshot labels and write timestamps do not establish capture
time. Bundle time, when available, stays under bundle provenance.
An empty parser result is labelled `no_rows`. Missing source returns
`unavailable` with no artifact. Any error or limit violation prevents
publication of a formal artifact. A response with `status: "ok"` provides
the new file path and SHA-256. Repeating the same filename returns an error.
The command does not write the original database, raw rows, existing parsed
rows, or prior derived exports.

Limits include 128 retained raw records, 256 KiB per raw record, 4 MiB total
raw text, 2,000 parsed rows per input, 20,000 total parsed rows, 4 MiB
for the artifact, and a 12-second deadline per worker stage. Replay requires
a checkpointed local database up to256MiB and a 64-bit Linux host with OFD locks. The command refuses symbolic links and an active or uncheckpointed WAL.

Raw-source reading occurs in a dedicated exec worker through a source-locked private snapshot copy; DuckDB opens the actual copy, not a proc descriptor alias of the original. The supervisor owns the temporary root and cleans copies on local cancellation. Low-level snapshot readers must not share a process with a source writer.
