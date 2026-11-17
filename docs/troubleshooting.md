# Troubleshooting

Capture the exact interpreter, package version, selected data root, script name, stdin parameters, exit code, JSON stdout and separate stderr. Do not include secrets or full customer configurations in a bug report. A successful process exit is insufficient: inspect the result's `status` or `ok` and the actual returned data/artifact.

## Installation and discovery

| Symptom | Check and next step |
| --- | --- |
| `doctor` is not OK | Run it from the absolute venv interpreter; inspect missing modules/resources and reinstall the reviewed distribution there |
| Skill not visible | Confirm project/user scope, harness discovery path and actual active configuration; check duplicate registrations and refresh the harness |
| `unowned destination`, edited file or symlink refused | Preserve and inspect the exact destination; move only that conflicting skill outside discovery after review, then rerun install |
| Interpreter path no longer exists | Reinstall bindings from the replacement venv at the same scope root; installing the wheel alone does not refresh copied skills |
| Pi custom user directory not found | Installer user scope targets `.pi/agent/skills`, not `PI_CODING_AGENT_DIR`; use project scope or configure the host explicitly |

There is no force-overwrite or uninstall CLI. See [installation](installation.md) for ownership and manual removal boundaries.

## Input and import

On `validate_bundle` `ok: false`, stop and inspect errors. A content SHA-256 mismatch requires a correct source bundle, not bypassing validation or editing its hash to match damaged content. Validation expects a canonical directory; use the [importer](skills/importer.md) survey flow for other supported formats and carry forward its returned prepared path.

Import can land raw text without producing structured rows. Read `parse_report`, `report_path`, unparsed reasons and devices without structured data. Distinguish intentionally raw-only commands, no registered parser and parser errors. Do not start a second ingest while the first is running; allow a suitable host timeout for large captures. Check actual `db_path` and snapshot ID before querying.

## Queries and evidence

- **Missing table/view:** inspect the relevant context/schema and parser coverage. A protocol not collected is a gap; it is not a healthy empty dataset.
- **Wrong or unexpectedly old data:** verify `OLAV_HOME`, CWD and inherited path overrides before Python starts. Use the actual import-returned snapshot ID.
- **`ambiguous_snapshot_time`:** inspect collection provenance and choose an explicit authorized snapshot. Sorting names or replacing unknown time with import time changes the question.
- **Preview has one row but two were expected:** inspect `has_more`, `next_offset`, cell/byte truncation and effective limits. Page the same stable query or narrow its projection. An unknown total must stay unknown.
- **`needs_sql_generation`:** `query` alone returns schema; supply reviewed `sql`. This package does not generate SQL through a model endpoint.
- **Date evidence is empty:** inspect `date_scope`, exclusions and unknown-time candidates. A past-the-end page is also different from no matches.
- **External SQL access or mutation refused:** use the supported read path. `requires_approval` does not authorize or perform administration.

## Artifacts and parser candidates

Check a diagram's returned `status`, host/edge count and actual file path. `too_wide` requires a narrower scope. An omission comment from a converter is not a successful diagram. Missing permissions can affect output even when reads work; choose an authorized writable output/CWD.

`finish_learn` `invalid` freezes nothing. Review diagnostics, add varied samples and retry the candidate. A successful one-sample TextFSM parser becomes live immediately and can overfit; low-sample Python candidates can be quarantined. See the [parser workflow](workflows/parsers.md).

Before reporting a product defect, reproduce against a new synthetic root. Include the smallest authorized input and expected semantic result. Do not repair a production database or legacy venv in place just to diagnose a documentation example.
