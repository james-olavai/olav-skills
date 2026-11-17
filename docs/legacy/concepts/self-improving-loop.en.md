# Self-Improving Loop

OLAV is **designed for local small models** — and small models repeat their mistakes unless something keeps notes for them. OLAV's answer is a feedback architecture that **learns from its own usage** — no training run and no operator intervention required.

!!! note "This page = the memory layer of a larger system"
    The self-improving loop is OLAV's **memory layer** (the "fast weights" —
    non-parametric, learns every run). It is one of three layers in
    [Continual Learning](continual-learning.md): memory (this page), optional
    consolidation into model **weights** (fine-tuning, gated by a benchmark),
    and **harness** config tuning. Fine-tuning is *optional and out-of-band* —
    this loop needs none of it.

This page explains the layers (L1 → L2 → L3), how they compose, and the experimental evidence that proves they actually steer model behaviour.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|------|--------|
    | C-L3-L1-CAPTURE | `OperationalEventCapturePlugin` auto-captures write-class tool calls as `operational_event` memory | ✅ R98 |
    | C-L3-L2-EXTRACT | `pattern_extractor` distils N samples into reusable `expert_knowledge` pattern via LLM | ✅ R98 |
    | C-L3-DIRECTIVE | Directive `*.guide.yaml` files prime memory via `olav kb import-guides` | ✅ R98 |
    | C-L3-AUTORECALL | Memories auto-injected into model context via `AutoRecallMiddleware` | ✅ v0.15+ |

---

## What "self-improving" actually means here

It does **not** mean OLAV trains the model behind the scenes — that would be expensive, slow, and risky. It means:

- Every successful tool call is **recorded as a memory**.
- Recurring patterns are **distilled into directive memories** by a curator.
- All those memories get **automatically injected** into the prompt next time something similar happens.

After a week of use, your OLAV deployment is *materially smarter* than a fresh install — same model, same code, **richer memory**.

---

## The three layers

```
                              ┌───────────────────────────────────────┐
   USER QUERY ────────────────┤  AutoRecallMiddleware                  │
                              │  (before each model call, pull top-K  │
                              │   relevant memories, inject into prompt│
                              └────────┬──────────────────────────────┘
                                       │
                                       ▼
                              ┌──────────────────────┐
                              │  AGENT + LLM          │
                              │  emit tool_calls      │
                              └────────┬──────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
  ┌──────────────┐           ┌──────────────────┐         ┌─────────────────┐
  │ L1 capture   │           │ Other middleware │         │ tool exec        │
  │ (after each  │           │ (audit, query    │         │ + result back    │
  │ agent run)   │           │ pattern, etc)    │         │ to LLM           │
  │              │           │                  │         └─────────────────┘
  │ Walks        │
  │ messages,    │
  │ captures     │
  │ write-class  │
  │ tool calls   │
  │ as           │
  │ operational_ │
  │ event        │
  │ memories     │
  └──────┬───────┘
         │
         │ accumulates ≥ N similar events
         │
         ▼
  ┌────────────────────────────────────────────────┐
  │ L2 extract  (on-demand or scheduled)            │
  │                                                  │
  │ pattern_extractor.extract_operational_patterns  │
  │  (groups by tool, calls LLM once per group,     │
  │   writes expert_knowledge memory back)          │
  └─────────────────────────────────────────────────┘
                                       │
                                       │ next AutoRecall hit
                                       ▼
                              ┌──────────────────────┐
                              │  Agent now sees both  │
                              │  the directive AND    │
                              │  concrete precedent   │
                              │  AND a distilled      │
                              │  pattern              │
                              └──────────────────────┘
```

### L1 — write-class capture (always-on, hot path)

`olav.plugins.middleware.operational_event_capture.OperationalEventCapturePlugin` runs as a builtin middleware. After every agent run it:

