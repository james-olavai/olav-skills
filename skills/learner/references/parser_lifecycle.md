# Parser versions and stored-raw replay

These native entries are unreleased and require a candidate build. Use an
authorized absolute `project` and explicitly set `OLAV_HOME` for normal
parsing in that project. Manage one canonical `platform` and `command` at
a time. The legacy `finish_learn` entry still writes legacy-format templates.

Use `olav-skills run learner manage_parser` with one stdin JSON object. Every
request includes `project`, `platform`, `command` and `operation`.

| Operation | Additional fields |
| --- | --- |
| `create` | `version`, `dsl` (`textfsm` or `python`), `source` without a DSL marker, `training_samples` containing raw strings |
| `validate` | `version`, `holdout` containing `{raw_output, expected_rows}` objects |
| `inspect` | Optional `version` |
| `activate` | Validated `version` |
| `rollback` | Optional `target` of `previous` (default) or `legacy` |

Supply two or more independently obtained holdouts and expected normalized
row lists, including at least one positive example. Verify expected fields
and every row. The tool checks distinct input hashes after newline and
boundary-whitespace normalization. Sample origin and representativeness
still depend on the operator. A failed validation has status `invalid`
and cannot activate. Activated versions are selected by the normal parser
entry. Activating or rolling back changes only project-local version
selection. Inspect the active version and validation digest.

Use `olav-skills run learner reparse_snapshot` with `project`, `platform`,
`command`, exact `snapshot_id`, validated `version`, and new `output`
filename ending in `.json`. The artifact is published in
`.olav/derived-reparses/` after all selected records finish. Report its
`source_scope`, source hashes, parser hash, and unknown or conflicting
times. `last_writer_exact_label_partial` cannot recover overwritten
raw records. An empty parsed row list or `unavailable` result does not
establish that a protocol, endpoint, or device was absent. File paths,
timeouts, scope mismatches, and parser failures are handled by the entry.

Stored raw is now read in a dedicated worker via the source-locked private snapshot-copy backend on 64-bit Linux (DB<=256MiB). The parent owns scratch cleanup and source verification completes before derived publication. A direct proc FD alias does not establish which bytes DuckDB opened.
