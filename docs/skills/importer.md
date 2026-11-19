# importer: land offline captures

Importer reads pre-collected files, validates canonical bundles and builds a local queryable snapshot. It does not SSH to devices or change their configuration. Use [the complete offline walkthrough](../offline-data.md) for an executable two-host example.

## Follow the prepared path

1. `survey_bundle` accepts `{"path":"/absolute/input"}`. It identifies supported formats and can extract/normalize a directory, archive or raw dump. Inspect `error`, `ingest_supported` and `notes` before continuing.
2. Keep the returned `path`; it may differ from the original input. Run `validate_bundle` against that canonical directory.
3. Continue only on JSON `ok: true`. Review warnings and host/command counts. A hash mismatch is a source integrity failure, not something to bypass.
4. `ingest_snapshot` accepts the prepared `path`, optional `collection_source` and reviewed `host_platforms`. Fix an explicit new `OLAV_HOME` first and choose a host timeout suitable for the capture size.
5. Record the returned `snapshot_id`, `db_path`, bundle hash, `parse_report` and `report_path`.

For a canonical directory already reviewed, validation and import can be called directly as shown in the walkthrough. The validator itself does not support arbitrary archive formats. Do not feed it the original archive after survey produced a new directory.

## What the bundle contains

A canonical bundle has `manifest.yaml`, `devices/<hostname>/_meta.yaml` and per-command `.txt` files. The manifest declares schema version, collector name/version, host count, redaction status and content SHA-256. The hash streams command-file bytes in sorted host/file order, including optional headers. Device metadata records hostname, management IP, platform and vendor. Optional explicit collection metadata is preferable to inference from filenames.

`discover_platform` accepts `host_dir` when survey reports hosts needing platform detection. Unknown platform confidence must remain unknown; do not infer network role from a platform guess. Survey's notes describe the next supported step; stop if the format is unsupported.

## Report the gaps as well as the landed data

Raw output can land without a structured parser. Report `devices_landed`, `command_outputs_landed`, parsed and unparsed counts, `unparsed_by_reason`, and devices without structured data. Use the full report to distinguish `raw_only`, `no_parser_registered` and `parser_error` cases. Imported count is not parsed count, and absent protocol rows are not a complete fleet inventory.

Suggest [learner](learner.md) for an appropriate parser gap as a separate reviewed operation. Import does not run a model or automatically learn every unsupported command. Bundled templates can be staged but are not silently activated.

Import writes analysis state and reports. It is not a database migration guarantee or a server maintenance API. Keep the original capture and [validate an authorized copy](../migration.md) before using historical data. See [configuration](../reference/configuration.md) to avoid accidentally selecting another project's database.

## Unreleased native provenance

The candidate's official native directory import saves new historical raw
captures and bounded source/clock metadata in its database transaction.
Default native snapshot labels gain a unique import suffix; they are not
capture clocks. Later raw-store overwrites leave the historical ledger intact.
Old projects are not backfilled; direct legacy no-opt-in library paths retain
their previous limits. See [evidence readiness](../reference/evidence-readiness.md).
Actual stored raw hashes bind the existing redaction output, not a claim that
redaction was successful or the source authentic. Filesystem staging, report
and redaction-state files are outside the database rollback guarantee. Native
imports bind redaction state to the selected project workspace; after changing
`OLAV_HOME`, start a fresh process before importing.