1. Walks the message thread for **AIMessage → ToolMessage** pairs where the tool name matches a write-class verb (register, deploy, push, save, destroy, write, record, emit, export, ingest, stop, create, update, delete, exec).
2. **Redacts secrets** in the args / result (token, password, api_key, secret, credential, auth_header) before persisting — values are replaced with `<redacted>`.
3. Skips failed calls (`status: error`) so the store doesn't accumulate broken examples.
4. Writes one `operational_event` memory keyed by SHA256 of the call summary — so re-running the same op doesn't duplicate.

Performance: one embedding call + one LanceDB row per write tool call, sub-second on Ollama-served local embeddings.

### L2 — pattern extraction (on-demand, batchable)

`olav.core.memory.pattern_extractor.extract_operational_patterns(scope, min_samples, window_days)` is a Python helper. It groups `operational_event` memories by tool name; for any group with at least `min_samples` events in the window, it calls the chat model **once** to abstract a reusable pattern, and writes the result back as `expert_knowledge`.

Run it on demand or schedule it nightly:

```bash
python -m olav.core.memory.pattern_extractor \
    --scope services --min-samples 5 --window-days 30
```

Or invoke from a curator agent via `execute_skill_script` — exactly the same code path. This is **deliberately not a middleware** — LLM calls are expensive, batchable, and need to be auditable, all of which argue for explicit invocation, not hot-path injection.

In Demo7 testing, six `register_service` events from prior runs distilled into eight bulleted conventions:

> - Action is consistently `register_service` requiring four arguments: `name`, `kind`, `endpoint`, and `token_env`.
> - Naming convention follows `{kind}_{suffix}`…
> - Authentication is mandatory via the `token_env` argument…
> - Variance flag: Host addressing is mixed (some `localhost`, some `192.168.100.x`)…

That distilled pattern is now the top hit when an agent encounters a new service registration request.

### Failure-driven constraints (`reflection`)

L1/L2 learn from **successes**. A parallel path learns from **failures**: after every run the audit middleware fires `trace_learner` in the background (fire-and-forget). It reads failed/cancelled runs from `audit.duckdb`, asks the LLM to distil one-line *constraints* ("don't call X when Y holds"), and writes them as `reflection` memories with `scope=global` and a 30-day TTL (ADR-0015). AutoRecall then surfaces them to **every** agent through a reserved `reflection` quota slot — a mistake made under one agent becomes a guardrail for all. Trigger a review on demand with `/trace-review [hours] [limit]`.

!!! note "Wired end-to-end 2026-06-13"
    This loop was write-only for months: constraints were written with `scope=shared:audit` while recall queried `scope=global`, so no agent ever read them back. It now writes `scope=global` with a real embedding and a reserved recall quota.

## The reflector agent — scheduled self-reflection

`trace_learner` (above) learns from **structured** `audit.duckdb` failures. The **reflector** (`admin/reflector` sub-agent) complements it: it **also** mines the plain-text logs, and adds a code-proposal lane. It runs daily via the `reflect` cron job at 4am, activated with `olav cron enable reflect`.

Its anti-hallucination core is `scan_error_signatures`. Instead of loading megabytes of log text into a small model's context (the `api_server.log` alone can be ~200 MB+), it returns a bounded **error-signature histogram**:

1. Pull recent errors from `audit.duckdb` + tail-scan the last ~2 MB of each `.olav/logs/*.log`.
2. **Normalize** each message — strip timestamps / ids / numbers / paths to placeholders — into a stable *signature*.
3. Group + count, and return the top-K signatures (K by tier: 15 / 30 / 60), each with **one** truncated example and a `truncated` flag.

The agent sees `"ERROR X ×47, example: …"` — precise, capped, and un-inventable. It then triages each signature into one of two lanes:

- **KB lane** — a one-line operational lesson → written **directly** as a `reflection` memory (`record_reflection`; `scope=global`, 30-day TTL, quota 1 — self-limiting and reversible); OR a permanent `usage_guide` → written as a **draft** to `.curator_drafts/` for human (HITL) commit via memory-curator (`propose_guide_draft`).
- **Code lane** — a real code defect → a **proposal** (root cause + suggested fix + optional patch) written to `exports/reflections/` (`draft_code_fix`). It **never** edits source, runs git, or applies anything — a human reviews.

