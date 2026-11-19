# Interface counter deltas

`reporter compare_counters` is available in the unreleased native candidate. It compares recorded cumulative counters from two explicit historical Cisco IOS `show interfaces` observations.

```bash
printf '%s\n' '{
  "snapshot_id_1": "snap_before",
  "snapshot_id_2": "snap_after",
  "device": "Edge-1",
  "interface": "GigabitEthernet0/1",
  "limit": 20
}' | olav-skills run reporter compare_counters
```

Set `OLAV_HOME` to the authorized absolute native project path first. Both snapshot IDs are required and must differ. `device` and `interface` are optional exact selectors. Use the normalized interface spelling stored in the historical record. `limit` ranges from 1 to 50 and defaults to 20.

The command reads the native historical `netops.parsed_outputs` table. It matches the device name and normalized interface using exact bytes. The result includes the fixed command and platform, the database SHA-256, and exact counts over the selected records. Samples are ordered by device and interface. Observations present on one side contribute to that side's count and have null deltas and rates. Collection coverage remains unknown.

| Field | Recorded count | Rate |
| --- | --- | --- |
| `input_errors` | Input errors | errors/second |
| `crc` | CRC errors | errors/second |
| `output_errors` | Output errors | errors/second |
| `queue_drops` | Input queue packet drops | packets/second |
| `queue_output_drops` | Total output packet drops | packets/second |

Compare these fields independently because their counters can overlap. Each sample retains the two observed readings. Nonnegative integer readings up to 38 decimal digits are supported, including decimal strings from the native parser. Missing or invalid values return null. A nondecreasing pair can yield a known delta. A decrease without further evidence returns `possible_reset_or_wrap`. Counter width and wrap count remain unknown.

## Collection time and resets

A rate requires known before and after instants with timezones and a strictly positive interval. The reporter uses exact command capture timestamps from explicit historical parsed columns or an identity- and SHA-256-verified ledger's command header. Date-only, timezone-free, conflicting, bundle-wide and ingest timestamps are insufficient. Default native bundle import can retain the two readings without retaining both precise command times. Deltas remain available when rate is unknown. The request itself sets the order of subtraction.

An exact IOS `show version` observation can confirm a reboot when the recorded restart time falls strictly between the two interface captures and the version observation is timed at or after the restart. A matching IOS interface stanza from verified historical raw evidence can confirm a counter clear when the explicit clear time, or the entire supported `HH:MM:SS` elapsed range, falls strictly between those captures. Confirmed discontinuity leaves the delta and rate null and returns the evidence. The tool compares recorded identities. Unrecorded resets, wraps and hardware replacements remain possible.

The source read is read-only and bounded on 64-bit Linux. The comparison uses a private verified copy, a 10-second deadline and fixed data, memory and response limits. Full observation counts cover the accepted scope independently of the returned sample count. If the scope exceeds a processing limit or has ambiguous identities, the command returns an error with null counts.
