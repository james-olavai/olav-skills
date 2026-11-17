# Extending Memory — Directive Guides for Your Team

OLAV's recall middleware injects relevant **memories** into every model call, but the store starts mostly empty. Two ways to fill it:

1. **Implicit growth** — L1+L2 capture every successful tool call as you use OLAV, then distill those into patterns. Free, automatic. See [Self-Improving Loop](../concepts/self-improving-loop.md).
2. **Explicit directives** — author `*.guide.yaml` files describing your team's conventions, then prime them into memory with one CLI command. Covered here.

Use directives to encode things OLAV can't infer from observation alone:

- "All services must use `token_env`, never inline tokens"
- "Bash scripts go to `exports/scripts/`, never `/tmp`"
- "Audit profiles must include both `bgp_health` and `interface_state` jobs"
- "When user says 'deploy', always show diff first and require approval"

---

## Anatomy of a directive guide

A directive guide is a YAML file with three structured fields and a free-form body:

```yaml
schema_version: 1
intent: write_class_tool_call_directive    # short identifier
agent: core                                 # who owns this guide
keywords:                                   # AutoRecall match hints
  - write
  - save
  - create
  - generate
  - export
  - 写
  - 保存
  - 生成
body: |
  Write-class request → MUST emit tool_call (NOT just describe)

  When the user requests creating, writing, saving, generating, or
  exporting any artefact, you MUST emit a tool_call in the SAME
  AIMessage that fulfils it.

  ANTI-PATTERN — do NOT do this:
    User: "Write a bash script to backup configs"
    AI:   "Now I'll write the backup script…"  [stops, no tool_call]

  CORRECT:
    AI:   format_and_export(format="sh", subdir="scripts", …)

  Tool selection:
    - real-disk artefact → format_and_export
    - workspace file → delegate to admin sub-agent
    - service registration → register_service
    - audit profile → write_profile
```

Field rules:

- **`schema_version: 1`** — required; bumped if the format ever changes.
- **`intent`** — your unique identifier; used as part of the memory ID.
- **`agent`** — primary scope for the guide. AutoRecall surfaces it for that agent.
- **`keywords`** — supplement the embedder's semantic matching with literal-string hints. Mix English + Chinese + your team's jargon. The list is **inclusive** — any one keyword in the user query is enough to boost retrieval.
- **`body`** — the actual content the model will see. Write **for the model**, not for humans:
    - Be directive ("MUST", "ALWAYS", "NEVER")
    - Show anti-pattern + correct pattern side by side
    - Name specific tools and arguments
    - Keep under ~600 tokens — bigger bodies waste recall budget

---

## Where to put guides

Place them under the right agent's `guides/` directory:

```
.olav/workspace/
  ├── core/guides/
  │     └── write_class_tool_call.guide.yaml
  ├── services/guides/
  │     └── service_lifecycle.guide.yaml
  ├── netops/guides/
  │     └── snapshot_diff.guide.yaml
  └── audit/guides/
        └── profile_review.guide.yaml
```

The `<agent>/guides/` convention is what `olav kb import-guides` walks. Files outside this layout are ignored.

If you want a guide visible to **every** agent, put it under `core/guides/` — `olav kb import-guides` writes those with `scope=global` so they surface for every recall.

---

## Priming guides into memory

After dropping a YAML file in place, run:

```bash
olav kb import-guides
```

What happens:

1. Walks every `.olav/workspace/<agent>/guides/*.guide.yaml` under the project workspace.
2. For each file, embeds the body and writes a `usage_guide` memory row keyed by `<intent>-<agent>`.
3. Idempotent — re-running with the same file content is a no-op.
4. Reports `Imported N guide(s) (M skipped)` so you can confirm.

That's it. AutoRecall picks up the new memory on the next agent invocation. **No restart, no rebuild, no code changes**.

---

## End-to-end example

You want OLAV to **always validate IP plans before saving an audit profile**. Two minutes:

