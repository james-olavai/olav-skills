# One-time audit profiles (unreleased)

Use `olav-skills run reporter run_audit` from the installed bound interpreter.
This candidate is not in published 0.1.1. The host supplies an authorized absolute
`OLAV_HOME`; no project/database or arbitrary command selector is accepted.

```json
{"profile":"baseline","snapshot_id":"capture-a","devices":["R1"],"limit":5}
```

`operation` defaults to `run`. `{"operation":"list"}` alone lists definitions
without reading data or requiring a project. Builtins: `baseline` (all three
checks), `inventory`, `interfaces`, `bgp`, and `ospf`. Supply an exact SID, not latest/current/newest/all.
Optional devices/vendors are exact strings (<=20 each, <=256 characters);
empty lists discover in the selected project. `limit` is 1..5, `timeout_ms`
1..18000, default 18000 for the entire execution after input/profile reading.
Combined input must fit 8KiB. No scheduler, SQL or model workflow is supplied.

The fixed checks are `inventory_membership`, `interface_states`, `bgp_states` and `ospf_states`.
Inventory/interface/BGP invoke analyzer's [recorded investigation](../../analyzer/references/investigation_recipes.md)
with the same exact input. Native/source limits and unsupported shapes carry
through. All successful rules must report the same actual database SHA-256;
a mismatch withholds the aggregate. The profile and executor hashes, effective
request, per-rule evidence, actual source versions and capture-time provenance
remain available. No database or source artifact is changed.

- Inventory membership checks observed devices absent from the **current**
  catalog. A pass does not establish that every inventory device was collected,
  nor that catalog metadata describes historical platform/role/vendor.
- Protocol checks compare the known nonhealthy record count to the explicit
  `max_concerns` policy. Missing/empty sources and unknown states do not pass.
  Known concerns are retained alongside unknown records. These are record
  counts, not necessarily unique interfaces, sessions or confirmed outages.
- `pass` means the observed check met that rule's policy. `concern` means it
  exceeded the policy. `unknown` means insufficient usable evidence.
  `not_executed` records a deadline omission.
- A capability error or unfinished execution gives `status=partial`,
  `completed=false`, `overall=unknown`; prior per-rule concerns remain visible.
  Otherwise overall is concerns/unknown/clear_for_observed_checks. Global live
  state and collection coverage always remain unknown. Exit 0 means checks
  executed successfully, including concern/unknown business outcomes. Partial
  or error exits 1; neither proves a healthy network.

For a reviewed custom policy, the host can create
`.olav/audit_profiles/team.json` inside the project, then use `profile_path`
instead of `profile`. File and ancestor symlinks, hardlinks and files >16KiB
are rejected. Required exact JSON shape:

```json
{"schema":"olav.audit-profile/v1","id":"team","version":"1","rules":[{"id":"ports","check":"interface_states","severity":"warning","max_concerns":0}]}
```

There must be 1..3 distinct checks and unique IDs. No additional fields, SQL,
Python or dynamic query templates are accepted. Severity is info/warning/critical,
and max_concerns is an integer 0..1000000. Severity and tolerance are the chosen
review policy, not independently established incident impact. File authorship still requires separate evidence. Freshness is only checked
when the explicit evidence policy below is supplied.

Linux capabilities execute in a fresh owned process group; the parent owns
TMPDIR and cleans copies after local deadline cancellation. Input/transport
timeout remains a host responsibility. The output is capped at 64KiB, each
sub-capability at its own bounds. Do not automatically retry failures or raise
limits. To deliver a file, an authorized host writer can save and review this
JSON/its report in the selected [evidence-bundle](evidence_delivery.md) staging
area, then explicitly package it. This script does not auto-write reports,
append findings to databases, register new WebUI tools or grant permissions.

## Explicit OSPF observations

The `ospf` profile invokes analyzer `analyze_ospf` in inspect mode using the
actual exact SID, filters, bounded limit and remaining deadline. Full observations
can satisfy this selected rule; transitional observations count as items for
review. 2-Way, unknown state, source gaps or missing routing context do not
become network-health approval. Missing requested sources remain unknown even
when a partial summary contains only Full. See
[OSPF analysis](../../analyzer/references/ospf_analysis.md). The default
`baseline` still runs inventory/interface/BGP only; custom policies retain
the limit of three distinct checks.

## Optional declared evidence gate

```json
{"profile":"ospf","snapshot_id":"capture-a","devices":["R1"],"evidence_check":{"requirements":[{"device":"R1","command":"show ip ospf neighbor"}],"as_of":"2026-11-18T12:00:00Z","max_age_seconds":300}}
```

`evidence_check` accepts exactly requirements/as_of/max_age_seconds. The parent
binds its own exact snapshot, checks the actual child's effective request, schema,
requirement denominator and database hash, and counts its time inside the
whole audit deadline. A non-ready/unknown/failed precheck skips the rules with
partial/unknown; it does not retry, recollect or broaden the checklist. All
subsequent rule hashes must match the precheck. Requirements outside a nonempty
audit device filter are rejected. Vendor filters with this gate are rejected,
because no historical vendor mapping is inferred for the caller's checklist.

The caller defines the checklist's sufficiency; a ready selected list is not
complete network collection, parser correctness, source authenticity or health.
Without this optional policy, no denominator or age threshold is invented.
The native candidate's official new imports retain historical raw/clock binding;
old missing ledger history is not reconstructed and may remain unknown. Read
[evidence readiness](../../analyzer/references/evidence_readiness.md).
