# Change-plan checks

In 0.2, run `olav-skills run analyzer check_change_plan` using one stdin JSON object,
an explicit authorized `OLAV_HOME` (or `project`) and `snapshot_id`.
The project must already be host-authorized. Snapshot aliases latest/newest/auto
are rejected; labels do not establish collection time or current state.

Provide exactly one `plan` or project-relative `plan_path` under
`exports/change_plans/` (no symlinks/traversal). A `.json` file uses the structured
schema; other files are Markdown. Inline Markdown uses a string `plan`.
Structured plan schema is `olav.change-plan/v1`, with unique `targets`:

```json
{"snapshot_id":"capture-a","plan":{"schema":"olav.change-plan/v1","targets":[{"device":"edge-a","platform":"cisco_ios","template":"cisco_ios","steps":["description planned"],"rollback":["description prior"],"verification":[{"command":"show interfaces","expected":"description matches reviewed intent"}],"addresses":[{"address":"192.0.2.9/24","vrf":"blue"}]}]}}
```

This synthetic structure is not a validated CLI procedure. Steps and rollback
are nonempty string lists. Every verification needs nonempty command and
expected text. Optional address objects contain an IPv4/IPv6 address or CIDR
and optional explicit VRF. Bare IP supports equality checks only, not a subnet
claim. Files and directories are not created.

## Evidence and interpretation

The fixed read-only source is `.olav/databases/main.duckdb` in the selected
project, table `netops.parsed_outputs`, filtered by the explicit snapshot ID.
Actual snapshot rows establish observed membership and historical platform;
global inventory and other snapshots cannot supply those facts. Missing,
empty, schema-incompatible, failed or truncated sources remain unknown. A
target absent from a complete nonempty snapshot result fails membership.
The cisco_ios template check recognizes identity only, not CLI correctness;
other templates stay unsupported/unknown.

Markdown receives supported placeholder and old-generator heading/Rollback/
Post-checks marker checks only. Section presence cannot prove per-device
coverage or semantic validity. Target/platform/address/rollback/verification
semantics remain unknown. The generator remains a Cisco IOS placeholder draft
with role sorting, not graph BFS or a multiple-vendor implementation.

Address observations consume parsed object lists only for show ip interface
brief, show ipv6 interface and show interfaces terse, using explicit
canonical ip_address and optional prefix_length/vrf fields. TextFSM Tier 1
and NTC lower-case headers. Exact upper-case IP_ADDRESS/PREFIX_LENGTH/VRF
aliases are explicit compatibility only; other spellings/layouts are not
inferred. Conflicting aliases or a prefix contradicting embedded CIDR exclude
the record and produce address_source_fields unknown with the field reason.
Missing parser fields are not reconstructed: native IOS interface-brief bare
IPs establish neither subnet nor VRF. Equality and known-prefix intersections are compared
against source observations and earlier proposed addresses.
An address_overlap pass means an intersection was detected, never a confirmed
conflict or safety. Unknown VRF is not default VRF. Different VRFs, anycast,
intended reuse, missing collection coverage and time affect interpretation.
No matches do not prove conflict-free source data. Down/unassigned interfaces
do not prove free ports.

Every `olav.plan-precheck/v1` result is `not_approved`. Exit 0 means execution
succeeded; check `checks_outcome`, individual pass/fail/unknown values, reasons,
evidence and source errors/truncation. Invalid requests, paths and exceeded
plan/output limits return execution_status error with exit 2. CLI syntax,
reachability, simulation, collection time and approval are not provided.
No plan commands, models or network calls run; no business database writes occur.

## Fixed limits

Input 96 KiB; plan 64 KiB; targets 32; total steps/rollback/verification entries
256; addresses per target 32; findings 512; output 256 KiB. Fixed parameterized
SELECT uses existing read-only SQL budgets: 1,000 rows, 4,000 characters per
cell, 256 KiB query preview, 5-second timeout, 256 MB DuckDB memory setting,
one thread. Address observations cap at 1,000. Fetch caps do not bound scan
work or host RSS. Source errors/truncation do not become empty success.
OLAV_SQL_TEMP_DIR is refused to avoid write-capable spill-directory setup.
Callers cannot raise these limits. This does not establish harness routing or
provide a new Open WebUI tool.
