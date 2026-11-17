# Continual Learning

OLAV keeps getting better at *your* operations the more it runs — without you
babysitting it. It does this through **three complementary learning layers**
that operate at different speeds and are governed so they reinforce each other
instead of fighting. This page is the umbrella; each layer has its own deep-dive.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|------|--------|
    | C-CL-MEMORY | Memory layer learns continuously from usage with no fine-tune ([Self-Improving Loop](self-improving-loop.md)) | ✅ |
    | C-CL-HYGIENE | Stale failure lessons are demoted / superseded / corrected so a fixed problem stops blocking | ✅ |
    | C-CL-WEIGHTS | Experience can be exported to a clean dataset and consolidated into model weights, gated by a benchmark | ✅ mechanism |
    | C-CL-HARNESS | Harness telemetry drives benchmark-gated, human-approved config tuning | ✅ mechanism |
    | C-CL-GOVERNANCE | The three layers have an explicit layering, precedence, and change-authority contract (ADR-0016) | ✅ |

---

## The idea: fast advice, slow habits, and a stable constitution

A useful mental model is complementary memory systems: a fast episodic store,
slow-changing skills, and a fixed rulebook. OLAV mirrors this:

| Layer | Analogy | What changes | How fast | Fine-tune? |
|---|---|---|---|---|
| **Memory** | working advice / hippocampus | injected lessons, guides, query patterns | continuous | no (non-parametric) |
| **Weights** | habits / instinct | the model itself | periodic | yes (optional) |
| **Harness** | constitution | budgets, `top_k`, caps, which machinery runs | rarely | no (config) |

Each layer improves through the same shape — **reflect → propose → gate →
apply** — but with an *escalating bar to change it*: the memory layer edits
itself cheaply, weights require a benchmark, and the harness requires a
benchmark **and** a human. Nothing rewrites its own constitution.

---

## Layer 1 — Memory (fast, non-parametric)

The **[Self-Improving Loop](self-improving-loop.md)** captures what happens
during runs, distils recurring patterns into reusable lessons, and injects the
relevant ones into the prompt on the next similar task (AutoRecall). Same model,
same code — a week-old deployment is materially smarter than a fresh install
because its **memory** is richer. No training, no operator work.

**Learning also means forgetting the right things.** A lesson learned from a
*transient* failure ("service X was unreachable") must not keep blocking the
task after X is fixed. OLAV's memory hygiene handles this automatically:

- **Transient-failure demotion** — an environment failure (connection refused,
  permission denied, service down) is stored with a short lease + capped
  confidence, not a durable 30-day rule.
- **Supersession** — if the tool a failure blamed has since succeeded, that
  stale lesson is suppressed at recall time, so a fixed service stops blocking
  immediately.
- **Correction-aware** — "X works again" is recognised as a *correction* of "X
  failed", written and allowed to retire the stale lesson rather than being
  discarded as a near-duplicate.

See [Memory Architecture](memory-architecture.md) for the store and categories.

---

## Layer 2 — Weights (slow, parametric consolidation)

The memory layer is "fast weights". Periodically, the durable lessons it has
accumulated can be **consolidated into the model's actual weights** — the
slow-weights handoff:

```
audit + memory ─▶ clean, encrypted dataset export ─▶ fine-tune (LoRA)
                                                         │
   deploy ◀── keep/discard benchmark gate ◀─────────────┘
     │
   consolidate: lessons now baked into weights step back in recall
```

1. **Export** — successful trajectories, preference pairs, and cleaned memory
   become a training dataset. Transient/superseded/low-confidence noise is
   filtered out; secrets are redacted; the export is encrypted and access is
   OLAV-controlled.
2. **Fine-tune** — a LoRA adapter teaches the model OLAV's tool-calling and
   long-chain idioms. Delivered in two tiers: an **open** fine-tuned model for
   the community floor, and a **commercial fine-tuning service** on larger
   models.
3. **Benchmark gate** — a new adapter is kept **only if** it beats the baseline
   on a fixed benchmark and does not regress a frozen suite (anti-forgetting).
   A worse adapter is discarded, never deployed.
4. **Consolidation rebase** — once a lesson is baked into weights, the matching
   memory row *steps back* in recall (de-weighted, not deleted) so the fast and
   slow layers don't double-inject the same knowledge. If the lesson recurs
   anyway, it re-surfaces — a signal the fine-tune didn't take.

Because the model is a client of strict, typed tools, OLAV never *depends* on a
fine-tune: better weights reduce mistakes, the tool contracts still catch the
rest.

---

## Layer 3 — Harness (config tuning)

The **[Agent Harness](agent-harness.md)** is the scaffolding around the model —
context budget, how many memories to inject (`recall_top_k`), result caps, which
middleware run. These are calibrated defaults per model tier. OLAV records
structured **harness telemetry** per run (which knobs were in effect, recall
counts, budget skips, grader verdicts, tool-selection failures), all joinable to
the run's outcome.

From that data OLAV can **propose** a knob change ("small-tier runs at
`recall_top_k=1` fail more often — try 2"), validate it through the same
benchmark gate, and apply it as a **reversible config override** — but only
after a human approves it. Structural and safety invariants, and the fixed
benchmark itself, are never auto-tuned.

---

## Governance: why the layers don't conflict

Three things that regulate behaviour could conflict if left implicit. OLAV makes
the contract explicit ([ADR-0016]):

- **Placement** — hard/safety/structural rules live in the harness; learnable
  domain lessons live in memory; repeatedly-validated competence graduates to
  weights. Conflict comes from putting a rule in the *wrong* layer, not from
  having three.
- **Precedence** — hard harness constraints and output graders outrank injected
  memory advice, which outranks the model's baked-in prior. Safety is never
  overridable by a learned lesson.
- **Change-authority** — inversely proportional to a layer's authority: memory
  self-edits (cheap, reversible), weights need the benchmark gate, the harness
  needs the gate **and** a human.

The result is one self-improving system that adapts continuously (memory),
consolidates deliberately (weights), and tunes its own scaffolding carefully
(harness) — each at the right speed and under the right safeguards.
