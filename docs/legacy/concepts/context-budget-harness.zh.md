# 上下文预算 Harness — 多层 Token 注入治理

**上下文预算 Harness** 是 OLAV 保护 LLM 上下文窗口不被过量 token 注入淹没的防御体系。它与[安全执行 Harness](agent-harness.md) 正交独立，在五个不同的架构层级强制执行——从单次工具输出到系统提示结构。

!!! abstract "为什么这很重要"
    Small-tier LLM 约有 8,000 tokens 的上下文。一条不加限制的 SQL 查询可能返回数百行；一个粗心的脚本可能输出几 MB 的文本；一个臃肿的工具 docstring 在每次 agent 调用时就会额外消耗 300 tokens——这一切都在第一个真正的工作 token 被处理之前就已发生。

    上下文预算 Harness 确保**任何单次操作的上下文成本有上界**，使 small-model agent 在生产工作负载中切实可用。

---

## 架构总览

```
Agent 轮次
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  第 1 层 — 工具注册预算（ARCH-18 #1）                             │
│  @tool docstring ≤ 20 行 · 完整文档藏在 tool_help() 后面          │
│  每个工具节省 ~150-300 tokens，每次调用都会生效                   │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  第 2 层 — 静态上下文注入预算（ARCH-17 + ARCH-20）                │
│  SKILL.md 正文 ≤ 350 行 · always 模式引用 ≤ 200 行               │
│  on_intent 引用 ≤ 500 行 · lazy 引用按需获取                     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  第 3 层 — 记忆注入分级限制（ARCH-19）                            │
│  AutoRecall top_k: small=1 / medium=2 / large=13                 │
│  （预算守卫跳过逻辑存在且有单测，但运行时 DORMANT —              │
│   未接 budget monitor；tier top_k 才是实际生效的控制）            │
└─────────────────────────────┬────────────────────────────────────┘
                              │ 工具调用
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  第 4 层 — SQL / 搜索行数限制（ARCH-16）                         │
│  execute_sql: small=10 / medium=20 / large=50 行                 │
│  search_logs 硬上限：所有 tier 500 行                            │
└─────────────────────────────┬────────────────────────────────────┘
                              │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  第 5 层 — 工具返回值紧凑化（ARCH-18 #2）                        │
│  execute_cli_parallel: 紧凑模式 4,000 字符 / full=True 可覆盖    │
│  diff_configs: 40 行上限 / full=True 可覆盖                      │
│  api_request: 列表响应每页 50 条上限                              │
│  skill_runner stdout: 512 KB 硬上限                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 第 1 层 — 工具注册预算（ARCH-18 #1）

每个 `@tool` 装饰函数的 docstring 和 Pydantic 参数 schema 在**每次 agent 调用**时都会被序列化进模型的工具列表。schema 本身每个工具就消耗 150–300 tokens。8 个工具合计约 2,400 tokens 的固定开销——占 small 模型全部上下文预算的 30%——在处理第一条用户消息之前就已用掉。

**规则：** `@tool` docstring 必须控制在 20 行以内。完整使用文档藏在 `tool_help('<name>')` 后面，仅在需要时才按需获取。

```python
@tool
def olav_recall_memory(query: str, category: str | None = None, ...) -> str:
    """Query long-term memory for past experiences, decisions, and network events.

    Hybrid semantic search (vector + BM25 + recency). Use natural language.
    Call once at the start of an investigation or change plan.
    Full usage: tool_help('olav_recall_memory').          # <-- 指针，不是内容

    Args:
        query:    Natural language query ("BGP failures on R1").
        category: Optional category filter. None = all categories.
        ...
    """
