# Explicit evidence readiness

## Choose the time policy before invoking

This checker evaluates a supplied policy; it cannot establish that the user
requested that policy. Both `as_of` and `max_age_seconds` must come from the
user's explicit request or a genuinely configured, authorized host policy.
When either is missing, ask for it before running the check. Do not substitute
capture/ingest time for as-of, treat the sample request as a default, choose an
arbitrary age, or run unrelated exploratory queries to avoid clarification.
For an explicit current-time request, use a trusted host clock if available;
a model's remembered date is not a clock. A ready result for invented inputs
is not an answer to the user's request. State readiness only for the supplied
checklist and policy, preserving its non-approval limitations.

The host must authorize
execution and set `OLAV_HOME` to the selected native project. Run:

```sh
printf '%s' '{"snapshot_id":"capture-A","requirements":[{"device":"Edge-1","command":"show interfaces"}],"as_of":"2026-10-01T00:01:00Z","max_age_seconds":60}' | olav-skills run analyzer check_evidence
```

The request has exactly four required keys. `snapshot_id` is an exact label,
never latest/current/newest/all. `requirements` is a nonempty list of at most
128 objects with exactly `device` and `command`; both are binary exact text.
Duplicate exact pairs are rejected, never silently removed. Command case and
spacing are significant, including VRF arguments. No inventory discovery fills
or changes this denominator. `as_of` is an explicit timestamp with timezone;
`max_age_seconds` is an integer from zero to 315576000. There is no default clock
or 24-hour policy.

The reader pins a native historical `netops.ingest_sources` source version.
Every selected raw record must have a matching exact stored key and
`source_json.raw_artifact` (table/snapshot/device/command), plus an
`actual_raw_sha256` matching the stored raw bytes. Wrong provenance or duplicate
selected raw/parsed sources fail the operation. No last-writer fallback is used.
Missing ledger/table evidence, including a snapshot with no ledger rows, is
unknown. Once the exact snapshot has ledger coverage, missing requested pairs
are missing. The 0.2 native importer writes historical ledger
rows for new imports transactionally; older imports without this capability
remain unknown even after a later import creates the ledger table. No backfill
or historical provenance manufactured from retained last-writer raw is used.

Parsed evidence is read separately from historical `netops.parsed_outputs`.
`nonempty` means a stored nonempty JSON list of nonempty objects only; it does
not verify parser correctness, completeness or association beyond the exact
stored snapshot/device/command. Empty arrays are `empty`; NULL, invalid JSON or
unsupported shapes are unknown. Raw presence never proves parsing succeeded.

Freshness requires known timezone timestamp evidence from `command_header`,
with exactly one identical header entry in `capture_evidence`. Conflicting,
invalid or partial capture evidence stays unknown. Manifest/device metadata,
date-only values, naive times, ingest/update clocks and snapshot names cannot
supply this command clock. Future times are unknown (possible clock skew), not
fresh. Age equal to the limit is fresh; larger ages are stale. This checks
stored clock consistency, not device clock accuracy or source authenticity.

`effective_request` returns the validated four fields (`as_of` is UTC ISO),
`source_version` pins the database hash, and every requirement is retained in
input order. Per-item `readiness` is `ready` only for verified nonempty raw,
nonempty parsed records and fresh clock; known missing/empty/stale evidence is
`not_ready`; other gaps are `unknown`. Overall `not_ready` takes precedence over
`unknown`. Summary readiness counts partition the explicit requirements;
`raw_verified`, `parsed_nonempty`, `fresh` are separate overlapping dimensions.

Readiness applies only to this declared checklist and these stored records.
`collection_completeness`, `parsing_success`, and `health_assessment` remain
unknown; source authenticity is not certified. A ready checklist is not an audit
approval, full-network inventory or live health result.

Linux native backend only. Budgets: 32KiB request, 256KiB response, 256MiB
source copy, 512KiB per raw/parsed payload, 64KiB provenance, 16MiB total selected
text, 15-second parent deadline. Bounded reads, lock/WAL rejection, read-only
private snapshots, final source verification, clean worker environment and
explicit default-reader close happen in a dedicated exec worker. Failures never
return a claimed complete subset. Caller connections and locks are untouched.

The dedicated Linux worker initializes a single-thread in-memory DuckDB default
connection before its first public DuckDB import, then closes normally after
source verification. This initialization is restricted to the actual worker
exec entry; Python callers and preloaded connections are never replaced.
Required APIs and effective settings are checked; unsupported initialization
fails closed. Validation covers DuckDB 1.5.6, not future versions or every native
reader in this package.
