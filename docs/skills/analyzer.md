# analyzer: bounded analysis and plan preparation

Analyzer exposes recorded metadata, bounded SQL, device/interface inspection, local log search and change-plan preparation. It does not run a model endpoint or apply device configuration. Give it a clear evidence question and stop when the available data answers it or establishes a gap.

## Prefer a matching template

`query_batch` supports snapshot statistics, device/command coverage, filtered devices/commands, multi-device summaries, top N, key differences and selected interface/BGP health checks. Start with `{"operation":"context"}` to inspect capabilities, then choose an operation and explicit snapshot. Unsupported intent remains unsupported: do not discard a date/vendor/device filter merely to fit a template.

For native snapshot-scoped device and interface questions, prefer these templates or bounded SQL. The legacy-style `inspect_devices` and `inspect_interfaces` scripts remain in the pack, but their native local paths do not support arbitrary explicit snapshot selection: the former rejects a supplied `snapshot_id`, the latter rejects values other than `latest`. Interface inspection can choose a latest record per device. Those conveniences must not be described as a single historical capture.

## SQL is explicit

With `OLAV_HOME` pointing to the synthetic home:

```sh
printf '%s\n' '{"sql":"SELECT hostname, role FROM netops.devices ORDER BY hostname","max_rows":10}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer execute_sql
```

Expect R1 and R2 with unknown roles. This query is inventory, not a collection-time assertion. A `query` without `sql` returns `needs_sql_generation` and schema context; a host or human writes the SQL. `describe_table` accepts `table_name` and optional `include_samples`; use relevant schema only. Projection, bound `params`, stable ordering and [result limits](../reference/scripts.md) matter.

Mutations are not executed. External SQL file/network access is refused. An explicit `export_bounded` writes only the applicable bounded preview, not a full dataset.

## Other deliverables

`search_logs` searches copied local logs; absence of a syslog store is a missing source. `inspect_blast_radius` evaluates removal from a recorded graph and returns exact totals separately from sampled members; it is not reachability simulation. `generate_change_plan` writes a bulk model-pattern plan under `exports/change_plans/`, accepting `model_pattern`, `output_filename`, `upgrade_description` and `bfs_order`. Review its facts, assumptions, device syntax, rollback and post-checks before using any draft.

No simulator or generic report-writing API is supplied. A host can write a reviewed report using its authorized file tools. See [inventory investigation](../workflows/inventory.md), [changes](../workflows/changes.md) and [safety](../safety.md).