```

**治理测试：** `tests/governance/test_docstring_budget.py` — 用 AST 解析每个注册的 `@tool` 文件，超过 20 行则 CI 失败。

---

## 第 2 层 — 静态上下文注入预算（ARCH-17 + ARCH-20）

OLAV 在第一个 agent 轮次之前将两类静态文本注入系统提示：

- **SKILL.md 正文** — agent 的角色描述、工具表格和工作流说明
- **`static_context:` 引用文件** — 在 SKILL.md frontmatter 中声明的补充文档

两者都是逐字注入，没有 LanceDB 检索，没有 top-k 门控。

### SKILL.md 正文预算

| 限制 | 值 | 治理测试 |
|------|-----|---------|
| 最大正文行数 | 350 | `test_token_injection_limits.py` |

如果正文过长，将工作流细节移入 `static_context:` 引用文件。

### 引用文件按模式分级预算

```yaml
# SKILL.md frontmatter 示例
static_context:
  - path: ./references/change_plan_authoring.guide.yaml
  - path: ./references/schema_introspection.guide.yaml
static_context_mode: on_intent   # always | on_intent | lazy
```

| 模式 | 注入触发时机 | 行数预算 |
|------|------------|---------|
| `always` | 每次调用，无条件注入 | ≤ 200 行 |
| `on_intent` | 第一轮，模型输出匹配关键词时 | ≤ 500 行 |
| `lazy` | 显式调用 `get_static_context(name)` 时 | 无上限 |

对于大型参考文档（API 规范、综合 schema），使用 `lazy` 模式。模型可以按需获取，无需在每一轮都支付成本。

**治理测试：** `test_token_injection_limits.py::test_static_context_always_mode_files_within_budget` 和 `test_static_context_on_intent_files_within_budget`。

---

## 第 3 层 — 记忆注入分级限制（ARCH-19）

`AutoRecall` 检索到的长期记忆会被注入每一轮的系统提示。没有限制的话，随着运营历史的积累，检索结果会持续增长。

### 分级 top_k

| Tier | 上下文预算 | Recall top_k |
|------|----------|-------------|
| small | 8,000 tokens | 1 |
| medium | 32,000 tokens | 2 |
| large | 200,000 tokens | 13 |

### 预算守卫（空间不足时跳过 Recall）—— 休眠

!!! warning "逻辑存在且有单测，但运行时未激活"
    `AutoRecallMiddleware` 接受一个可选的 `budget_monitor`；接上后，在第一轮
    之后的轮次里若剩余空间低于分级阈值就跳过检索。**但运行时没有任何代码接上
    monitor** —— 提供空间读数的 `ContextBudgetMonitor` 已于 2026-06-13 作为
    死代码删除，因此 `_should_skip_for_budget()` 在生产中恒返回 `False`
    （见 `test_missing_budget_monitor_never_skips`）。当前唯一生效的 recall
    上界是上面的分级 `top_k`。在重新接上 monitor 之前，本节是设计预留钩子，
    不是实时防御。

```python
# 若接上 monitor 才会用到的分级阈值
# （TIER_DEFAULTS.recall_skip_headroom_pct）：
# small:  剩余 < 20% 时跳过
# medium: 剩余 < 10% 时跳过
# large:  从不跳过（0%）
```

**测试：** `test_autorecall_tier.py`（top_k 边界 —— 生效），`test_autorecall_budget_guard.py`（跳过逻辑 —— 仅在注入 stub monitor 时验证；`test_missing_budget_monitor_never_skips` 记录了真实运行路径）。

---

## 第 4 层 — SQL / 搜索行数限制（ARCH-16）

数据库查询是无界数据注入的主要来源。对一张 50,000 行的接口表执行 `SELECT *`，返回的 token 数量会超过 small 模型的全部上下文预算。

### execute_sql 分级限制

| Tier | 最大行数 |
|------|---------|
| small | 10 |
| medium | 20 |
| large | 50 |

```sql
-- Agent 查询（无需写 LIMIT 子句——harness 会自动添加）
SELECT device, interface, status FROM interfaces WHERE status = 'down';