```bash
# 1. Create the file
cat > .olav/workspace/audit/guides/ip_plan_validation.guide.yaml <<'YAML'
schema_version: 1
intent: ip_plan_validation_required
agent: audit
keywords:
  - save profile
  - audit profile
  - bgp peering
  - validate ip
  - check subnet
body: |
  Before calling write_profile() with any BGP / OSPF / interface
  validation jobs, you MUST first run a query against
  v_topo_links_clean to confirm the IP plan in the profile
  matches the actual topology subnets.

  Example flow:
    1. execute_sql: SELECT DISTINCT subnet FROM v_topo_links_clean
    2. compare to IPs referenced in the proposed profile yaml_jobs
    3. if mismatch → raise to user, do NOT save
    4. if match → write_profile(name=…, yaml_jobs=…)

  Why: a profile that queries IPs not in topology will silently
  return zero rows — false-green audit. Demo7 Ch6 surfaced this
  failure mode (ISSUE-AUDIT-FALSE-GREEN-EMPTY-RESULTSET).
YAML

# 2. Prime it into memory
olav kb import-guides

# Output:
# Imported 6 guide(s) from .olav/workspace (0 skipped)
```

Next time anyone uses the audit agent and the user query embeds close to those keywords, the directive surfaces in `<relevant-memories>`. The agent now knows the team rule.

---

## Fault-handling override — making OLAV use your team's runbook

A common ask: "OLAV's default BGP-Idle troubleshooting walks L1→L2→L3 in order. But on **our** fleet, 90% of BGP-Idle events are caused by an over-eager auto-remediation script that admin-shuts `ge-0/0/0`. We want OLAV to check that first."

Directive guides are how you encode this. Three things to get right:

### 1. Pick a sufficiently-specific intent

```yaml
intent: bgp_idle_my_company_specific_root_cause
```

Don't use a generic intent like `troubleshoot_layered`. The intent
becomes part of the memory ID, and you want it distinct from
platform defaults so re-imports don't collide.

### 2. Tell the model to override the default — explicitly

The body of your guide is the literal text the model sees. If you
want to override the default L1→L4 ordering, **say so**:

```yaml
body: |
  OVERRIDE the default layered_l1_to_l4 ordering for any BGP-Idle
  request on the prod fleet. Run STEPS 1-3 below FIRST.
  Only fall through to default layered diagnosis if all three
  return zero rows.

  STEP 1 (90% hit rate): check if ge-0/0/0 was admin-shut by
  the auto-remediation script.
    SELECT device_name, interface, admin_state, link_state
    FROM netops.v_show_interfaces_terse_auto
    WHERE device_name = '<host>'
      AND interface LIKE 'ge-0/0/0%'
      AND admin_state = 'down';
  If row found → ROOT CAUSE: auto-remediation false-positive.
  Recommend rolling back via change request CR-#####.

  STEP 2 (~7%): ASN config drift on MX204 with Junos 21.4 —
  the auto-config-merge has been observed to drop `peer-as`.
    SELECT raw_output FROM netops.raw_output_store
    WHERE command = 'show configuration protocols bgp'
      AND device_name = '<host>'
      AND raw_output NOT LIKE '%peer-as%';

  STEP 3 (~3%): MTU mismatch on jumbo path to partner ASN 65501.
    SELECT interface, mtu FROM netops.v_show_interfaces_auto
    WHERE device_name = '<host>' AND mtu < 9000;
```

### 3. Choose keywords that match how operators actually phrase the issue

```yaml
keywords:
  # User-side phrasing
  - BGP Idle
  - BGP 邻居 Idle
  - peer Idle
  - eBGP Idle
  - bgp neighbor down
  - 路由邻居故障
  # Domain-specific identifiers operators use
  - mycompany prod
  - prod fleet
  - prod cluster
```

**Both English and Chinese**, plus any internal jargon. AutoRecall's
hybrid retrieval fires on any one keyword — there's no penalty for
listing many.

After `olav kb import-guides`, every BGP-Idle request from the
prod fleet picks up your STEPS 1-3 ahead of the platform's
generic L1→L4 sequence. The override is automatic; agents don't
need to be told to use it.

---

## Pitfalls — things that look fine but bite later

