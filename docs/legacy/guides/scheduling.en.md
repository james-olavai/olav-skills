# Scheduled Inspections

OLAV uses the system cron to run recurring agent tasks — daily snapshots, weekly audits, hourly health checks. All OLAV-managed cron entries are maintained centrally via the `manage_cron` tools on the `core/admin` sub-agent.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L1-08 | `list_cron` / `add_cron` / `remove_cron` @tools provided by the admin sub-agent | ✅ v0.17 |
    | C-L1-09 | `cron_schedules.yaml` for declarative batch application | ✅ v0.17 |
    | C-L1-10 | OLAV-managed cron entries are isolated by an `# olav:agent\|instruction` comment marker | ✅ v0.17 |
    | C-L1-11 | `add_cron` is upsert — same agent+instruction reuses the existing slot | ✅ v0.17 |

---

## Tool location

```
.olav/workspace/core/admin/tools/manage_cron.py    ← implementation
.olav/workspace/core/admin/SKILL.md                ← registered on the admin sub-agent
.olav/workspace/core/AGENT.md                      ← admin is a sub-agent of the core orchestrator
```

Call path:

```mermaid
graph LR
    User["olav 'schedule ...'"] --> Core[core orchestrator]
    Core -->|olav_delegate| Admin[core/admin]
    Admin -->|@tool| MC[manage_cron]
    MC -->|python-crontab| Crontab[("system crontab")]
```

---

## Isolation design

Every OLAV-managed entry carries a fixed-prefix comment:

```
0 6 * * *  cd /home/yhvh/Olav && /home/yhvh/Olav/.venv/bin/olav --agent audit \
           --auto-approve "run wireless_health" \
           >> ~/.olav/logs/cron_audit.log 2>&1   # olav:audit|run wireless_health
```

- The **`# olav:` prefix** is the isolation marker. `list_cron` / `remove_cron` only touch entries with this prefix; user-authored cron jobs are NEVER touched.
- The **`agent|instruction` suffix** is the task's unique key. Re-running `add_cron` with the same `agent` + `instruction` upserts (schedule edit), never duplicates.

---

## Imperative usage

### List all OLAV-managed jobs

```bash
olav "List olav scheduled jobs"
```

Returns:

```json
{
  "count": 3,
  "jobs": [
    {"job_id": "olav:config|take snapshot",
     "agent": "config", "instruction": "take snapshot",
     "schedule": "0 2 * * *", "enabled": true,
     "command": "cd /home/yhvh/Olav && .venv/bin/olav --agent admin --auto-approve ..."},
    {"job_id": "olav:audit|run wireless_health",
     "agent": "audit", "instruction": "run wireless_health",
     "schedule": "0 6 * * *", "enabled": true, ...}
  ]
}
```

### Add / update a job

```bash
olav "Schedule wireless_health audit to run every day at 06:00"
```

The admin sub-agent parses this as:

```python
add_cron(
    schedule="0 6 * * *",
    agent="audit",
    instruction="run wireless_health"
)
```

…and writes a crontab entry:

```
0 6 * * *  cd /home/yhvh/Olav && /home/yhvh/Olav/.venv/bin/olav --agent audit \
           --auto-approve "run wireless_health" \
           >> ~/.olav/logs/cron_audit.log 2>&1  # olav:audit|run wireless_health
```

!!! note "Schedule field format"
    Standard 5-field cron expression: `minute hour day-of-month month day-of-week`. Common patterns:
    * `0 2 * * *` — every day at 02:00
    * `0 6 * * 1` — every Monday at 06:00
    * `*/15 * * * *` — every 15 minutes
    * `0 8-18 * * 1-5` — every hour 08:00–18:00 on weekdays

### Remove a job

```bash
olav "Remove the scheduled wireless_health audit"
```

→ `remove_cron(agent="audit", instruction="run wireless_health")` — matches exactly on the agent+instruction pair in the comment.

---

## The `olav cron` command — opt-in activation

OLAV ships a set of scheduled jobs **declared** in `cron_schedules.yaml` at `olav init` — but it **never auto-writes them to your crontab**. A recurring `olav --auto-approve` job is a daily LLM call (and `snapshot` SSHes to live devices), so activation is always an explicit opt-in via the `olav cron` command:

```bash
olav cron enable            # activate ALL declared jobs (prints each + a disclosure note)
olav cron enable reflect    # activate just the daily self-reflection job
olav cron list              # show currently active olav cron jobs
olav cron disable [name]    # remove all, or one, olav cron jobs
```

`olav cron` resolves `cron_schedules.yaml` and routes to the `core/admin` `manage_cron` skill script — the single source of truth for crontab mechanics and undo journaling. It is the one-command front door to the same isolation + upsert machinery documented above.

Both `olav init` and `olav doctor` end with a one-line cron hint reflecting your current state:

```
○ optional — enable daily self-reflection via 'olav cron enable reflect'   # when none active
✓ 5 scheduled job(s) active                                                # when enabled
```

!!! note "Cron lines self-create their log directory"
    Every generated cron line self-creates `~/.olav/logs/` on each run (its redirect target) before writing — so a job never silently fails to log just because the directory was cleaned or the install is fresh.

---

## Declarative usage — `cron_schedules.yaml`

Better for IaC-style ops: all schedules batch-applied from one yaml.

### Config file location

```
.olav/workspace/ops/netops_init/config/cron_schedules.yaml
```

### Example

```yaml
# Cron schedule configuration for olav scheduled tasks.
# Apply with: olav --agent core "apply cron schedules"
#
# All jobs call: olav --agent <agent> --auto-approve "<instruction>"

schedules:
  snapshot:
    cron: "0 2 * * *"
    agent: config
    instruction: "take snapshot"
    description: "Daily network snapshot — SSH collect + parse + diff all devices"

  trace_learner:
    cron: "0 3 * * *"
    agent: config
    instruction: "run trace learner"
    description: "Learn from network traces and update command template registry"

  wireless_health_daily:
    cron: "0 6 * * *"
    agent: audit
    instruction: "run wireless_health profile"
    description: "Daily wireless health — AP concentration / migration debt / stale APs"

  physical_layer_daily:
    cron: "0 7 * * *"
    agent: audit
    instruction: "run physical_layer_health profile"
    description: "Daily physical layer — input errors / CRC / link-up-proto-down"

  audit_weekly:
    cron: "0 6 * * 1"
    agent: audit
    instruction: "generate weekly compliance report"
    description: "Weekly audit — BGP health, config drift, compliance check"
```

### Batch apply

```bash
olav --agent core "apply cron schedules"
```

The admin sub-agent calls `apply_cron_schedules()`, which does add-or-update for every yaml entry. Existing matches by `agent+instruction` are updated (schedule change); new ones are appended.

!!! tip "YAML as source of truth"
    Recommended workflow: keep all schedules in `cron_schedules.yaml` under git. Fresh installs run `apply_cron_schedules()` once and everything is set. Use imperative `add_cron` only for ad-hoc debugging.

---

## End-to-end example: schedule today's audits

Earlier we walked Explorer → Audit and froze the wireless and physical-layer findings into two v4.0 profiles:

- `audit/profiles/wireless_ap_concentration.md` (single job, AP concentration)
- `audit/profiles/physical_layer_health.md` (three jobs: top input errors / CRC dominated / link-up-proto-down)

Scheduling them is one sentence:

```bash
olav "Schedule wireless_ap_concentration daily at 6am
      and physical_layer_health daily at 7am"
```

Or declaratively — add two entries to `cron_schedules.yaml` and run `olav --agent core "apply cron schedules"`.

Every morning between 06:00–07:30 you'll get:

```
~/.olav/logs/cron_audit.log                                # run log
exports/audit_reports/wireless_ap_concentration_<date>.md  # AP concentration health
exports/audit_reports/physical_layer_health_<date>.md      # three physical-layer
                                                             sections + Priority 2b
                                                             follow-up commands
```

Critical / Warning rows in each report are already executable `olav -a netops "Investigate ..."` commands — operators copy them straight into a terminal for the next investigation step.

---

## Relation to the Audit Agent

See the "Full lifecycle" section of the [Audit Agent](../netops/audit-agent/index.md) doc. This guide is the **detailed reference for the third stage (Scheduling)** of that lifecycle.

| Stage | Owner | Frequency |
|---|---|---|
| Explorer discovery | netops/explorer + human review | One-shot |
| Profile authoring | audit/author | One-shot |
| **Cron scheduling** | **core/admin (`manage_cron`)** | **One-shot** |
| Recurring inspection | cron → audit/runner | Daily / weekly |

---

## Security notes

1. **`--auto-approve` flag**: cron-triggered audit/runner runs unattended, bypassing HITL gates. Profile SQL MUST be strictly read-only (SELECT only — no DDL/DML). The author sub-agent's `create_profile_atomic` runs schema verification + per-SQL validation that rejects INSERT/UPDATE/DELETE before writing the profile.
2. **`--agent` flag**: cron commands always pin to a specific `--agent <name>`. Avoid `--agent core` (the orchestrator can recursively reach admin/remote with write privileges). `--agent audit` / `--agent admin` are safe subsets.
3. **Log location**: `~/.olav/logs/cron_<agent>.log`. Configure logrotate for 30-day retention.
4. **PATH handling**: cron daemon's default PATH is minimal, so `manage_cron` generated commands use absolute paths (`cd /path/to/olav && /path/to/.venv/bin/olav ...`) — no shell PATH resolution required.