-- 实际执行时添加：LIMIT 10  （small tier）
```

### search_logs 硬上限

无论哪个 tier，`search_logs` 最多返回 **500 行**。日志搜索可能匹配数百万条记录；硬上限防止单次调查查询耗尽上下文。

**治理测试：** `test_round41_arch16_tool_limits.py` — 通过 AST 和 import 检查验证分级限制和硬上限。

---

## 第 5 层 — 工具返回值紧凑化（ARCH-18 #2）

工具输出是最后一道防线。即使 SQL 有行数限制，其他工具也可能返回大量原始文本。每个可能返回大量输出的工具都实现了"默认紧凑 / `full=True` 覆盖"模式。

### execute_cli_parallel

```python
# 紧凑模式（默认）：每台设备输出截断至 4,000 字符
result = execute_cli_parallel(devices=[...], command="show bgp summary")

# 完整模式：返回完整输出（调试时使用）
result = execute_cli_parallel(devices=[...], command="show bgp summary", full=True)
```

紧凑输出包含 `"[截断于 4000 字符——使用 full=True 重新运行]"` 提示，让模型知道下一步怎么做。

### diff_configs

紧凑模式下差异限制为 **40 行**。大型设备的配置差异可能有数千行；上限只展示最相关的变更，并提示模型在需要时请求 `full=True`。

### api_request（DRF 分页 API）

第一页列表响应限制为 **50 条**。分页元数据（`count`、`next`）被保留，模型可以按需请求后续页面。

### skill_runner stdout（execute_skill_script）

所有脚本子进程的输出每个流（stdout + stderr）硬上限为 **512 KB**。这是运行时强制执行的硬性上限——返回超过 512 KB 文本的脚本本身存在设计问题，应使用内部分页。

**治理测试：** `test_compact_returns.py`（紧凑常量和逻辑），`test_token_injection_limits.py::test_skill_runner_stdout_cap_value_via_ast`（512 KB 上限 AST 验证）。

---

## 治理测试即活的规约

上面描述的每个预算和限制都有对应的治理测试，违反约束时 CI 直接失败。这让规约变得可执行：

| ARCH ID | 测试文件 | 守护内容 |
|---------|---------|---------|
| ARCH-16 | `test_round41_arch16_tool_limits.py` | SQL / 搜索行数限制 |
| ARCH-17 | `test_round38_progressive_disclosure.py` | load_reference 切片、tool_help brief 模式 |
| ARCH-18 | `test_docstring_budget.py` | `@tool` docstring ≤ 20 行 |
| ARCH-18 | `test_compact_returns.py` | CLI / diff / API 紧凑返回常量 |
| ARCH-19 | `test_autorecall_budget_guard.py` | AutoRecall 预算感知跳过 |
| ARCH-19 | `test_autorecall_tier.py` | top_k 分级边界 |
| ARCH-20 | `test_token_injection_limits.py` | SKILL.md 正文、static_context 引用、stdout 上限 |

测试套件在每次提交时运行。一个新工具、guide 文件或脚本违反任意一层，立即失败——无需人工审查即可捕获 token 预算回归。

---

## 添加新工具或引用文件

扩展 OLAV 时，遵循以下规则保持在预算内：

=== "添加 @tool"
    1. docstring 保持 ≤ 20 行
    2. docstring 末尾加 `Full usage: tool_help('<name>').`
    3. 把详细参数说明放入 `tool_help` 知识库
    4. 运行 `uv run pytest tests/governance/test_docstring_budget.py`

=== "添加 static_context 引用"
    1. 选择合适的模式：`always`（≤ 200 行）/ `on_intent`（≤ 500 行）/ `lazy`（无上限）
    2. 综合参考文档（API 规范、大型 schema）优先使用 `lazy`
    3. 运行 `uv run pytest tests/governance/test_token_injection_limits.py`

=== "添加脚本（execute_skill_script）"
    1. 尽可能返回结构化 JSON 而不是原始文本
    2. 如果脚本查询数据库，添加内部行数 / 大小限制
    3. 512 KB stdout 上限是最后一道防线——设计良好的脚本应远低于 50 KB

---

!!! info "相关文档"
    - [Agent Harness（安全 / 执行治理）](agent-harness.md) — AAA、沙箱、注入扫描
    - [Memory 架构](memory-architecture.md) — LanceDB、AutoRecall、分级检索
    - [构建 Skill](../guides/build-a-skill.md) — SKILL.md 编写规则与脚本注册
