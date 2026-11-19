# Recorded change-plan checks

Olav Skills 0.2 provides `check_change_plan`. This checker never executes a command or approves a change.

Use the authorized project and an explicit snapshot:

```sh
OLAV_HOME=/authorized/project olav-skills run analyzer check_change_plan <<'JSON'
{"snapshot_id":"capture-a","plan":{"schema":"olav.change-plan/v1","targets":[{"device":"edge-a","platform":"cisco_ios","template":"cisco_ios","steps":["description planned"],"rollback":["description prior"],"verification":[{"command":"show interfaces","expected":"description matches reviewed intent"}],"addresses":[{"address":"192.0.2.9/24","vrf":"blue"}]}]}}
JSON
```

This synthetic text demonstrates structure, not a reviewed CLI procedure.
`project` may replace an explicit `OLAV_HOME`. The host must authorize that
project; the argument does not grant access. The fixed local database is
`.olav/databases/main.duckdb`; no warehouse or database-path override is used.
`latest`, `newest` and `auto` are rejected. Snapshot labels do not establish
collection time or current state.

Provide exactly one `plan` or `plan_path`. Structured plans use the schema and
fields above. Targets must be unique. Steps and rollback are nonempty string
lists; each verification needs nonempty command and expected text. Addresses
are optional IPv4/IPv6 address or CIDR objects with optional explicit VRF.
A bare IP supports equality checks only: it does not establish a subnet.
`plan_path` is relative to the selected project, must start with
`exports/change_plans/`, and cannot traverse parents or symlinks. A `.json` file
uses the structured schema; other files are Markdown. Inline Markdown uses
`plan` as a string. No files or directories are created.

Markdown checks only supported placeholder markers and the existing bulk
generator's device-heading/Rollback/Post-checks markers. Those markers do not
prove per-device coverage, meaningful rollback, usable verification or any CLI
semantics. Target/platform/address/rollback/verification semantics stay unknown.
The old generator remains a Cisco IOS placeholder draft with role-based order;
this checker does not extend it to multiple vendors or graph BFS.

## Evidence and results

`olav.plan-precheck/v1` separates `execution_status` from `checks_outcome`.
Each check has `pass`, `fail` or `unknown`, a reason and scoped evidence.
`decision` is always `not_approved`; even a pass is only the named observation.
Exit 0 means the checker ran, not that the plan is safe. Invalid requests,
paths and exceeded plan/output limits return an error with exit 2.

Snapshot membership and historical platform come only from actual
`netops.parsed_outputs` rows with the requested snapshot ID. Global inventory
and records in other snapshots do not prove membership or historical platform.
Empty, missing, schema-incompatible, failed or truncated sources remain unknown;
a target absent from a complete nonempty returned snapshot fails the membership
check. The `cisco_ios` template check recognizes its identity only; other
templates remain unsupported/unknown. No CLI syntax validator is supplied.

Address observations use only parsed records for `show ip interface brief`,
`show ipv6 interface` and `show interfaces terse`, with a list of objects
containing canonical `ip_address`, optional `prefix_length` and `vrf` fields.
TextFSM Tier 1 and NTC lower-case their headers; exact upper-case
`IP_ADDRESS`/`PREFIX_LENGTH`/`VRF` aliases are supported only for compatibility.
Other spellings/layouts are not inferred. If both aliases exist and disagree,
the record is excluded and `address_source_fields` reports unknown with the
conflicting field reason. An explicit prefix that contradicts embedded CIDR
also remains unknown. Fields absent from a parser output are not reconstructed: 
for example native IOS interface-brief bare IPs supply neither subnet nor VRF.
Unsupported layouts do not become inferred addresses. The checker detects
address equality and known-prefix intersections against these records and prior
proposed addresses. `address_overlap: pass` means an intersection was observed,
**not** that a conflict or safety was established. Missing VRF is unknown,
never an assumed default. Different VRFs, anycast and intended reuse may alter
interpretation. Collection coverage and freshness are not proven; no matches
do not prove no conflicts. Down/unassigned interfaces do not prove free ports.

CLI syntax, reachability, simulation, collection time and deployment approval
remain unknown. No online systems, model calls, command execution or business
database writes are provided.

## Limits

Input 96 KiB; plan 64 KiB; 32 targets; 256 total steps/rollback/verification
entries; 32 proposed addresses per target; 512 findings; output 256 KiB.
Fixed SELECT queries use parameters and existing read-only SQL budgets:
1,000 returned rows, 4,000 characters per cell, 256 KiB query preview,
5-second timeout, 256 MB DuckDB memory setting, one thread. Fetch caps do not
bound scan work or host RSS. Address observations are capped at 1,000.
Source truncation/errors are visible and do not become empty success.
`OLAV_SQL_TEMP_DIR` is refused to avoid a write-capable spill-directory path.
Limits are fixed, not caller-increasable. This local checker supplies neither
harness routing nor a new Open WebUI tool.
