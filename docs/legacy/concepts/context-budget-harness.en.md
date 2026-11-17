# Context Budget Harness — Multi-Layer Token Injection Governance

The **Context Budget Harness** is OLAV's defense system for protecting the LLM context window from being overwhelmed by excessive token injection. It operates independently of the [security harness](agent-harness.md) and is enforced at five distinct architectural layers — from individual tool output to the system prompt structure.

!!! abstract "Why This Matters"
    A small-tier LLM has ~8,000 tokens of context. An unconstrained single SQL query result could return hundreds of rows; a naive script might emit megabytes of text; a bloated tool docstring adds 300 tokens of overhead on every single invocation — before the first real work token is spent.

    The Context Budget Harness ensures that **the context cost of any single operation is bounded**, making small-model agents viable for production workloads.

---

## Architecture Overview

```
Agent Turn
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1 — Tool Registration Budget (ARCH-18 #1)                 │
│  @tool docstrings ≤ 20 lines · full docs behind tool_help()      │
│  ~150-300 tokens saved per tool, paid on every invocation        │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 2 — Static Context Injection Budget (ARCH-17 + ARCH-20)   │
│  SKILL.md body ≤ 350 lines · always-mode refs ≤ 200 lines        │
│  on_intent refs ≤ 500 lines · lazy refs fetched on demand        │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 3 — Memory Injection Tier Limits (ARCH-19)                │
│  AutoRecall top_k: small=1 / medium=2 / large=13                 │
│  (budget-guard skip logic exists + unit-tested but is DORMANT —   │
│   no budget monitor is wired at runtime; tier top_k is the control)│
└─────────────────────────────┬────────────────────────────────────┘
                              │ tool calls
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 4 — SQL / Search Row Limits (ARCH-16)                     │
│  execute_sql: small=10 / medium=20 / large=50 rows               │
│  search_logs hard ceiling: 500 rows across all tiers             │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 5 — Tool Return Compaction (ARCH-18 #2)                   │
│  execute_cli_parallel: 4,000 chars compact / full=True override  │
│  diff_configs: 40-line cap / full=True override                  │
│  api_request: 50-entry page cap on list responses                │
│  skill_runner stdout: 512 KB hard ceiling                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Tool Registration Budget (ARCH-18 #1)

Every `@tool`-decorated function has its docstring and Pydantic argument schema serialized into the model's tool list on **every agent invocation**. The schema alone costs 150–300 tokens per tool. With 8 tools, that is ~2,400 tokens of permanent overhead — 30% of a small model's entire context budget — before a single user message is processed.

**The rule:** `@tool` docstrings must stay ≤ 20 lines. Full usage documentation lives behind `tool_help('<name>')` and is only fetched on demand.

```python
@tool
def olav_recall_memory(query: str, category: str | None = None, ...) -> str:
    """Query long-term memory for past experiences, decisions, and network events.

    Hybrid semantic search (vector + BM25 + recency). Use natural language.
    Call once at the start of an investigation or change plan.
    Full usage: tool_help('olav_recall_memory').          # <-- pointer, not content

    Args:
        query:    Natural language query ("BGP failures on R1").
        category: Optional category filter. None = all categories.
        ...
    """
```

**Enforcement:** `tests/governance/test_docstring_budget.py` — AST-parses each registered `@tool` file, fails CI if any docstring exceeds 20 lines.

---

## Layer 2 — Static Context Injection Budget (ARCH-17 + ARCH-20)

OLAV injects two kinds of static text into the system prompt before the first agent turn:

- **SKILL.md body** — the agent's role description, tool table, and workflow instructions
- **`static_context:` reference files** — supplementary documents declared in the SKILL.md frontmatter

Both are injected verbatim. There is no LanceDB retrieval, no top-k gating.

### SKILL.md Body Budget

| Limit | Value | Enforcement |
|-------|-------|-------------|
| Max body lines | 350 | `test_token_injection_limits.py` |

Move workflow detail into `static_context:` reference files if the body grows too large.

### Reference File Budget by Mode

```yaml
# SKILL.md frontmatter example
static_context:
  - path: ./references/change_plan_authoring.guide.yaml
  - path: ./references/schema_introspection.guide.yaml
static_context_mode: on_intent   # always | on_intent | lazy
```

| Mode | Injection Trigger | Line Budget |
|------|------------------|-------------|
| `always` | Every invocation, unconditionally | ≤ 200 lines |
| `on_intent` | First turn, when model output matches keywords | ≤ 500 lines |
| `lazy` | Explicit `get_static_context(name)` tool call | Uncapped |

Use `lazy` for large reference documents (API specs, comprehensive schemas). The model can fetch them on demand without paying the cost on every turn.

**Enforcement:** `test_token_injection_limits.py::test_static_context_always_mode_files_within_budget` and `test_static_context_on_intent_files_within_budget`.

---

## Layer 3 — Memory Injection Tier Limits (ARCH-19)

Long-term memories retrieved by `AutoRecall` are injected into each turn's system prompt. Without limits, a dense memory store causes retrieval results to grow proportionally with accumulated operational history.

### Tier-Aware top_k

| Tier | Context Budget | Recall top_k |
|------|---------------|-------------|
| small | 8,000 tokens | 1 |
| medium | 32,000 tokens | 2 |
| large | 200,000 tokens | 13 |

### Budget Guard (Skip Recall When Context Is Tight) — dormant

!!! warning "Logic exists and is unit-tested, but is NOT active at runtime"
    `AutoRecallMiddleware` accepts an optional `budget_monitor`; when one
    is attached, on turns beyond the first it skips retrieval if remaining
    headroom falls below a tier threshold. **However, nothing wires a
    monitor at runtime** — the `ContextBudgetMonitor` that would supply
    headroom readings was removed as dead code (2026-06-13), so
    `_should_skip_for_budget()` always returns `False` in production (see
    `test_missing_budget_monitor_never_skips`). The only active recall
    bound today is the tier-based `top_k` above. Treat this section as a
    design hook, not a live defense, until a monitor is wired back in.

```python
# Tier thresholds the guard WOULD use if a monitor were attached
# (TIER_DEFAULTS.recall_skip_headroom_pct):
# small:  skip if headroom < 20%
# medium: skip if headroom < 10%
# large:  never skip (0%)
```

**Tests:** `test_autorecall_tier.py` (top_k bounds — active), `test_autorecall_budget_guard.py` (skip logic — exercised only with a stubbed monitor; `test_missing_budget_monitor_never_skips` documents the real runtime path).

---

## Layer 4 — SQL / Search Row Limits (ARCH-16)

Database queries are the primary source of unbounded data injection. A naïve `SELECT *` against a 50,000-row interface table would return more tokens than a small model's entire context budget.

### execute_sql Tier Limits

| Tier | Max Rows |
|------|---------|
| small | 10 |
| medium | 20 |
| large | 50 |

```sql
-- Agent query (no LIMIT clause needed — the harness adds it)
SELECT device, interface, status FROM interfaces WHERE status = 'down';

