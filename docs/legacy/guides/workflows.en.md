# Multi-Step Workflows

A workflow chains several sub-agents into one verified deliverable — for
example: *draft a change plan → sanity-check it against live state → formally
validate it with Batfish* — from a single natural-language request. Workflows
are declared as **YAML data**, not prompt prose, and the platform renders them
deterministically into the domain orchestrator's system prompt.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-WF-01 | Workflows load from `<agent_dir>/workflows/*.workflow.yaml` | ✅ main |
    | C-WF-02 | Invalid workflow files are skipped, never break agent startup | ✅ main |
    | C-WF-03 | netops ships `change_plan` and `decommission_impact` workflows | ✅ main |
    | C-WF-04 | Repeated failing tool calls are circuit-broken (3× short-circuit / 6× hard stop) | ✅ main |

---

## Using the shipped workflows (netops)

You don't invoke a workflow explicitly — phrase the request naturally and the
orchestrator matches it against each workflow's trigger.

**Change-plan verification chain** (add / modify / redundant):

```bash
olav --agent netops "Draft a change plan to add a redundant eBGP uplink \
  to the AARNet peer on the 'alpha' border router"
```

**Decommission impact chain** (decommission / remove / shut down):

```bash
olav --agent netops "Decommission the branch switch alpha-br-3560 — draft \
  the removal plan and tell me what we lose"
```

Both run three delegations and return the results stacked under fixed
headers:

```
## Draft                              ← analyzer: plan saved to exports/change_plans/
## Pre-check (reporter)               ← state sanity + independent blast-radius recomputation
## Formal verification (Batfish)      ← subnet conflicts, BGP compatibility, reachability verdict
```

The pre-check tier answers in seconds from the state database; the Batfish
tier is the formal config-semantics verdict. If Batfish isn't running, that
step degrades to setup instructions — the plan and pre-check still arrive.

## Authoring a workflow

Drop a `*.workflow.yaml` under your domain agent's `workflows/` directory:

```yaml
schema_version: 1
name: change_plan
title: Change-plan workflow — draft, pre-check, formally verify
trigger: 'change-plan intent for ADDING or MODIFYING config ("add / modify /
  redundant") — a change plan is not done until it is verified'
artifact: 'the plan FILE PATH (exports/change_plans/<file>.md, found in
  step 1''s reply); never paste the plan content itself between steps'
steps:
  - agent: analyzer                # must be a sub-agent declared by this orchestrator
    prompt: <the original user request>
    returns: its reply names the saved plan path — extract it.
  - agent: reporter
    prompt: '"Pre-check the change plan at <path>: …"'
  - agent: simulator
    prompt: '"Validate the change plan at <path> with Batfish: …"'
assembly:
  headers: [Draft, Pre-check (reporter), Formal verification (Batfish)]
on_failure: If a step errors, include what you have and note the failed
  step. Never retry a step more than once; never reorder.
```

Validation is forgiving by design: a malformed file, a step referencing an
undeclared sub-agent, or a header-count mismatch makes that workflow **skip**
(the agent falls back to normal single-agent routing) — it never blocks
startup.

### Authoring rules (hard-won)

1. **Artifacts travel by reference.** Steps exchange a file path or snapshot
   ID — never pasted content. Small local models lose payload round-trips.
2. **Every step agent needs a machine-extractable output contract.** The next
   step can only be prompted with what the previous reply *names*. If a
   sub-agent answers in prose without naming its artifact, give it an
   explicit final-line rule (e.g. `PROFILE_PATH: <path>`) *before* writing
   the workflow.
3. **Audit the orchestrator's existing hard rules first.** A mature
   orchestrator rule like "the sub-agent's reply IS the final answer" will
   win against an injected workflow — measured, not speculated. Restructure
   the rule or don't compose in that domain.
4. **Keep triggers disjoint.** Two workflows claiming the same intent word
   make the choice a coin flip (`change_plan` owns add/modify; 
   `decommission_impact` owns remove/decommission).
5. **≤2–3 workflows per domain.** Each rendered workflow adds ~30–40 lines
   to the orchestrator prompt.
6. **2–4 steps, each an existing sub-agent's bounded job.** Open-ended
   exploration and self-contained loops don't belong in workflows.

## How this relates to memory-based steering

| Layer | Mechanism | Use for |
|---|---|---|
| Control flow — *which steps* | workflow YAML → system prompt | fixed, known-shape sequences |
| In-step behavior — *doing a step well* | knowledge-base guides (auto-recalled) | e.g. "redundancy plans must cite a blast-radius number" |
| Evolution — *discovering new workflows* | trace review → human-approved YAML | turning repeated manual sequences into declarations |

Don't put control flow into memory guides: recall is similarity-ranked and
quota-capped, so orchestration instructions would only *sometimes* be
present — measured at ~33% follow-through versus 100% step execution for
system-prompt-rendered workflows on a 31B-class model.

## Reliability guardrails

Workflows run under the platform's tool-loop circuit breaker: an identical
failing tool call short-circuits after 3 consecutive failures and hard-stops
the run with an honest partial-result message after 6 — a corrupted
tool-call emission can no longer snowball into a context-overflow crash.
