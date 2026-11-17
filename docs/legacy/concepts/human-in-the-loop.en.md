# Human-in-the-Loop (人机共治)

OLAV is built on a deliberate design principle: **LLM agents do not replace engineers — they amplify them**. The human stays in the loop, owns the decisions, and signs off on every consequential action. Agents are tools, not substitutes.

This page explains *why* this matters, *where* in the platform you'll see it, and *how* it shapes every interface OLAV exposes.

---

## The Principle

> **The engineer decides. The agent assists.**

Every workflow in OLAV is built around three roles:

| Role | What they do | Authority |
|---|---|---|
| **Sim agent** | Drafts the change (TCF, plan, query) from intent + structured context | None — output is a *proposal* |
| **Lab agent** | Validates the draft against rules, vendor parsers, digital-twin runs | None — output is *evidence* |
| **Engineer (you)** | Reads sim's draft, reviews lab's evidence, decides what to change, when to deploy | Sole decision-maker |

OLAV's contract: **no agent ever takes a consequential action without the engineer's go-ahead**. Drafts, findings, lab results — all of these are *artifacts the engineer reads*. Deploy, approve, escalate — these are *decisions the engineer makes*.

---

## Why LLM Cannot Replace the Engineer

Three reasons that hold today and likely for the foreseeable future:

### 1. LLMs miss context the engineer carries silently

Every production environment has unspoken baseline assumptions: "we already have AS numbers configured on every router", "interface IPs are set in baseline templates", "this customer has a specific export-policy convention". An LLM doesn't know which of these are true for *your* fleet on *this* day. The engineer does.

### 2. Liability stops at a person, not a model

If a change brings down a data center at 03:00, the post-mortem asks "who approved this?" That answer cannot be "a 70B-parameter model said yes". Compliance frameworks (SOC 2, ISO 27001, PCI), change advisory boards, and regulatory environments require a named human accountable for the decision. OLAV is built for that reality, not against it.

### 3. The LLM's confidence is uncalibrated

LLM agents emit confident-sounding text whether they're right or hallucinating. The engineer can read a finding and judge "is this real or is the model spinning out?". An auto-loop that trusts the LLM's verdict skips that judgment — and skips the value the engineer brings.

OLAV's design conclusion: **don't build the auto-loop**. Build the artifacts, expose them clearly, let the engineer drive.

---

## What "Co-Governance" Looks Like in Practice

A canonical example is the netops change-plan flow — the [analyzer](../netops/netops-agent/analyzer.md) drafts a plan and the engineer approves before anything is applied — but the same pattern recurs across OLAV:

### Pattern: shared artifact + on-demand agents

OLAV defines a shared, persistent **artifact** (TCF spec, audit report, runbook), and each agent is invoked **on-demand by the engineer** to mutate or annotate it.

```
engineer asks sim → sim writes draft into artifact
engineer reads artifact
engineer asks lab → lab annotates artifact with findings + evidence
engineer reads artifact
engineer decides:
  • edit artifact directly
  • ask sim for a targeted revision
  • ask lab to re-validate
  • approve and proceed
```

There is no `cab_iterate(spec, max_iter=3)` composite. There is no convergence detector. There is no "agent decides when to stop". Each step is **one engineer-triggered call**, and the engineer reads the result before deciding what's next.

### Pattern: advisory, not corrective

When a lab finding says "Junos eBGP requires `set routing-options autonomous-system <N>`", that's **advisory**. The lab does not block the pipeline. It records the finding alongside its other evidence and lets the engineer decide:

* "Yes, fix it" → ask sim to add the line.
* "No, that's baseline-assumed in our environment" → accept and move on. The finding stays in the artifact as an audit caveat.
* "Let me think about it" → no action; the finding is patiently waiting next time.

The engineer is never bullied into action by a finding's severity. `severity=blocker` is a strong signal, not an executable instruction.

### Pattern: deterministic-where-possible, LLM-where-necessary

OLAV uses LLMs only where their fuzzy reasoning is required (intent extraction, prose explanation). Wherever the underlying logic is rule-shaped — protocol checklists, schema enforcement, IP allocation, command translation — OLAV uses **deterministic Python** instead. This makes the agent's behavior predictable and auditable.

In the CAB workflow, the prod-CLI review is rule-based regex matching, not an LLM call. The translator that maps prod commands to lab commands is deterministic. Only the initial *draft* generation involves LLM judgment, and that draft is always read by the engineer before anything happens.

---

## What This Buys You

### Predictable behaviour

Because agents are tools and not autonomous actors, you can reason about what each invocation will and won't do. There is no "the agent went on a spree" failure mode in OLAV — there's only "the engineer asked the agent to do X, and the agent did exactly X".

### Audit trails that satisfy compliance

Every artifact tracks `revision_count`, `last_revised_at`, `last_revised_by`. Every lab run writes a journal with timestamped phase entries. Every operator decision is a discrete event. CAB approvers, auditors, and post-mortems read this trail and reconstruct exactly what happened.

### Safe rollout to production

A team adopting OLAV doesn't need to "trust the AI" before letting it touch real infrastructure. They use OLAV the way they'd use any other tool: the engineer in the seat is the same person who'd type the commands manually, just faster because the agent drafts and verifies for them.

---

## What This Costs You

OLAV is not "ChatOps that auto-deploys". If your goal is "AI runs the network without anyone watching", **OLAV is the wrong choice**. The platform deliberately won't go there.

The trade-off is:

| You get | You give up |
|---|---|
| Predictable, auditable agent behaviour | "Set it and forget it" autonomy |
| Compliance-friendly accountability | Pretty `100% AI-driven` marketing copy |
| Engineer trust through transparency | Speed-of-light deployment without review |
| Useful drafts and verification | The fantasy that LLMs can take over |

For most teams running real infrastructure under real change-management, this is the right trade. For teams who prefer to gamble, OLAV is not the platform.

---

## See Also

* **[NetOps Analyzer](../netops/netops-agent/analyzer.md)** — a canonical end-to-end example of HITL: the analyzer drafts a change plan (CLI per device + rollback + post-checks + risks), the engineer reviews it, and any apply step proceeds only after explicit approval.
* **[Security Model](security-model.md)** — how OLAV enforces the "no consequential action without engineer go-ahead" contract at the permissions layer.
* **[Self-Improving Loop](self-improving-loop.md)** — how agents learn from past usage *without* compromising the HITL principle (memories advise, never decide).