-- Actual execution adds: LIMIT 10  (small tier)
```

### search_logs Hard Ceiling

Regardless of tier, `search_logs` returns at most **500 rows**. Log searches can match millions of entries; the hard ceiling prevents a single investigation query from saturating context.

**Enforcement:** `test_round41_arch16_tool_limits.py` — verifies tier-aware limits and the hard ceiling via AST and import inspection.

---

## Layer 5 — Tool Return Compaction (ARCH-18 #2)

Tool outputs are the last line of defense. Even with row limits on SQL, other tools can return large raw text. Each tool with potentially large output implements a compact-by-default / `full=True` override pattern.

### execute_cli_parallel

```python
# Compact mode (default): truncates per-device output at 4,000 characters
result = execute_cli_parallel(devices=[...], command="show bgp summary")

# Full mode: returns complete output (use when debugging)
result = execute_cli_parallel(devices=[...], command="show bgp summary", full=True)
```

The compact output includes a `"[truncated at 4000 chars — re-run with full=True]"` hint so the model knows what to do next.

### diff_configs

Diffs are capped at **40 lines** in compact mode. Configuration diffs can run into thousands of lines for large devices; the cap surfaces the most relevant changes and prompts the model to request `full=True` if needed.

### api_request (DRF Paginated APIs)

First-page list responses are capped at **50 entries**. Pagination metadata (`count`, `next`) is preserved so the model can request subsequent pages if needed.

### skill_runner stdout (execute_skill_script)

All script subprocess output is capped at **512 KB** per stream (stdout + stderr). This is a hard ceiling enforced by the runtime — scripts that return more than 512 KB of text have a design problem and should use internal pagination.

**Enforcement:** `test_compact_returns.py` (compact constants and logic), `test_token_injection_limits.py::test_skill_runner_stdout_cap_value_via_ast` (512 KB ceiling via AST).

---

## Governance Tests as Living Specification

Every budget and limit described above has a corresponding governance test that fails CI if the constraint is violated. This makes the specification executable:

| ARCH ID | Test File | What It Guards |
|---------|-----------|---------------|
| ARCH-16 | `test_round41_arch16_tool_limits.py` | SQL/search row limits |
| ARCH-17 | `test_round38_progressive_disclosure.py` | load_reference slicing, tool_help brief mode |
| ARCH-18 | `test_docstring_budget.py` | `@tool` docstring ≤ 20 lines |
| ARCH-18 | `test_compact_returns.py` | CLI/diff/API compact return constants |
| ARCH-19 | `test_autorecall_budget_guard.py` | AutoRecall budget-aware skip |
| ARCH-19 | `test_autorecall_tier.py` | top_k tier bounds |
| ARCH-20 | `test_token_injection_limits.py` | SKILL.md body, static_context refs, stdout cap |

The test suite runs on every commit. A new tool, guide file, or script that violates any layer fails immediately — no human review required to catch token budget regressions.

---

## Adding a New Tool or Reference File

When extending OLAV with new capabilities, follow these rules to stay within budget:

=== "Adding a @tool"
    1. Keep the docstring ≤ 20 lines
    2. End the docstring with `Full usage: tool_help('<name>').`
    3. Put detailed parameter descriptions in the `tool_help` knowledge base
    4. Run `uv run pytest tests/governance/test_docstring_budget.py`

=== "Adding a static_context reference"
    1. Choose the right mode: `always` (≤ 200 lines) / `on_intent` (≤ 500 lines) / `lazy` (uncapped)
    2. Prefer `lazy` for comprehensive references (API specs, large schemas)
    3. Run `uv run pytest tests/governance/test_token_injection_limits.py`

=== "Adding a script (execute_skill_script)"
    1. Return structured JSON rather than raw text wherever possible
    2. Add internal row/size limits if the script queries a database
    3. The 512 KB stdout ceiling is a last resort — well-designed scripts stay well under 50 KB

---

!!! info "Related Documentation"
    - [Agent Harness (Security / Execution Governance)](agent-harness.md) — AAA, sandbox, injection scanner
    - [Memory Architecture](memory-architecture.md) — LanceDB, AutoRecall, tier-aware retrieval
    - [Build a Skill](../guides/build-a-skill.md) — SKILL.md authoring rules and script registration
