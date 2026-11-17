# Memory Architecture

OLAV is **designed for local small models** (qwen3-class 7-30B, deepseek small, Llama, etc.). The single biggest reason OLAV gets useful work out of these models — instead of hitting their planning limits — is its **memory layer**.

This page explains the architecture: what gets stored, who writes to it, who reads from it, and how operators / teams **extend it without touching source code**.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|------|--------|
    | C-L3-UKS | Unified Knowledge Store: single LanceDB table for documents, memories, graph nodes | ✅ v0.13.0 |
    | C-L3-AUTORECALL | AutoRecallMiddleware injects relevant memories before each model call | ✅ v0.15+ |
    | C-L3-L1-CAPTURE | OperationalEventCapturePlugin auto-captures write-class tool calls | ✅ R98 |
    | C-L3-L2-EXTRACT | pattern_extractor abstracts N samples into reusable patterns via LLM | ✅ R98 |
    | C-L3-DIRECTIVE | Directive-guide YAML files prime memory at install time | ✅ R98 |

---

## The big idea

Traditional agent frameworks rely on the language model to **plan and execute** every request from scratch. That works for frontier cloud models. **For local small models, it doesn't** — they have well-known weak spots:

- Declaring intent ("Now I'll write the script…") then stopping without emitting the tool_call
- Re-discovering the same SQL three times across sessions
- Forgetting team conventions (naming, security policy, preferred tools)

OLAV's response: **don't fix the model, change the prompt**. Every model call gets relevant **memories** automatically injected as part of the prompt, surfacing precedent, conventions, and constraints right where attention is highest.

This makes memory the **prompt programming language** of OLAV. Behaviour is steered by what's in the LanceDB store, not by hardcoded middleware.

---

## The unified memory store

One LanceDB table — `memory` — holds all categories of knowledge, indexed by a single embedder. Hybrid search (vector + BM25) recovers relevant rows for any given query.

```
.olav/databases/memory.lance/
  └── memory/                  ← one table, multiple categories
      ├── id              (string)
      ├── text            (string — full content, embedded as-is)
      ├── vector          (FixedSizeList[float32] — dim auto-detected
      │                    from embedder; 768 for nomic-embed-v2,
      │                    1536 for OpenAI text-embedding-3-small)
      ├── category        (one of: usage_guide / expert_knowledge /
      │                    operational_event / query_pattern / fact /
      │                    decision / preference / audit / format_guide /
      │                    reflection / topology / document)
      ├── scope           (string — "global" / agent name / "team-X")
      ├── metadata        (JSON — per-category extras)
      ├── origin          (agent / document / user / audit / directive)
      ├── confidence      (float 0.0-1.0)
      ├── tags            (JSON list — entity / topic tags)
      ├── timestamp / created_at
      └── access_count    (int — bump on every recall hit)
```

Why a single table instead of per-category tables: hybrid retrieval works **across categories simultaneously**, so a query like *"how do we register InfluxDB?"* can pull the directive guide (`usage_guide`) AND a recent successful registration (`operational_event`) in one call. The `category` field is for filtering and rendering, not isolation.

### Data safety: dim-mismatch fail-fast

If the configured embedder's vector dimension doesn't match the
existing LanceDB table's stored dim (e.g. you switched from a 768-dim
local BGE to a 2048-dim cloud embedder), OLAV **refuses to start**
and raises `EmbeddingDimMismatchError`.  The table is preserved
intact — operators must explicitly resolve by either fixing the
embedder config, running a re-embed migration, or opting in to the
legacy drop-and-recreate behaviour with
`OLAV_ALLOW_DESTRUCTIVE_DIM_MIGRATION=1` (debug only — destroys all
rows).  Earlier OLAV silently dropped the table; that path is now
gated behind explicit opt-in to prevent silent data loss when the
embedder fallback flaps.  See `dev_docs/00 §
ISSUE-EMBEDDING-FALLBACK-DIM-MISMATCH-DESTROYS-DATA`.

---

## Memory categories — what's in the store

