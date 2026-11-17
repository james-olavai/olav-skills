# Importer

The **importer** lands offline snapshots into OLAV's main DB — the same
`raw_output_store` + structured views that live SSH collection feeds. Drop in a
portable bundle, a rancid backup, or a vendor dump and it lands the data
without any SSH access from the OLAV host.

Use it when the OLAV host cannot reach the devices directly:

- **Air-gapped sites** — an operator collects from a jump box and sends the
  output back.
- **Existing backups** — rancid / Oxidized / NCM already collect nightly;
  reuse that data.
- **Vendor support handoffs** — a `show tech-support` bundle for diagnostics.

!!! note "Offline sibling of the collector"
    The [collector](collector.md) does **live** SSH gather. The importer does
    **offline** file gather. Both feed the same downstream
    (`raw_output_store → parsed_outputs → v_*_auto views`).

---

## When to use

- "Import the snapshot from this zip into OLAV"
- "I have a rancid backup directory — can we analyze it?"
- "Run the audit on the data the customer sent in a tarball"

---

## Tools

| Tool | When to use |
|------|-------------|
| `survey_bundle` | Call FIRST — format detection + host/platform survey |
| `validate_bundle` | sha256 + manifest schema check (no DB writes) — run before ingest |
| `ingest_snapshot` | Land the bundle into `raw_output_store` + structured views |
| `discover_platform_for_host` | TextFSM cascade platform detection for a host |

---

## Example

```bash
olav --agent netops "ingest the bundle at ~/.olav/inbox/snapshot.bundle"
```

The orchestrator routes to the importer, which surveys the bundle
(`survey_bundle`), validates it (`validate_bundle` — refuses to proceed on
sha256 / schema mismatch), then lands it with `ingest_snapshot` and reports the
hosts and commands landed.

```python
task("importer", "validate then ingest the rancid backup at /data/backups/site-tor1")
```