!!! note "Reflection writes are semantically de-duplicated"
    A new lesson within L2 distance 0.3 of an existing reflection is skipped, so daily runs over the same recurring errors don't pile up near-identical lessons.

### L3 — cross-team / governance (enterprise extension)

The data infrastructure for L3 — federated LanceDB, scope-aware aggregation, compliance-grade redaction, audit trail of who-saw-what — is in place. **L3 is the natural enterprise differentiator**: aggregating L1+L2 across teams, deploying compliance-tested directive bundles per regulated vertical (telco, fintech, healthcare).

OSS distribution ships L1+L2 by default. L3 is **opt-in business**, not a fork.

---

## Plus: explicit directives (zero-LLM steering)

Sometimes you want to encode a rule **before** the system has any examples to learn from. Drop a `*.guide.yaml` under `<workspace>/<agent>/guides/`, then:

```bash
olav kb import-guides
```

The guide is now a `usage_guide` memory with `scope=global` (if under `core/guides/`) or `scope=<agent>` (otherwise). AutoRecall surfaces it on relevant future queries.

This is how teams encode their conventions ("never inline tokens", "always validate IP plan before write_profile", "use H2 sections in audit reports"). See [Extending Memory](../guides/extending-memory.md) for the full how-to.

---

## Experimental evidence: it actually works

Demo7 Chapter 8 (2026-04-28) tested whether the architecture really steers a small model. Same vague user prompt in every run:

> "Write a bash script to backup running-config from all routers. Use real device names/IPs from the database. Save to exports/scripts/. Include --dry-run + error handling."

| # | Backend | Memory state | Result | Wall-clock |
|---|---|---|---|---|
| 1 | qwen3.6:27b (local) | clean | ❌ narrate-only | 2:51 |
| 2 | qwen3.6:27b (local) | clean (directive prompt by user) | ✅ 118-line script | 2:26 |
| 3 | qwen3.6:27b (local) | clean (max_tokens 16000) | ❌ narrate-only | 2:44 |
| 4 | qwen3.6:27b (local) | clean (SKILL.md hardened) | ❌ narrate-only | 1:52 |
| 5 | deepseek-v4-flash (cloud) | clean | ✅ 126-line script | 2:52 |
| **6** | **qwen3.6:27b (local)** | **directive guide + L1 precedent** | ✅ **150-line script** | **3:40** |

Same model, same prompt, same SKILL.md across runs #1, #3, #4, #6. The only thing that flipped #6 from "narrate-only" to "150-line bash output" was the **memory content** (one directive guide + one L1 capture from the deepseek run #5).

That's the empirical proof. Memory injection — not bigger context, not stronger prompts in SKILL.md — is what makes a 27B local model behave correctly on free-form requests.

---

## Decay and pruning

Memories aren't immortal. The schema includes `access_count` (incremented on every recall hit) and `created_at` so a curator can:

- Demote / drop entries with zero access over the last N days.
- Keep recently-cited memories at higher confidence.
- Promote operational events into expert patterns once a count threshold is reached.

These curators are pluggable — sensible defaults ship; teams can add their own.

---

## Operating principles

1. **Memory is the prompt programming language.** Behaviour rules live in memory, not code. Adding/removing/tuning them is text editing, not refactoring.
2. **L1+L2 grows the store from observation.** You don't have to anticipate every convention — many emerge as you use the system.
3. **Directives kickstart what L1+L2 can't infer up front.** "Don't inline tokens" is hard to learn from observation; easy to write as a guide.
4. **Per-scope isolation prevents pollution.** Team A's memories don't leak into Team B's agent. Same code, different memory, different behaviour.
5. **Auditability by default.** Every memory has origin, confidence, scope, timestamp. `olav_recall_memory` shows what surfaced for any given query.

---

## Where to next

- **[Memory Architecture](memory-architecture.md)** — table schema, categories, AutoRecall internals
- **[Extending Memory](../guides/extending-memory.md)** — practical guide-authoring walk-through
- **[Knowledge Base](../guides/knowledge-base.md)** — document import flows for the same store