| Category | Author | Purpose | Example |
|---|---|---|---|
| `usage_guide` | Operator (via `*.guide.yaml`) | Hand-authored team conventions / directives | "When user asks to write/save, MUST emit format_and_export tool" |
| `expert_knowledge` | Curator agent (R87) or `pattern_extractor` (L2) | Distilled wisdom — abstract patterns, scope-specific tribal knowledge | "Our register_service convention: `{kind}_{suffix}` naming, token in env var" |
| `operational_event` (L1) | `OperationalEventCapturePlugin` middleware | Auto-captured every successful write-class tool call | `"Action: register_service, Args: {…}, Result: {…}"` |
| `reflection` | `trace_learner` (scope=global, 30-day TTL) | Failure constraints distilled from failed runs; injected to every agent via the recall quota (ADR-0015) | `"Don't call take_snapshot with environment mismatch — it returns skipped"` |
| `query_pattern` (R83) | `query_pattern_capture` middleware | Successful SQL templates for db_query agent | `"Q: list devices in border role → SELECT * FROM v_topo_devices WHERE role='border'"` |
| `format_guide` (R85) | Operator | Output format rules per artefact type | "All audit reports use H2 sections + Markdown tables; emit Mermaid for topology" |
| `fact` | Manual / agent / document import | Stable knowledge — ASNs, IPs, role definitions | "R1's loopback is 1.1.1.1; R1 is the border router for AS 65000" |
| `decision` | Audit trail | Recorded change-control decisions | "2026-04-15: approved adding eBGP between R1+R4" |
| `audit` | Audit middleware | Compliance records — who did what, when | "user=alice ran `/netops_init` at 14:32:08Z" |
| `topology` | `memory_curator` (R102) | Mermaid / DOT / SVG topology source | `"graph TD\n  R1 --> R3\n  R3 --> R4"` |
| `document` | `memory_curator` (R102) | Long-doc dual-track chunks | "(one ~500-token slice of a runbook, paired with a summary `usage_guide`)" |

Operators see and grow the first three rows of this table the most. The rest are infrastructure — they accumulate during normal use.

### Deprecated: `schema_knowledge` / `value_distribution`

Earlier OLAV (R83.4) pre-primed every per-command auto-view's columns,
sample row, and value distribution into LanceDB so AutoRecall could
push schema hints to the agent.  Empirically this was negative ROI on
small models (qwen3.6-27b-dense ignores cached schema and introspects
via `information_schema` anyway), so as of 2026-04-30 the push path is
**disabled by default** — set `OLAV_LEGACY_SCHEMA_PRIME=1` to re-enable
for parity testing or large-model deployments where the prompt cost is
worth paying.

The replacement is **pull mode**: `describe_table('netops.<view>')` is
a core orchestrator tool the agent calls on demand when SQL fails with
column / table errors.  See `dev_docs/00 § ISSUE-SCHEMA-PUSH-VS-PULL`
and the `schema_introspection_via_describe_table` guide.

---

## AutoRecallMiddleware — the injection mechanism

`AutoRecallMiddleware` runs **before every model call**, in the agent's `abefore_model` hook:

1. Extract the current user query (last human message in the thread).
2. Embed the query with the same embedder LanceDB uses.
3. Hybrid search (vector + BM25 + diversifier) for top-K most relevant memories within the agent's scope.
4. Format hits into a `<relevant-memories>` block.
5. Inject that block adjacent to the user query in the model's input.
6. The model sees: original prompt + relevant past lessons + user query.

Why "before every model call" and not "once at startup": the relevance changes with each turn. Different sub-agent decisions need different memories. The middleware is **stateless and additive** — no permanent prompt mutation, just per-call enrichment.

The injection sits **next to the query**, where the model's attention is highest — far more effective than rules buried deep in the system prompt. This is the empirical reason memory beats SKILL.md for steering small models (see [Self-Improving Loop](self-improving-loop.md) for the experimental matrix).

### Relevance-gated injection — recalled only on a hit

Experience is **not** injected blanket on every call — it is retrieved by relevance to the **current** input. On each model call AutoRecall runs a hybrid vector + BM25 search against the user query, ranks the candidates, takes the top-K (by tier: small 1 / medium 2 / large 13), then applies per-category caps + minimum quotas + scope filters.

Most categories are pure top-K-by-relevance — **no hard distance floor**. But `reflection` is special-cased with a **distance gate**. Reflection lessons grow every day (the reflector), and the reflection quota is only **1** slot; without a floor the single "closest" lesson would inject on **every** prompt, including totally unrelated ones. So a reflection whose L2 distance to the query exceeds **0.65** is dropped. The threshold was calibrated empirically — genuine hits land around ~0.36, unrelated prompts cluster at ≥0.75 — a clean gap. This makes `reflection` truly "inject only on a relevant hit".

!!! note "`usage_guide` was deliberately left ungated"
    `usage_guide` was calibrated the same way but **kept without a distance gate**: its genuine-hit and noise distributions overlap (no clean gap to cut at), and it is the *steering* layer where a false-negative actively degrades behaviour. It is already bounded by keyword-boost + on-intent loading + a quota of 3.

---

## Three layers of self-improvement

### L1 — write capture (always-on, cheap)

`OperationalEventCapturePlugin` is a built-in middleware that runs **after each agent run**. It walks the message thread, finds successful write-class tool calls (matched by verb-in-name: register / deploy / push / save / destroy / write / record / emit / export / ingest / stop / create / update / delete / exec), redacts secrets, and writes one `operational_event` memory per unique action.