| Symptom | Likely cause | Fix |
|---|---|---|
| Guide never surfaces | Keywords too narrow / wrong language | Add 5+ keyword variants in operators' actual language |
| Guide fires on wrong queries | Keywords too generic (e.g. `error`, `down`) | Tighten — use multi-word phrases like `BGP Idle`, not `Idle` |
| Multiple guides collide on same query | Two guides share an intent prefix | Use distinct `intent:` ids; the prefix is part of the memory key |
| Guide fires but model ignores it | Body too long (> ~600 tokens) | Trim — recall budget is finite; long guides starve other recall |
| Re-import doesn't update body | Idempotent on `<intent>-<agent>` | If you renamed intent, run `olav kb status` to spot orphans |
| Guide injected into wrong agent | File under wrong `<agent>/guides/` dir | Move to correct agent dir + re-import; `core/guides/` for cross-agent |
| Override doesn't actually override | Body says "see also default" or hedges | Use imperative language: "OVERRIDE", "FIRST", "ONLY fall through if…" |

Verify a guide is wired correctly:

```bash
olav kb search "BGP Idle prod fleet"  # must surface YOUR guide top-3
```

If it doesn't, your keywords miss the user's actual query.

---

## When to use `expert_knowledge` instead of `usage_guide`

Both surface via AutoRecall but with different recall quotas
(per turn: 3 usage_guide + 2 expert_knowledge):

| You're encoding… | Category | Why |
|---|---|---|
| Workflow / decision tree ("how to handle X") | `usage_guide` | Tied to user-intent; directive in nature |
| Domain knowledge ("how BGP path attributes are evaluated") | `expert_knowledge` | Shared infrastructure, less intent-bound |
| Compliance rule ("never store keys in logs") | `usage_guide` | Workflow override |
| Vendor quirk ("Junos 21.4 drops peer-as on auto-merge") | `expert_knowledge` | Background knowledge |

Most teams writing fault-handling overrides want `usage_guide` —
which is the default for `*.guide.yaml` files. Use
`*.expert.yaml` only when the knowledge is platform-domain rather
than workflow-directive.

---

## Sharing guides across teams

Directives are plain YAML files. Bundle and distribute them as you'd distribute any text artefact:

```bash
# Author guides in a separate repo
git clone https://gitea.team.local/our-org/olav-directives /tmp/d

# Prime from any directory
olav kb import-guides /tmp/d
```

This makes **OLAV directive bundles** a real artefact for sale, internal distribution, or compliance certification. A "fintech regulatory pack" is just a folder of `.guide.yaml` files.

The CLI accepts an optional path:

```bash
olav kb import-guides /path/to/directives    # explicit dir
olav kb import-guides                        # default: .olav/workspace
```

---

## Inspecting your memory state

Before / after auditing what's in the store:

```bash
olav kb status                  # totals by category / origin / scope
olav kb search "BGP register"   # natural-language semantic search
```

Or directly via Python:

```python
from olav.core.memory import get_store, MEMORY_TABLE

tbl = get_store().get_table(MEMORY_TABLE)
guides = tbl.search().where("category = 'usage_guide'", prefilter=True).to_list()

for g in guides:
    print(f"{g['scope']:10s}  {g['id']}  ← {g['text'][:80]}")
```

---

## When directives don't work — fall back to L1+L2

Directives are **proactive** — you write them ahead of time. But you can't anticipate every wrinkle. For things you discover after the fact, OLAV's L1+L2 layers handle them automatically:

- Every successful tool call gets captured as a precedent (L1).
- After enough captures, a curator run distils them into a pattern (L2).
- The pattern surfaces alongside (or instead of) the directive on future queries.

So you don't need to write a directive for *every* convention — just the ones you can articulate up front. The rest emerge from observed behaviour.

See [Self-Improving Loop](../concepts/self-improving-loop.md) for the full L1+L2 details.

---

## Cheatsheet

| Task | Command |
|---|---|
| Author a directive | Drop `.guide.yaml` in `<agent>/guides/` |
| Prime into memory | `olav kb import-guides` |
| Inspect store | `olav kb status` / `olav kb search "<query>"` |
| Bundle for sharing | Tar the `*.guide.yaml` files; receiver runs `olav kb import-guides /path/to/extracted` |
| Update existing | Edit the YAML, re-run `olav kb import-guides` (idempotent on memory ID) |
| Force scope | Files under `core/guides/` get `scope=global`; otherwise `scope=<agent>` |

---

## Related

- **[Memory Architecture](../concepts/memory-architecture.md)** — what categories exist, how AutoRecall works
- **[Self-Improving Loop](../concepts/self-improving-loop.md)** — the L1+L2 layers
- **[Knowledge Base](knowledge-base.md)** — document import side of the same store
