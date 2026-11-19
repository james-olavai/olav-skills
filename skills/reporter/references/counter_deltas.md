# Recorded interface counter comparison

Use `compare_counters` for recorded Cisco IOS `show interfaces` cumulative errors, CRC errors and queue drops. Use the report scope to distinguish matched interfaces from observations present on one side. A single captured reading can show a cumulative count; a difference requires two readings.

```bash
printf '%s\n' '{"snapshot_id_1":"snap_before","snapshot_id_2":"snap_after","device":"edge-01","interface":"GigabitEthernet0/1"}' |
  olav-skills run reporter compare_counters
```

Supply distinct exact snapshot IDs in the requested subtraction order. Optional `device` and `interface` match stored labels byte for byte. Omit these filters to compare every recorded interface in scope. `limit` controls sample count from 1 to 50, default 20. The caller supplies the authorized native project through `OLAV_HOME`.

| Counter field | IOS reading | Unit |
| --- | --- | --- |
| `input_errors` | Input errors | errors |
| `crc` | CRC errors | errors |
| `output_errors` | Output errors | errors |
| `queue_drops` | Input queue drops | packets |
| `queue_output_drops` | Total output drops | packets |

Report each counter on its own. Error categories can overlap. `delta_status: known` reports the second reading minus the first. `possible_reset_or_wrap` indicates a decrease with ambiguous cause. `confirmed_discontinuity` cites an observed IOS restart or interface counter clear during the interval and leaves delta and rate null. Missing or invalid values have `delta_status: unknown`. A recorded zero is a valid observation.

Check `rate_status` independently. Rates require two verified, timezone-aware command capture instants and a positive interval. With the normal native bundle import, historical counters can be present while command-level capture times are unavailable. A SID or import time supplies no rate evidence. Cite the snapshot pair and a matching sample when reporting a delta. Use `counts` for totals even when `has_more` indicates clipped samples.
