# Recorded metric baselines

Unreleased source candidate; published `olav-skills==0.1.1` does not include this
entry point. The host must authorize the selected native project.

```sh
export OLAV_HOME=/absolute/authorized/project
printf '%s' '{"baseline_snapshot_ids":["h0","h1","h2","h3","h4","h5"],"target_snapshot_ids":["t0","t1"],"device":"Edge-1","interface":"GigabitEthernet0/1","profile":"input_errors"}' |
  olav-skills run analyzer analyze_baseline
```

Supply all five keys and exact stored device/interface labels. Supported IOS
`show interfaces` profiles are `input_errors`, `crc`, `output_errors`
(errors/second), `queue_drops`, `queue_output_drops` (packets/second). Each rate is
a recorded cumulative-counter net increase divided by its observed interval;
it does not certify the true event rate or continuous counters.

Baseline SIDs are an ordered list of 2–32 distinct exact IDs. At least six
snapshots (five adjacent intervals) are required for statistics. Two separate
target SIDs must not occur in the baseline. Capture order uses known, timezone
aware command-level stored clocks, never SID text, import time or lexical sort.
All baseline intervals must be positive and have equal duration. Missing data,
clock conflict, missing clock, unsupported platform, observed restart/clear or
counter decrease makes the interval unknown; any such baseline interval prevents
statistics. Different capture intervals prevent statistics too. No unknown
interval is silently removed. Stock bundle import may have counters but no
command clock and therefore returns unknown rates.

The complete selected interval set produces minimum/maximum, arithmetic mean,
median and nearest-rank P90/P95 (one-based rank `ceil(p*n)`). With five intervals,
both percentiles are the maximum; this is a small sample reference, not a stable
population-tail estimate. The P95 suggestion remains `not_approved` and creates
no alert configuration or schedule. Target comparison additionally requires its
start after the last baseline clock and equal interval duration. Invalid target
evidence leaves target comparison unknown while valid baseline statistics may
remain. A valid target is `above_reference` or `at_or_below_reference`; neither
means healthy or unhealthy.

Preserve `health_assessment: unknown`, `counter_continuity: not_certified` and
`unobserved_reset_possible: true`. Even an IOS clearing line saying “never” does
not rule out an unobserved device restart or reset. Collector/parser changes,
identity continuity and completeness outside stored evidence need caller review.
Counters can overlap and must not be summed as independent incidents.

Every selected interval reads the complete exact single-interface observation
from one pinned source version, never a capped fleet sample. Duplicate commands
or interfaces fail. Budgets are 16 KiB input, 64 KiB output, 32 baseline SIDs,
1 GiB source, 512 KiB command, 32 MiB selected data per pair and 15 seconds.
Unknown results can have successful execution; inspect semantic status too.

The Linux native reader uses a locked private copy, final source recheck and a
supervised dedicated process. It preserves caller connections and writes no
source data. Trusted local filesystem/temp root, lock-respecting writers and
truthful authorized stored records/clocks are assumptions; a source hash is not
authenticity certification. Warehouse, real collection, model/harness routing,
browsers and production deployment are outside this entry's validation.
