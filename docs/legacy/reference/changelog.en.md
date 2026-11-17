---
title: Changelog
description: Release notes for OLAV platform — recent changes, breaking renames, migration notes.
---

# Changelog

Only user-visible changes are listed here.  For the full commit log see the
[gitea repo](http://192.168.100.50:3000/admin/olav) (internal) or the GitHub
mirror for public releases.

## v0.26.0 — Silent behaviour made visible; verified provider layer

The theme is telling you what happened. An import that discarded 74% of command
outputs said nothing; an auto-export reached you only if the model chose to
mention it; `olav doctor` passed 9/9 on an install whose every query path was
dead.

### Features

- **`olav doctor` grew from 9 to 11 checks.** `auth` names the mode and the
  provider that will serve it — a clean install could pass 9/9 with every query
  path dead, because `init` had written `auth.mode=token` whose provider ships
  in olav-ent. `context` compares the agent's context budget against the window
  your server actually advertises, which is what turns into `agent error, code
  400` on a long run.
- **`olav doctor --verify`** proves a service works rather than merely answers,
  and each registered service contributes its own readiness check.
- **Import report.** Ingest writes `exports/import_reports/<snapshot>.md` naming
  how much parsed, what did not, and why — `raw_only`, `no_parser_registered`,
  `parser_no_match`, or a parser error. On a 339-device dataset, 8,761 command
  outputs produced 2,278 structured rows; that 74% was previously invisible.
  Where the `learner` could close a gap the report says so and stops — it never
  runs it, because that would turn a two-minute import into a long one.
- **Query exports are announced.** When `execute_sql` caps rows to fit the model's
  context and writes the full result to a file, the CLI states the row count, the
  path, and how much you were actually shown.
- **Provider support is now measured, not assumed.** Each provider has a
  first-class driver with its own function-call determinism defaults, pinned by
  an offline check on the exact request it receives plus an opt-in live call.

### Fixes

- **Queries selecting stored config text failed** with `'str' object has no
  attribute 'items'`. A per-field size budget was being applied to whole rows,
  so any wide row stopped being a row.
- **`olav init` no longer overwrites an auth mode you chose.** It only selects a
  mode on a config it created in that run.
- **Claude rejected requests with a 400** when temperature exceeded 1.0, which is
  its documented ceiling. Temperature is now clamped per provider.
- **Memory capture ran twice per query**, costing a second LLM round-trip and
  writing duplicate memories.
- **A repeated query left duplicate CSVs.** Exports are content-addressed, so an
  identical result set reuses its file.
- **`devices.role` stayed empty** when no inventory supplied it; it is now derived
  from the hostname where the name says so.
- **Two co-located deployments could adopt each other's containers** — compose
  project names are namespaced.

### Removed

- **Mistral provider support** and its `[mistral]` extra. The setup wizard was
  offering a provider whose driver a plain install did not ship.

## v0.25.1 — Packaging fix: authored workspace files now ship

### Fixes

- **16 authored workspace reference files were never packaged.** Guides and
  references that shape agent behaviour existed in the repo but not in the wheel,
  so a pip install behaved differently from a source checkout.
- **`render_report` initialised an LLM unconditionally**, so a report that needed
  no model still required one to be reachable.

### Changed

- Developer-facing text in the workspace prompt surface is English-only, so a
  mixed-language prompt no longer reaches the model.

## v0.25.0 — Tiering: team features move to olav-ent

### Changed

- **The web API and WebGUI moved to olav-ent.** The open-source package is the
  CLI, runtime and agents; team features (multi-user auth, the web surface) are
  the enterprise extension.
- **`auth.mode=none` grants the local OS user admin rights.** A single-operator
  install needs no login at all.
- **A plain install is smaller** — `fastapi` and the `ldap3` extra are gone from
  the OSS dependency set.

### Features

- **One write path to DuckDB** (`open_write_connection`), so concurrent writers
  no longer corrupt state or race each other.

## v0.24.1 — Audit and export fixes

### Fixes

- **Audit postcheck prompts suggested a command that does not exist**
  (`olav -a ops`); they now name a real agent.
- **Batfish export** falls back to built-in per-platform backup commands when a
  device has no stored config, instead of exporting nothing.

## v0.24.0 — Loop protection, declarative workflows, honest doctor

### Features

- **Multi-step tasks can be declared** rather than improvised: known-shape work
  routes across sub-agents by declaration.
- **A zero-LLM circuit breaker on every agent.** A model repeating the same tool
  call is stopped by Python, not by asking the model to notice.
- **`olav doctor` names the backends it found** — model and endpoint for the LLM,
  the same for embeddings — so a misconfiguration is visible without a run.
- **Per-script timeouts** can be declared in `SKILL.md`.

### Fixes

- **The first-run wizard rejected the numbered choice it had just printed** in
  the embedding menu.
- **API-mode embeddings read a config key that did not exist**, so an endpoint you
  configured was silently ignored.
- **`deploy_service` reported healthy when a container had already exited** —
  `docker compose ps` was being read without `-a`.
- **A single broken audit view failed the whole audit**; jobs now degrade
  individually.

## v0.23.1 — Metadata

### Changed

- Project URLs point at `https://olavai.com` and the documentation site; author
  and copyright metadata unified.

## v0.23.0 — Self-reflection, scheduled jobs, recall benchmark

Also carries the changes internally tagged 0.22.2, which was never published.

### Features

- **`admin/reflector`** — a daily self-reflection pass that turns recurring error
  signatures into constraints the agents recall later.
- **`olav cron`** — `enable` / `disable` / `list` for declared scheduled jobs,
  opt-in rather than implicit.
- **`olav kb bench`** — measures whether the knowledge base can actually recall
  what was stored in it, without domain assumptions.
- **`olav doctor` grew from 3 to 8 zero-LLM checks**, adding agents, tools and
  scaffolding.
- **`draw_topology`** — a one-shot draw.io or Mermaid topology from the writer
  agent.
- **`execute_sql` caps per-cell text**, so one 70 KB config column can no longer
  consume the model's whole context.
- **Batfish self-service** — the simulator diagnoses a missing Batfish and offers
  to deploy it instead of failing.

### Fixes

- **Scheduled jobs silently did nothing** — their log redirect pointed at a
  directory that did not exist.
- **`register_service` wrote auth keys the reader never looked for**, so a
  registered credential was ignored.
- **NetBox 4.5** — Bearer `nbt_` tokens and the `/api` prefix are supported.
- **`olav doctor --json` emitted invalid JSON**, because rich wrapped a value
  mid-line.
- **A slow local model was reported as unreachable** rather than slow.

## v0.22.1 — Local embedding endpoints

### Fixes

- **Ollama, llama.cpp and vLLM embedding endpoints** work as configured; the
  client no longer assumes an OpenAI-shaped response.

## v0.22.0 — Zero-ritual onboarding

### Features

- **Bare `olav` in a fresh directory sets itself up** — no init ceremony before
  the first question.
- **`olav doctor` and the TUI `/doctor`** — a deterministic, zero-LLM health check
  you can run before blaming the model.
- **A welcome screen that reports real findings** from your workspace instead of
  rotating tips.
- **Conversational self-configuration** — the admin agent can edit its own
  configuration under approval.
- **Undo for workspace writes and cron changes.**

## v0.21.1 — Per-skill LLM overrides, KB source-tier lifecycle, NetBox CSV sync

Status: **in progress** (post-rc4, prepping for tag).

### Features

- **Per-skill LLM overrides.** Each `SKILL.md` / `AGENT.md` may now carry an
  optional `llm:` block with any subset of `model`, `temperature`,
  `max_tokens`, `base_url`, `model_provider`, `num_ctx`, `num_predict`.
  Missing keys fall through to the global `api.json.llm.*` default.  No
  profile indirection — single fall-through chain.  Used in-tree by:
  - `sim`, `investigate`, `learner` → `temperature: 0.0` (deterministic output)
  - `core/memory_curator` → `max_tokens: 4096` (R102 HITL is short)
  - `analyzer` (no override) — inherits global 32K
- **Global `max_tokens` raised 16000 → 32768.**  gemma4:31b nothink can
  produce long structured tables/reports without truncation.  Override
  per-skill if needed (memory_curator already does the opposite).
- **Source-tier KB lifecycle (`dev_docs/79`).**  Every `*.guide.yaml`
  carries a `source_tier` field (`vendor` / `platform` / `team` / `user`).
  Priority + weight derived from the tier, not hand-set.
  - R102 conversational commits land at `source_tier: user` (priority 3,
    weight 0.83) — operator-spoken rules can't outrank architectural
    invariants by accident
  - Schema v1 YAML still loads (default = `user` tier with backward-compat
    for explicit `priority:` field)
  - New CLI: `olav kb remove <intent> --reason "..."` (tombstone YAML +
    LanceDB delete + `kb_audit/<ts>_remove_<intent>.yaml` row), `olav
    kb show <intent>`, `olav kb list-guides --tier <name>`
- **`kb_audit/` git-trackable audit chain.**  Every KB-mutating action
  (R102 commit, `kb remove`, NetBox push) drops one
  `kb_audit/<iso-ts>_<verb>_<intent>.yaml` row with
  `action / intent / actor / timestamp / body_sha256` + outcome counters.
  Distinct from `audit.duckdb` — git-tracked so the change record
  survives independent of the runtime DB.
- **`olav user` CLI.**  Friendly aliases for `olav admin add-user/...`.
  `olav user create <name>` requires the Linux user to exist
  (`pwd.getpwnam(name)`) by default — OLAV's user model is aligned to
  the OS, no parallel identity.  Token hash + salt stored, plaintext
  shown once.  Override Linux check with `--no-verify` for containers.
- **Collection-time credential redaction** (`olav.core.redaction.scrub`,
  ADR-0008).  Two-layer pipeline scrubs passwords / SNMP communities /
  BGP MD5 keys / IPSec PSK / TACACS / RADIUS shared secrets out of
  collected network config before it lands in `audit.duckdb` or
  exported datasets, while keeping IP / hostname / ASN / topology
  intact so diff and diagnostic logic still work.
  - **Collection layer**: `olav.core.redaction.scrub` wraps
    [netconan](https://github.com/intentionet/netconan) (Batfish team,
    ~50 KB pure Python; 55 built-in sensitive-item regex + 5213 reserved
    words; per-workspace `.redaction_salt` chmod 600).  Wired into
    `netops_init/run.py:_collect_cmd` (collection boundary) and
    `AuditEventRecorder.record_message` (defense-in-depth for chat/LLM
    text).  Replacement marker: `netconanRemovedN`.
  - **Enterprise gate layer**: `audit_recorder.redact_sensitive`
    (5-pattern regex producing `[REDACTED]`) drives Layer 1 of
    `audit_dataset_export.redact_audit_run` and runs again at gate-check
    time as a final escape detector.
  - **Configuration**: `api.json.redaction.{enabled, anon_ip,
    extra_sensitive_words, extra_reserved_words}` +
    `OLAV_REDACTION=0` env-var kill-switch.  Install via
    `pip install 'olav[redaction]'` to pull `netconan>=0.13`; without
    it OLAV still operates but Layer 1 fails open with a WARNING.
  - 16 governance tests in `tests/governance/test_redaction.py` cover
    Cisco / Junos / salt management / fail-open semantics / idempotency.
- **NetBox CSV sync chain** (`dev_docs/71` Ch10).  Three-phase workflow
  decoupling read (netops) from write (services) via a CSV intermediate:
  - `olav --agent netops "/export_netbox_csv"` — produces NetBox-shaped
    CSV from `netops.devices` (column mapping in
    `netops/guides/netbox_csv_export.guide.yaml`)
  - `olav --agent services "/import_netbox_csv"` — dry-run validates
    shape, writes `exports/reports/netbox_import_dry_run.md`
  - `olav --agent services "/import_netbox_csv --write --endpoint ...
    --token ..."` — actual POST to a running NetBox, 5-step idempotent
    lookup-or-create chain (site → manufacturer → device-type →
    device-role → platform → device); writes `kb_audit/` row per push

### Fixes

- **Audit tables populate from the CLI again.**  v0.20.3 flipped
  `OLAV_MIDDLEWARE_MODE` default to `middleware`; the CLI invoke path
  never bound an `OlavRunContext` to `astream_events`, so
  `AuditMiddleware._get_context()` returned None and silently no-op'd
  every tool call.  Plus `record_run_start` was called without
  `thread_id` so the `sessions` UPSERT never fired.  Both fixed:
  CLI now mirrors the API server's context-binding pattern, and a
  `thread_id=session_id or run_id` is threaded through every turn.
  Net effect: `audit_runs / sessions / audit_events / audit_messages /
  audit_tool_calls` all populate after a single tool-using turn.
- **`reasoning_effort: minimal` no longer sent.**  Ollama OpenAI-compat
  strictly validates this field and accepts only
  `high / medium / low / max / none`.  Dropped — the sibling
  `chat_template_kwargs.enable_thinking=false` already handles the
  qwen3 / gemma4 nothink path.
- **`olav skill install` refuses to overwrite platform-owned
  workspaces.**  Domain-package installs (e.g. `olav-netops`) that
  carried a dev-mirror of `audit/` would silently clobber the
  platform's bundled audit/ on `olav skill install`.  Now guarded by
  `_PLATFORM_OWNED = {core, audit, services}` skip list with a
  `--force-overwrite` escape.
- **`/netops_init` ingest gate.**  Stage 4 now distinguishes "SSH
  collected" from "DB ingest" and prints `❌ FAILED at the ingest
  stage` with non-zero exit when the staging JSON loaded 0 rows
  despite successful SSH collection.  Previously printed `✅
  Network initialization complete` even when ingest erred.
- **5 SQL view schema guides shipped** (`netops/guides/sql_schema_*.guide.yaml`)
  documenting BGP / OSPF / interface / route / device-and-topology view
  column names + worked examples.  AutoRecall surfaces them on
  schema-discovery queries; gemma4 nothink lands first-try SQL
  instead of column-name trial-and-error.
- **`olav user` subparser added.**  Previously CLI had only the more
  verbose `olav admin add-user` path; `olav user create / list /
  token / revoke` are the canonical user-facing form.
- **Platform audit workspace bundled into the wheel.**  Pre-v0.21.1
  `pip install olav==0.19.0 && olav init` produced only 2 agents
  (`core` + `services`); the audit/ workspace existed in dev but
  wasn't in the wheel's data path.  Fresh install now ships 3
  platform agents (audit / core / services).

### Migration

- **No flag-day.**  Schema v1 `*.guide.yaml` files continue to load,
  treated as `source_tier: user` with a deprecation warning at prime
  time.  Migrate at your leisure; the v2 form is
  `schema_version: 2` + an explicit `source_tier:` field.
- **R102 commits are demoted to `user` tier.**  If you were relying
  on operator-spoken rules outranking platform guides, that no longer
  happens.  Promote via git PR (edit the YAML to `source_tier: team`
  and commit through the normal review path).
- **Lab / CAB content moved out.**  `lab/` sub-agent + CAB workflow +
  TCF schema demo + fault-injection scripts now live in `olav-ent`.
  The platform-base runsheet (`dev_docs/71`) drops former Ch7 + Ch8;
  the enterprise runsheet picks them up.

---

## v0.21.0 — WebUI agent routing + service lifecycle hardening

Status: **in progress** (rc4 latest at time of writing).

### Fixes

- **Web GUI: agent dropdown now actually switches the running graph.**
  Pre-rc2 the FastAPI server kept a single `_agent_instance` singleton;
  clicking "ops" in the dropdown only re-tagged audit events but still
  executed the core graph.  `get_agent()` is now a per-`assistant_id`
  cache; five agents coexist without cross-contamination.
- **`POST /reload` returns 200 again.**  rc2 replaced the agent singleton
  with a cache dict but forgot to update the `/reload` handler; every
  reload raised `NameError`.  Handler now iterates the cache, best-effort-
  closes each agent, and clears state.
- **Audit in middleware mode sees run_id.**  v0.20.3 flipped
  `OLAV_MIDDLEWARE_MODE` default to `middleware`; the web server never
  attached an `OlavRunContext` to `astream_events`, so `AuditMiddleware`
  had no `run_id` to record against — every web request ran without
  audit.  Fixed by wiring context through in both modes.
- **Thread + agent binding persists across page refresh.**  WebUI now
  writes `currentThreadId` and each thread's `agentId` to
  `localStorage`; F5 restores state fully.
- **`core` pinned first in `/agents`.**  Installing olav-netops adds
  4 workspaces that all sort before `core` alphabetically, so the
  WebUI dropdown was auto-selecting `audit` as the default.  Server
  now forces `core` to index 0.
- **Service lifecycle pins spawned processes to the workspace.**
  Pre-rc4 `uvicorn` / the agent daemon / the syslog receiver derived
  their PID + log paths from a module-level `PROJECT_ROOT` frozen at
  module-import time.  When olav was launched from inside another repo
  with a `pyproject.toml`, the spawned processes silently inherited the
  wrong cwd and read the wrong `users.duckdb` — every login 401'd.
  Resolution is now cwd-based, anchored on the `.olav/` marker, and
  re-resolved on every call; `Popen(..., cwd=str(workspace_root))` is
  passed explicitly.
- **`olav service start --all` works on a fresh wheel install.**  Not
  a bug — arg order is `olav service <action> --all`, which was always
  correct — but the demo runsheet examples have been re-verified.
- **`olav skill install <path>` still works** via a v0.21.0-rc4
  deprecation shim.  The singular `skill` dispatcher was removed in
  v0.20.3; the shim forwards verbatim to `olav agent install` and
  prints a one-line warning.  See [CLI reference](cli.md) for the
  migration note.

### New in rc4

- [`scripts/sync_netops_workspace.py`](http://192.168.100.50:3000/admin/olav/src/branch/0.11.0/scripts/sync_netops_workspace.py)
  — sync the platform-vendored `.olav/workspace/ops/` copy with the
  authoritative `olav-netops/.olav/workspace/ops/` tree.  Default
  direction: root → olav-netops; `--reverse` pulls the other way;
  `--check` is the CI drift guard.

---

## v0.20.3 — deepagents migration complete (P7 final)

- **`olav skill install` subcommand removed.**  Use `olav agent install`.
  (v0.21.0-rc4 adds a deprecation shim for back-compat; see above.)
- **`OLAV_MIDDLEWARE_MODE` default flipped to `middleware`.**  The
  legacy callback plugins are still installable via
  `OLAV_MIDDLEWARE_MODE=callback` for one release cycle while
  `AuditMiddleware` proves itself.
- **`parse_subcommand_args` sweeping into 7 verbs.**  Shared helper
  for quoted/empty/`--` argparse edge cases.
- **`workspace_discovery.py` consolidated.**  Three duplicate scanners
  merged into one; all consumers (CLI, web `/agents`, TUI) go through
  the same code path.

## v0.20.2 — P1 + P2 + P6 cutover

- **Native TUI** (via `langgraph dev` subprocess) is now default,
  replacing the in-process overlay.  `OLAV_TUI_MODE=overlay` rolls
  back to the v0.19.x path for emergency cases.
- **Tool-loader** auto-discovers `.deepagents/agents/<name>/tools/*.py`
  and `.deepagents/agents/<name>/skills/<skill>/tools/*.py` at
  startup.
- **v0.20 workspace layout migration** available via
  `olav migrate v0_20_layout` (idempotent + `--dry-run`).

## v0.20.1 — P3 middleware migration

- **`AgentMiddleware` subclasses** for audit / guardrails /
  memory-capture / memory-recall / uks-capture, parallel with the
  callback plugin path.  `OLAV_MIDDLEWARE_MODE` env var selects.

## v0.20.0 — P5 graph factory + `olav agent install`

- **`olav agent install <path>`** introduced as the public install
  verb.  `olav skill install` still works (same dispatcher under the
  hood); this release is the start of the rename.
- **`graph_factory.build_graph()`** internal API for constructing the
  LangGraph per `assistant_id` — foundation for the per-id cache
  that landed in v0.21.0-rc2.

---

## v0.19.0 — last pre-deepagents release

See [DEMO_RUNSHEET](https://github.com/olav-ai/olav/blob/v0.19.0/dev_docs/42.%20DEMO_RUNSHEET.md) (historical).
