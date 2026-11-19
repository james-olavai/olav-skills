# Data readiness

This is an **unreleased development candidate**. Published `olav-skills==0.1.1`
does not provide `doctor --data`; use a reviewed source build containing this
feature. Installing the published version does not enable the commands below.

`olav-skills doctor` remains a fast dependency/resource check. It does not open a
project database. Explicit data inspection uses a separate, lazy-loaded path:

```sh
olav-skills doctor --data --project /absolute/authorized/project
olav-skills doctor --data --project /absolute/authorized/project --snapshot-id exact-import-label
# Equivalent explicit host binding:
OLAV_HOME=/absolute/authorized/project olav-skills doctor --data --snapshot-id exact-import-label
```

`--project` takes precedence over `OLAV_HOME`. A project must be an existing
absolute directory; symlink path components are refused. There is no cwd,
config, dotenv, another-project or warehouse fallback. The only database is the
selected project's `.olav/databases/main.duckdb`. A missing file stays missing;
this command never initializes or repairs a project. Do not point it at data
you are not authorized to inspect.

Omit `--snapshot-id` for supported catalog capabilities and current inventory:
snapshot-scoped record checks remain `snapshot_not_selected`. For snapshot
checks, supply an exact label from the import receipt. `latest`, `newest` and
`current` aliases are refused. The bounded label sample comes only from
`parsed_outputs`, in lexical order; it is neither an exhaustive catalog nor
collection-time ordering. A clipped label is not a valid replacement for the
original import label.

## What is checked

The versioned JSON contract is `olav.data-readiness/v1`. `status` describes
execution; `data_readiness` describes recorded query capability. Exit 0 means
the inspection completed, including missing/empty data. Exit 2 means validation,
database, timeout, memory, external-access or output-budget failure. Neither
exit 0 nor `recorded_queries_available` certifies collection completeness,
protocol health, current network state or every analysis operation.

| Source | Reported scope |
| --- | --- |
| `netops.devices` | Current inventory rows, distinct non-null hostnames and null identities; intent, not snapshot membership |
| `netops.parsed_outputs` | Exact-label row count and distinct observed device identities; not the full collected estate |
| `netops.raw_output_store` | Current last-writer record count across labels; no snapshot-history inference and no raw bodies |
| `netops.topology_links` | Current retained links filtered by exact last-writer label; not complete historical topology |
| `netops.bundle_ingests` | Exact-label metadata records and qualified `collected_at` range; not per-device/command time coverage |
| Canonical interface/BGP views | Available columns and exact-label visible record counts in current projections; old-label empty results do not prove absent historical source records |

The fixed view list is `v_show_ip_interface_brief_auto`,
`v_show_interfaces_terse_auto`, `v_show_ip_bgp_summary_auto` and
`v_show_bgp_summary_auto`. Other catalog entries and topology protocols are not
checked by this initial doctor. Missing source, unsupported required columns,
empty visible records, omitted snapshot and query failure have separate states.
Present views do not prove a command was collected or parsed; an empty view
never proves no protocol sessions. Collection and parsing coverage stay
`unknown`. Time ranges use typed bundle metadata only; missing, invalid or
unsupported timestamp types remain unqualified. Naive timestamps retain their
missing timezone qualification. Ingest times and snapshot labels never become
capture times.

## Bounds and next steps

The worker has a total 5-second deadline, read-only DuckDB access, 128 MB memory,
one thread, no disk spill, and disabled external file/network access and
extension auto-loading/installation. It runs fixed `SELECT` statements with
bound label parameters, returning at most 32 columns per supported source,
20 label samples, 256 characters per catalog cell and 24,000 UTF-8 response
bytes. Truncation is explicit; a timeout/resource/output error does not certify
a partial capability report. No raw CLI body, parsed JSON payload or capture
file is fetched. The host still supplies filesystem authorization; this command
is not a substitute for an OS permission boundary.

Follow `next_steps` and the existing import result's `parse_report`,
`report_path` and `learnable_commands` to investigate parser gaps. Doctor does
not scan reports or create a second parser ledger. For missing canonical views
or retained topology, inspect the authorized import evidence and the relevant
query's own scope before choosing an analysis. For missing data, perform a
separately authorized offline import; doctor itself only reads.
