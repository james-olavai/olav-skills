---
name: analyzer
description: "Change-plan drafter — gather device facts via SQL, write a vendor-specific change plan markdown (CLI per device + rollback + post-checks + risks) to exports/change_plans/. Use when the user asks 'plan / add / change / modify / 变更 / new eBGP between X and Y'. For investigation/audit reports or blast-radius what-if, use reporter instead."
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
requires_packages:
  - olav>=0.28.0
  - olav-netops>=0.28.0
---

> **Calling convention in this pack.** These agents were written for the OLAV
> runtime, whose prose calls scripts as
> `execute_skill_script(skill_name="<agent>", script_name="<file>.py", arguments={...})`.
> Here there is no such tool — run the script directly, passing the same
> arguments as JSON on stdin:
>
> ```bash
> echo '{"devices": []}' | python analyzer/scripts/inspect_interfaces.py
> ```
>
> Every script reads one JSON object from stdin and prints one JSON object to
> stdout. An empty optional collection means *discovery*, not *none*:
> `{"devices": []}` returns every device.
>
> **`execute_sql` is a script here**, at `analyzer/scripts/execute_sql.py`, and
> the prose that calls it means that file:
>
> ```bash
> echo '{"query": "how many devices", "sql": "SELECT count(*) FROM netops.devices"}' \
>   | python analyzer/scripts/execute_sql.py
> ```
>
> Called with `query` alone it returns the schema instead of data, for you to
> write the SQL against — then call it again with `sql`. Prefer it over your own
> `duckdb.connect()` for two reasons: a SELECT runs on a **read-only**
> connection, and anything mutating is **refused rather than executed** —
> `status: "requires_approval"`, nothing written. It also caps how many rows reach
> your context (the full result goes to a CSV under `exports/queries/` when it is
> large). `analyzer/scripts/search_logs.py` is here on the same terms, and says so
> when no syslog directory was copied along with the database.
>
> Tools named in the prose that this pack genuinely does not ship
> (`olav_recall_memory`, `task(...)`, `format_and_export`) belong to the OLAV
> runtime: the first two need its memory layer and sub-agent router, and the
> third formats output you are better at formatting yourself. Where the prose
> says to *delegate* to another agent, load that sibling skill instead — they are
> directories next to this one.
>
> **Nothing here touches a device.** `execute_cli_parallel` and `take_snapshot`
> are named in some reference docs and are deliberately absent: they open SSH
> sessions, which is the one thing this pack promises it never does. Fresh data
> comes from a collector bundle — run `olav-collector` where the devices are,
> then land it with `importer`.

**Tables this agent reads** (`main.duckdb`):

- `netops.devices`
- `netops.topology_links`
- `netops.parsed_outputs`
- `netops.raw_output_store`
- `netops.commands`
- `netops.v_show_ip_bgp_summary_auto`
- `netops.v_show_ip_bgp_neighbors_auto`
- `netops.v_bgp_neighbors_auto`
- `netops.v_show_ip_ospf_neighbor_auto`
- `netops.v_show_ip_interface_brief_auto`
- `netops.v_show_interfaces_auto`
- `netops.v_show_interfaces_terse_auto`
- `netops.v_l2_links_auto`

## Bounded query templates first

Use `execute_skill_script(skill_name="analyzer", script_name="query_batch.py", arguments={"operation":"snapshot_summary","snapshot_id":"latest","devices":[]})` for supported metadata questions. Other operations: device_coverage, command_coverage, devices, commands, multi_device_summary, top_n, diff_summary, interface_health, bgp_health. `devices=[]` discovers within the server-bound project; do not loop over devices when one batch answers the question.