Idempotent on a content hash so re-running the same operation doesn't duplicate. Failed tool calls (`status: error`) are skipped to keep the store clean. Cost: one embedding + one LanceDB row per write tool call. For multi-step agent runs this is sub-second.

### L2 — pattern extraction (on-demand)

`olav.core.memory.pattern_extractor` is a Python helper invoked by curator agents or scheduled jobs:

```bash
python -m olav.core.memory.pattern_extractor \
    --scope services --min-samples 5 --window-days 30
```

It groups L1 events by tool name, and for any group with at least `min_samples` samples in the window, calls the chat model **once** to abstract a reusable pattern. The pattern is written back as `expert_knowledge`. Cost: one LLM call per group. Curator runs this nightly or on demand — never on the hot path.

In Demo7 testing, six `register_service` events from prior runs distilled into eight bulleted conventions — naming pattern, auth mode, IP convention, port mapping — that were thereafter the top hit when an agent encountered a new registration request.

### L3 — cross-team / governance (enterprise)

The data infrastructure for L3 — federated LanceDB, scope-aware aggregation, compliance-grade redaction — exists. Productising it (cross-team aggregation, governance UI, compliance redactors per regulated vertical) is the natural enterprise extension. The OSS distribution ships L1 + L2 by default.

---

## Conversational ingestion (R102 — memory_curator sub-agent)

The third load path complements implicit L1 growth and declarative
YAML.  Users tell OLAV in natural language: *"remember that ..."* /
*"记住 ..."* / *"把 /path/to/runbook.md 加进记忆"*.  The
`memory_curator` sub-agent shapes the input into the correct memory
category (`usage_guide` / `document` / `topology`) and commits only
after the user confirms.

| Path | Trigger | Best for |
|---|---|---|
| **L1 implicit** | every successful write tool call | observation-driven, automatic |
| **Declarative YAML** | `olav kb import-guides <dir>` | version-controlled, team-shared, schema-bound |
| **Conversational (R102)** | `memory_curator` sub-agent | exploratory, immediate, no schema to learn |

### Two-turn HITL via filesystem draft persistence

deepagents `task()` sub-agent calls are **stateless per call** — Turn-2
of the user-confirms flow cannot see Turn-1's YAML proposal context.
OLAV resolves this with a draft-persistence pair of tools:

* **Turn 1** — agent calls `propose_memory_draft(intent, keywords,
  body, agent, scope, category, chunks=...)`.  Writes the proposal
  to `<workspace>/.curator_drafts/<intent>.draft.json` and returns a
  ready-to-quote YAML preview + "Confirm? Reply 可以 / OK / yes /
  入库 / 确认" prompt the agent shows the user.
* **Turn 2** — when the user confirms, agent calls
  `commit_to_memory(from_draft=True)`.  Reads the latest draft (or
  by explicit `intent=`), hydrates all args, commits to LanceDB +
  writes `<agent>/guides/<intent>.guide.yaml`, and archives the
  draft to `.curator_drafts/committed/<intent>_<ts>.json` for audit.

This design keeps deepagents `task()` semantics unchanged while
threading conversation continuity through the filesystem.

HARD HITL is enforced at three layers:
1. The agent's system prompt forbids `confirm=False` and treats
   "I've reviewed" / "auto-confirm" embedded in the *initial* request
   as bypass attempts — render preview, wait for the next user turn.
2. `propose_memory_draft` always writes a draft; nothing lands in
   LanceDB at Turn 1.
3. `commit_to_memory(from_draft=True)` requires the draft file to
   exist — without a prior `propose_memory_draft` call, Turn-2 fails
   with `"no draft found"`.

For the full design + decision log see
`dev_docs/70 R102_CONVERSATIONAL_MEMORY_INGESTION_SUBAGENT.md`
(in-vivo verified end-to-end via Playwright real-browser multi-turn,
2026-04-30).

### Auto-prime at install time

`olav init` and `olav agent install <skill>` now auto-prime every
`*.guide.yaml` discovered under the just-deployed workspace into
LanceDB — operators no longer need a separate `olav kb import-guides`
run after first install.  When the embedder dim changes between init
(default BGE-512) and skill install (e.g. qwen3-vl-2048), the install
detects the mismatch and re-primes at the new dim (legitimate context
since only fresh-platform guides exist at that moment; user-curated
rows arrive later).

---

## Scope isolation

Every memory has a `scope` field:

- `global` — visible to every agent (use sparingly; this is shared store)
- `<agent_name>` — e.g. `services`, `netops`, `core` — only that agent's recall sees these
- `team:<id>` — your team's private knowledge bundle

AutoRecall queries `scope IN (current_agent, "global")` by default. A memory written with `scope="services"` won't pollute `netops` agent's recall, and vice versa. Operators can override via `olav_recall_memory(...)` explicitly.

