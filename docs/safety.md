# Permissions, writes and evidence boundaries

Run the package with the filesystem, environment and execution permissions appropriate to the task. Standard skill text and installer metadata are instructions and ownership records; they are not operating-system isolation or a per-user authorization system.

## Know which operation writes

| Operation | Expected effects |
| --- | --- |
| `version`, `list`, `doctor` | Package/environment inspection |
| `install`, standard `export`, portable export | Write skill or knowledge files to the selected destination |
| `validate_bundle` | Read canonical bundle and check integrity; no database import |
| `survey_bundle` | May extract or normalize input into a new prepared location |
| `ingest_snapshot` | Write analysis database, derived state and import reports |
| Bounded SQL reads | Read-only SQL; optional `export_bounded` can write a preview CSV |
| `draw_topology` | Read scoped relationships and write a diagram |
| `finish_learn`, `save_recipe`, `rebuild_views` | Persist parser/recipe or rebuild derived database views |

This table describes intended paths, not a sandbox around all Python code. A trusted script runs with its process permissions. Limit mounted data and output roots in your host, keep credentials out of the child environment, and use an authorized copy for experiments. Set `OLAV_HOME` before Python imports. Also choose a deliberate working directory: some relative artifact paths resolve from CWD.

## SQL is a bounded reader

Use `analyzer execute_sql` instead of opening your own administrative connection for model-generated queries. It accepts SELECT/WITH reads through a read-only connection, limits rows/cells/response bytes/time and refuses external file/network access through SQL. Mutating statements return `requires_approval` without execution; that status does not supply an approval endpoint or execute the statement after a chat reply. The CLI does not offer general SQL administration.

Projection matters: select the exact fields needed, use bound `params` for values and preserve a stable order when paging. Do not dump entire raw configurations. `export_bounded=true` only exports the returned bounded preview in the applicable truncated-result path; it is not a complete export guarantee.

## Treat input and output as sensitive

An offline capture can include credentials, addresses and customer information. Use authorized transfer/storage and review redaction before sharing. The manifest hash establishes byte consistency, not sender identity, safe content or collection authorization. A declared `pre_scrubbed` flag does not prove all secrets are absent. Parser candidates are code/data requiring review; sample success does not prove general correctness.

Reports and diagrams inherit the sensitivity of their sources. Check artifact contents and destination access before publishing. Portable exports carry package knowledge, not an access grant to your database or the host's tools.

## Keep conclusions inside the evidence

State the selected snapshot, collection-time provenance, source views and limitations. Unknown role/time is not zero. Missing topology coverage is not absence of a protocol. A graph-based impact calculation is not packet reachability simulation. A draft plan requires engineering review and current-state checks before any application; the package does not apply device configuration.

Model instructions cannot replace filesystem controls or host authorization. Harness-specific approvals, multi-user identities, tenant isolation and service operations are separate integration responsibilities. See [data semantics](data-model.md), [portable boundaries](portable.md) and [compatibility](reference/compatibility.md).