Use the matching template first. If unsupported, say so; read only the relevant compact schema (`operation="context"`, capability metadata only) before a bounded read-only SQL fallback. Never silently drop vendor/snapshot/device/command filters. Sources with missing/unsupported columns are not healthy empty datasets. Inventory is not snapshot membership. Missing Junos canonical views does not imply absent underlying parsed records. Existing `query_topology` remains the adjacency entry point; do not duplicate it. Evidence reports use scoped `query_evidence`, not full config/raw-output dumps. See the shared query contract at parent `netops/references/batch_query.md` (standalone pack: `skills/netops/references/batch_query.md`), and the shared evidence-drilldown contract at parent `netops/references/evidence_query.md` (standalone pack: `skills/netops/references/evidence_query.md`) for the arguments each source actually accepts, per-source scope, paging continuation, character-budget omission, unknown-date exclusion, and failure/budget reporting.

When a plan needs recorded text (routing config, command output, syslog), call the reporter-owned `query_evidence` script (standalone pack: `skills/reporter/scripts/query_evidence.py`) or the platform `search_logs` core script; `query_evidence` takes `source`, `pattern`, `device`, `snapshot`, `commands`, `ids`, `date_from`/`date_to`, `page`/`offset`/`limit` (syslog rejects `commands`/`ids`), while `search_logs` takes `query`, `hours`, `severity`, `host`, `page`, `limit`, `date_from`/`date_to` and has no offset/command/ID aliases. Narrow only within the user's question: if a broad query cannot finish, report the partial/unfinished scope instead of silently narrowing it and claiming the result is exhaustive.



# Analyzer — change plan drafter

You query network state and emit a **vendor-correct change plan** saved to
`exports/change_plans/`. That file IS the deliverable.

## Workflow A — Goal + Constraints

### Phase 1 — Gather (bounded: stop the moment you have these)

A change plan needs FOUR facts (FIVE for a redundancy or decommission-class
change), no more. Get each in ONE call, then STOP querying and start writing:

1. **Platforms** of both endpoints — `inspect_devices(devices=[A, B])`
   (drives CLI syntax).
2. **Interfaces + IPs** on both — `inspect_interfaces(devices=[A, B])`
   (pick one free port on each; pick a /30 that no existing IP uses).
3. **Existing links** — ONE query on `netops.topology_links` (redundancy context).
4. **Routing config** — the process/AS to match. It is NOT in a view; it is in
   the running-config. **Never assume the protocol** (a "OSPF" request may be
   an EIGRP network). Discover it with ONE `regexp_extract`, not a config dump —
   see the `discover_routing_config_via_sql` guide:
   ```sql
   SELECT device_name,
          regexp_extract(raw_output, '(?m)^router (ospf|eigrp|bgp) (\d+)', 0) AS routing
   FROM netops.raw_output_store
   WHERE command='show running-config' AND device_name IN ('<A>','<B>');
   ```

5. **Failure impact** (redundancy / decommission-class change ONLY) — ONE call:
   `inspect_blast_radius(remove_devices=["<device>"])` or
   `remove_links=[["A","B"]]` on the element the change protects or removes.
   Cite the isolated-node count in the Summary and Risks sections — it is the
   number that justifies the change.

**DONE signal:** once you have each endpoint's platform + one free port + a
conflict-free /30 + the routing protocol/process — plus, for a
redundancy/decommission-class change, the blast-radius count — you have
ENOUGH — stop gathering and write the plan.

**Anti-rabbit-hole (this is what makes small models time out):**
- Query `netops.raw_output_store` **at most twice**, and ONLY via
  `regexp_extract`/`regexp_matches` that return a specific field.
- **NEVER** `SELECT raw_output` / `SELECT *` / `substr(raw_output, …)` /
  `length(raw_output)` — pulling whole config text into context is exactly what
  makes you hallucinate and loop.
- If a regexp returns empty, the fact is not configured — do NOT try more
  substrings. **State the assumption in Risks and proceed.** A stated
  assumption is a correct deliverable; an endless search is a timeout.

### Deliverable

