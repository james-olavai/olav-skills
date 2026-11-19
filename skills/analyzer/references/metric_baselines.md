# Recorded metric baseline (unreleased)

This development candidate is absent from published 0.1.1. Use
`olav-skills run analyzer analyze_baseline` with an explicitly authorized native
`OLAV_HOME`. The operation reads one pinned database version and writes no
source data, monitoring rule or alert schedule.

```json
{"baseline_snapshot_ids":["h0","h1","h2","h3","h4","h5"],"target_snapshot_ids":["t0","t1"],"device":"Edge-1","interface":"GigabitEthernet0/1","profile":"input_errors"}
```

All five keys are mandatory; additional keys are rejected. Device and interface
are exact stored labels, without discovery or cross-device aggregation. Profiles
are `input_errors`, `crc`, `output_errors` (errors/second), `queue_drops` and
`queue_output_drops` (packets/second). These are IOS `show interfaces` cumulative
counter **recorded net increases per second**, not certified event rates.
Error categories may overlap; do not sum them.

Supply 2–32 baseline SIDs in capture order and exactly two target SIDs. All IDs
must be distinct exact IDs, never latest/current/newest/all. The target is excluded
from all baseline statistics. Baseline statistics require at least five adjacent
intervals (six snapshots), with known timezone-aware command clocks, positive
intervals and the same elapsed seconds. Ordering comes from actual stored
clocks; dates in SIDs and ingest timestamps are not clocks. Missing source,
unsupported platform, missing counter, invalid/conflicting/nonpositive clocks,
confirmed observed restart/clear or decreasing counter makes the interval
unknown. All selected intervals remain in output; unknown intervals are never
dropped to manufacture a usable subset. Any unknown baseline interval suppresses
all baseline statistics and threshold suggestions. A changed capture interval
also suppresses statistics. Stock native bundle ingestion can preserve counters
without command clocks; this normally yields unknown rates. No time is invented.

A baseline with sufficient evidence returns minimum, maximum, arithmetic mean,
median, nearest-rank P90 and P95 of the **complete selected interval set**.
Nearest rank is sorted values at one-based `ceil(p*n)`. Five intervals make both
P90/P95 equal to the largest value; this small sample does not estimate a stable
population tail. Output contains the interval count, cadence and units.
`threshold_suggestion` is the historical P95 reference only, with
`approval: not_approved` and no automatic alert configuration.

Target comparison additionally requires both target clocks strictly after the
last baseline clock (target start must be later), the same cadence, and a known
target rate. Otherwise `target_comparison` stays unknown even when baseline
statistics remain available. A valid target is `above_reference` only for a
strictly greater rate, otherwise `at_or_below_reference`. Neither result declares
health, predicts incidents, approves changes or deploys monitoring. Always keep
`health_assessment: unknown`, `counter_continuity: not_certified` and
`unobserved_reset_possible: true`: absence of observed reset evidence, including
an IOS “never” clearing line, cannot prove no unobserved reboot/clear/wrap.

The selected metric has a fixed command/platform/field/units and equal cadence;
unsupported source formats are rejected or unknown. This does not establish
that parser semantics, collection completeness, device identity continuity or
instrumentation stayed unchanged outside the stored evidence. The caller must
review those external comparability assumptions before using a suggestion.

The shared counter engine is called with one exact device/interface pair and
requires exactly one source per side, one matched observation, one full sample
and no sample clipping. It never computes a fleet baseline from capped previews.
Duplicate exact command/interface sources fail; no arbitrary SQL/profile code
is accepted. Limits: 16 KiB request, 64 KiB response, 1 GiB database, 512 KiB
command, 32 MiB per selected pair and a 15-second whole-operation deadline.
Source oversize and duplicate/invalid schema produce errors rather than partial
statistics. Use `status`/process exit separately from `analysis_status` and
`baseline_status`; successful execution with unknown evidence is not a usable
baseline.

Native Linux uses a dedicated exec child, cleaned environment, fixed package
path/cwd, parent-held scratch lifetime and deadline, OFD source lock, bounded
private copy, read-only SQL and final source identity recheck. All SIDs share one
pin and `source_version` hash. The reader closes its private/default DuckDB
connections after final checks and before output; abnormal exit is rejected.
The public API does not close the caller's connections. This relies on a trusted
local filesystem, stable private temporary root, normal lock-respecting writers
and truthful authorized stored records/clocks. Hash consistency is not source
or device authenticity. Warehouse backends are unsupported. Natural-language
routing, models, browsers, real collection and deployed services require
separate validation.
