# Script catalog and stdin/result protocol

Use `olav-skills list` from the bound venv as the installed inventory. Version 0.1.1 exposes these public entry scripts; shared names under different skills can be wrappers around the same domain capability.

| Skill | Script basenames |
| --- | --- |
| analyzer | `describe_table`, `execute_sql`, `generate_change_plan`, `inspect_blast_radius`, `inspect_devices`, `inspect_interfaces`, `query_batch`, `search_logs` |
| importer | `discover_platform`, `ingest_snapshot`, `survey_bundle`, `validate_bundle` |
| learner | `finish_learn`, `prepare_learn` |
| netops | `diff_configs`, `diff_snapshots`, `inspect_blast_radius`, `query_batch`, `read_change_plan`, `search_commands` |
| reporter | `describe_table`, `diff_snapshots`, `query_batch`, `query_evidence`, `read_change_plan` |
| topology | `list_recipes`, `prepare_recipe`, `query_batch`, `query_topology`, `rebuild_views`, `save_recipe` |
| writer | `draw_topology`, `render_topology_drawio`, `render_topology_mermaid`, `render_topology_svg`, `topology_view_filter` |

## Wire convention

Pass **one JSON object** on stdin to `olav-skills run SKILL SCRIPT`. Keep stdout and stderr separate. Most scripts print a JSON object; the three render converters print a JSON **string** containing markup or an omission note. Decode JSON before saving those artifacts. Results and exit behavior are script-specific: `validate_bundle` can exit zero with `ok: false`, and converters can exit zero with an omission comment. `execute_sql` and `query_evidence` return nonzero on their error paths; snapshot-diff argument errors use exit 2 and execution failures use 1. Check both the response and exit status.

`{}` is not a universal valid request. Required fields depend on the script. Empty optional lists often mean discovery within the selected scope, not “select nothing”. Do not pass arbitrary database/project paths to bounded batch operations.

## Bounded batch operations

Common inputs: `operation`, explicit `snapshot_id`, optional exact case-sensitive `devices`, `vendors`, `commands`, and `limit` (1–50, default 10). Lists accept at most 50 values of up to 256 characters. Unknown or inapplicable keys are errors. No SQL, arbitrary table or `db_path` parameter is accepted. `OLAV_HOME` must be bound explicitly.

| Operation | Result and important boundary |
| --- | --- |
| `context` | Relevant whitelisted source/column presence, no answer rows/counts; no data filters |
| `snapshot_summary` | Inventory devices, observed devices, distinct non-null commands and records |
| `device_coverage`, `command_coverage` | Aggregate coverage by vendor or command; preserve units |
| `devices`, `commands` | Bounded metadata rows; inventory and captured command scopes differ |
| `multi_device_summary` | Per-device records/commands/status, including requested zero matches |
| `top_n` | `group_by` device/command/vendor; `metric` device_count/command_count/record_count |
| `diff_summary` | Two explicit IDs; parsed/command/topology/OC identity-key sets, not semantic diff |
| `interface_health`, `bgp_health` | Supported canonical views only; missing source is a failure, not healthy emptiness |

Metadata row operations can select nonempty supported `fields`, `order_by` and `order`. Stable tie-breaks are appended. Top N uses its metric/order; do not add conflicting ordering. Protocol health does not accept command filters, and OSPF health is unsupported. Templates should not silently discard constraints from a user's question.

Rows are capped, cells may be clipped at 256 characters and the normal batch envelope is capped at 12000 UTF-8 bytes. Aggregates can be exact over scope while rows are only samples. Read `has_more`, byte truncation, effective limits, count scope/unit and actual physical sources. There is no general batch `offset`; narrow filters when needed. Compare source/target temporal metadata independently.

## Bounded SQL

`execute_sql` accepts `sql`, optional bound `params`, `max_rows`, `max_cell_chars`, `max_total_bytes`, `timeout_ms`, `offset`, `export_bounded`; `query` without SQL returns schema for a caller to generate SQL. `explain_only` requests schema only. `describe_table` instead accepts `table_name` and optional `include_samples`.

Library upper ceilings include 1000 rows, 8000 characters per cell, 1000000 response bytes, 60000 ms and 65536 SQL bytes. The entry point can impose lower tier/context caps. Requested larger values do not remove these limits; inspect returned `budget` and result-contract limits. Row fetch bounds do not limit the entire engine scan or host RSS.

`data`, `returned_rows` and `returned_bytes` describe the page. `has_more`, `next_offset`, `truncated`, `cell_truncated` and `budget_exceeded` describe incompleteness. `count`/`total` remain null when not fully observed; a page after offset zero does not establish a complete total. Keep stable ordering, filters and snapshot when paging. Cell clipping needs a narrower field extraction, not another page. A timeout or byte error is not an empty success. CSV output, when requested and produced, contains only the returned bounded preview.

## Time and evidence

`temporal` separates collection, ingestion, record update and event clocks with precision/source/status. Arbitrary SQL time scope is unknown; do not transfer snapshot-time claims to an unrelated SELECT. Different convenience scripts retain different default snapshot selectors. Use explicit IDs.

For `query_evidence`, inspect match/total completeness, paging, excerpt bounds and date exclusions. Unknown-time candidate exclusions occur before body matching and are not match counts or a denominator to add to other counts. `no_dated_candidates` can leave a date question unresolved. See [reporter](../skills/reporter.md) for detailed date behavior, and each [skill page](../index.md#choose-a-skill) for domain parameters and write effects.