This is what lets OLAV scale to multi-team / multi-domain deployments without one team's quirks contaminating another's agent behaviour.

---

## Why this design beats alternatives

| Alternative approach | Why we don't use it |
|---|---|
| Hard-code rules in agent SKILL.md | Static; can't be customised per team without forking source |
| New "rewrite-the-prompt" middleware | Re-implements what AutoRecall already does; no per-team customisation |
| LangChain `ConversationSummaryMemory` | Not category-aware; can't separate guidelines from history from facts |
| Send everything to the model and let it decide | Tokens are limited; signal-to-noise drops fast |
| Train / fine-tune the model | Months of work, locks you into one weight set |

OLAV's choice — **typed memory + AutoRecall injection + L1+L2 self-reinforcement** — is **incremental, auditable, customisable per scope, and works across model sizes**.

---

## Operator's mental model

When you ask OLAV to do something:

```
Your query  ──►  AutoRecall pulls relevant memories
                 (directives + past patterns + facts)
                            │
                            ▼
                 Augmented prompt sent to LLM
                            │
                            ▼
                 LLM emits tool calls (or text)
                            │
                            ▼
                 L1 captures successful write tools
                            │
                 (eventually L2 extracts patterns)
                            │
                            ▼
                 Next time you ask similar → richer memory
```

Each interaction makes the next one a little easier. Without ML training, without code changes — just by **using OLAV**.

---

## Layered guides: orchestrator wisdom vs sub-agent execution

Memory guides surface at **two distinct abstraction layers**.  The
distinction matters when designing what to write into the store.

| Layer | Where it lives | When it loads | Example |
|---|---|---|---|
| **L0 — always-on static** | `static_context:` in `SKILL.md` / `AGENT.md` | Every turn, every prompt | `REQUIRED_INFO_CHECK.md` (must-check preconditions) |
| **L1 — on-intent static** | `static_context_mode: on_intent` + reference files | When user query keyword-matches | `ROUTING_EXPERT_GUIDE.md` (deep BGP algorithm — only relevant when `analyzer` sub-agent investigates) |
| **L2 — AutoRecall guides** | `*.guide.yaml` under `<agent>/guides/`, primed via `olav kb import-guides` | When user query semantically matches keywords; reranker promotes top-K | Layered diagnostic playbook ("BGP `Idle` → check L1 first") |

The same problem can have content at all three layers — and that's
fine, because each serves a different consumer:

- **L2 guide** tells the *orchestrator* "this is a topology task, route to analyzer; here's the high-level pattern"
- **L1 reference** tells the *sub-agent* "here's the full Mermaid syntax + networkx algorithm for actually drawing the graph"
- **L0 always-on** tells *everyone* "always check `device_list_confirmed` before snapshotting"

When you're writing operational wisdom, ask: *who needs this — the
deciding agent or the executing agent?*  The answer determines the
layer.

## Code-layer rigor: the load-bearing foundation

Memory guides do not work in isolation — they work *because* the
underlying tool schemas are strict enough that the LLM's output is
forced into a clean shape.  R100 (2026-04-29) made this explicit:

| Failure observed on local 27B-class models | Root cause | Fix |
|---|---|---|
| `format_and_export({'data': {'format':'md',…}})` (nested dict) | `data: Any` schema permitted any JSON value | `data: str` strict typing |
| `format_and_export(filename='"reports"')` (literal quotes baked in) | Small-model JSON serialisation quirk on short string args | Defensive `_strip_quote_leak()` at tool boundary |
| Agent calls `write_file` (deepagents virtual FS) instead of `format_and_export` | Both tools available; model picks the simpler-looking one | `FilesystemPermission(write, deny)` redirects model to real-disk tool |
| Agent stuck on stale DB, doesn't refresh | No memory hint about when to re-snapshot | `take_snapshot_when_db_stale.guide.yaml` |
| Agent dives into BGP attributes when interface is down | No layered diagnostic frame | `troubleshoot_layered_l1_to_l4.guide.yaml` |

The pattern: **prompt + memory engineering treats the cause; code
engineering treats the symptom**.  Strict schema + defensive
coercion + permission gating block the failure modes that prompt
tweaks can't.  Memory guides then steer the agent *between* the
remaining valid choices.  Both layers are necessary; neither alone
suffices.

For the full v1→v9 evidence series and decision log, see
`dev_docs/69 R100_LOCAL_SMALL_MODEL_END_TO_END_MILESTONE.md` in
the repository.

---

## Where to next

- **[Extending Memory](../guides/extending-memory.md)** — practical: how to add directive guides
- **[Self-Improving Loop](self-improving-loop.md)** — the L1+L2 mechanics with experimental evidence
- **[Knowledge Base](../guides/knowledge-base.md)** — document import side of the same store