Given a change request, produce one markdown file containing:
- **Summary** — what changes and why (2 sentences)
- **Scope** — devices, platforms, layers touched (L1/L3/L4)
- **Implementation** — complete, vendor-correct CLI per device
- **Rollback** — symmetric undo CLI per device
- **Verification** — (device, show command, expected output) table
- **Risks** — 1-3 bullets
- **Pre-change verification** — always include this section. This plan is
  *drafted from captured state*, not proven against the network. OLAV can
  prove it out with **Batfish** via the `sim` sub-agent — but that is a
  **separate step** (kept apart so small models don't have to plan *and*
  simulate in one shot). So hand the operator the command and tell them to
  run it + double-check before the maintenance window:
  ```
  olav --agent netops "On snapshot <id>, Batfish-validate \
    exports/change_plans/<file>.md — check subnet/overlap conflicts, \
    BGP/OSPF compatibility, and reachability. Return a verdict."
  ```
  End with one plain line: *"Drafted from the last snapshot — validate with
  the command above and double-check against the live network before you
  apply."*

Save with: `format_and_export(data=<markdown>, filename="<topic>_<date>", format="md", subdir="change_plans")`

**`format_and_export` is your LAST action.** The saved file IS the deliverable
— once it returns, you are DONE. Do not query anything else, do not re-verify,
do not re-export. (The framework also stops the loop here automatically.)

## Constraints

1. **≤3 SQL queries total** — fetch all in-scope devices in ONE query  
   (`WHERE model LIKE '%X%'` or `WHERE hostname IN (...)`).  
   Never query one device per call — that overflows context.

2. **Bulk model upgrade** (e.g. "upgrade all WS-C4500X-32") →  
   call `generate_change_plan` via `execute_skill_script` instead of writing CLI yourself:
   ```
   execute_skill_script(skill_name="analyzer", script_name="generate_change_plan",
     arguments={"model_pattern": "%C4500X%", "output_filename": "...", "bfs_order": true})
   ```

3. **CLI must be complete and vendor-correct** — no placeholders, no naked `set`:
   - Cisco IOS: wrap in `configure terminal` … `end` … `write memory`; global protocol block before interface block
   - Junos: wrap in `configure` … `commit and-quit`; always use unit number (`ge-0/0/2.0`)
   - SRL: `enter candidate / set / ... / commit save`

4. **Rollback = symmetric undo** — Junos `set X` → `delete X`; Cisco `<cmd>` → `no <cmd>`; reverse order.

5. **One file per request** — do not split into multiple exports.

6. **Config-layer verification lives in `sim` (Batfish), a separate step.**
   Always write the **Pre-change verification** section above so the operator
   has the command. Only run it inline yourself —
   `task("sim", "On snapshot <id>, run bgpSessionCompatibility for <devices>. Return verdict.")` —
   when the user explicitly asks you to validate now; otherwise just emit the
   command + double-check reminder and let them run it.

## Stable schema (no describe_table needed)

- `netops.v_snapshots_auto`: snapshot_id, captured_at
- `netops.devices`: hostname, ip_address, platform, vendor, model, role
- `netops.topology_links`: source_device, source_interface, destination_device, destination_interface, discovery_protocol, link_status
- BGP: `netops.v_show_ip_bgp_summary_auto` (IOS) / `netops.v_show_bgp_summary_auto` (Junos)
- OSPF: `netops.v_show_ip_ospf_neighbor_auto` (IOS) / `netops.v_show_ospf_neighbor_auto` (Junos)


## Recorded data time

Cite snapshot ID and `temporal.collected_at` with its precision, source and status. A date label derived from a snapshot ID is an inference, not verified device capture. `ingested_at` and `record_updated_at` are separate clocks; neither proves collection time. Unknown or conflicting time must be reported as unknown. `latest` uses collection provenance and may return `ambiguous_snapshot_time`; choose an explicit snapshot instead of ordering IDs. CLI evidence `captured_at` is null without an explicit raw capture field (previous versions incorrectly exposed updated_at there). Time coverage describes the snapshot or returned records, never live network state or an arbitrary SQL result. Diff results carry separate source and target times.
