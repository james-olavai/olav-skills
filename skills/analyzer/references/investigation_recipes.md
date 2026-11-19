# Recorded investigation recipes

Use `olav-skills run analyzer run_investigation` with one stdin JSON object and
an authorized absolute `OLAV_HOME`. The entry point is available in 0.2.

```json
{"recipe":"interfaces","snapshot_id":"capture-a","devices":["R1"],"limit":10}
```

Required: recipe inventory/interfaces/bgp and exact snapshot_id. Optional:
devices/vendors (<=50 exact strings each, <=256 characters), limit (integer
1..50, default10). Empty filters discover in the bound native project. SQL,
database selectors, warehouse backend, latest/current/all aliases and unknown
arguments are rejected. The stdin cap is 16KiB; the Python API retains its
separately bounded larger object budget. Both use a disposable reader worker.

## Recorded scopes and states

Inventory separates current catalog metadata from observed snapshot members.
`observed_without_catalog_count` and its bounded name sample identify captured
members absent from the current catalog. Without vendor filters, protocol and
snapshot record counts include such uncatalogued members; vendor filtering uses
known **current** catalog assignments and explicitly excludes unknown vendors.
It does not establish historical platform, vendor or role.

Read [native contracts](../../netops/references/native_contracts.md) for the
shared IOS/Junos state rules and default parser coverage. IOS interfaces use
status and line proto; Junos uses admin_state/link_state while proto is family.
Known non-up values such as admin-down are nonhealthy observations, including
when the other component is unknown. Only known up/up is healthy. Remaining
states stay unknown. Numeric BGP prefix counts imply Established only in the
state_or_prefixes_received profile; native Junos state uses known text. Native
peer_ip/state and explicit compatible field pairs are supported, but two
complete Junos profiles are ambiguous. Default native Junos BGP and terse
parsers now have verified synthetic import coverage; actual device collection
and other unsupported layouts remain separate evidence.

Counts separate healthy_record_count/nonhealthy_record_count/unknown_record_count.
These are records, not necessarily distinct sessions/interfaces: multiple
families or addresses can yield multiple rows. Non-up does not prove outage.
Rows sample nonhealthy and unknown records, clip cells at256 characters and
carry has_more. Unsupported schemas fail; no supported protocol view yields
an unknown step with counts null. Canonical views may retain only recent
records, so empty historical selections do not prove collection absence.
Collection coverage and live state remain unknown. Date-shaped IDs are only
snapshot_label_inference; without capture evidence collected_at stays unknown.

## Source version and limits

A dedicated exec reader, shared by CLI and Python entry, holds a Linux LP64 OFD
read lock while a checked native database is copied from its verified descriptor
to a parent-owned 0700 private scratch. DuckDB actually opens that copy read-only,
with external access and spill disabled. Hash, bytes, source/copy identities and
WAL are checked before result delivery. A `/proc/self/fd` path alone does not
pin DuckDB's actual open target. Runtime assumes a trusted local filesystem,
stable trusted temporary roots and writers obeying DuckDB locking; it does not
authenticate data authors or withstand malicious direct filesystem rewrites.
The low-level copy helper belongs only in a dedicated exec reader without
preexisting source DB handles; a public read must not disturb caller writer locks.

The parent owns the whole scratch lifetime and hard five-second deadline after
input read, kills/reaps its worker and clears copies on local timeout. Host
transport and host crashes are separate. Engine256MiB/one thread, DB256MiB,
output32KiB/cells256 characters, bounded schema and no persistent custom macros
or explicit COLLATE DDL. Scope/protocol columns require standard VARCHAR types.
Over-budget results fail, not silently drop steps. The response binds actual
DB/schema hashes, effective input, recipe/executor/state-contract hashes,
package version, sources and temporal metadata. These establish reproducibility,
not a signature, full capture coverage or current network truth.

These fixed recipes do not learn model answers, cache responses, supply a
harness or automatically register additional OpenWebUI tools.
