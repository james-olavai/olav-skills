# 记忆架构

OLAV **是为本地小模型设计的**（qwen3 系列 7-30B、deepseek 小模型、Llama 等）。OLAV 能从这些模型身上得到生产级输出而不是撞上它们的规划上限——最大原因是它的**记忆层**。

本页解释这一架构：什么被存储、谁写入、谁读取，以及运维 / 团队如何**在不动源码的情况下扩展记忆**。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L3-UKS | 统一知识存储：单一 LanceDB 表存放文档、记忆、图节点 | ✅ v0.13.0 |
    | C-L3-AUTORECALL | AutoRecallMiddleware 在每次 model 调用前注入相关记忆 | ✅ v0.15+ |
    | C-L3-L1-CAPTURE | OperationalEventCapturePlugin 自动捕获写类工具调用 | ✅ R98 |
    | C-L3-L2-EXTRACT | pattern_extractor 通过 LLM 把 N 条样本抽象为可复用模式 | ✅ R98 |
    | C-L3-DIRECTIVE | 通过 directive YAML 文件在安装时入库 | ✅ R98 |

---

## 总体思路

传统 agent 框架依赖语言模型**从零规划并执行**每次请求。这对前沿云端模型有效，**对本地小模型不成立**——它们有公认的弱点：

- 声明意图（"我现在写脚本……"）后停下，**不发出 tool_call**
- 在多个会话里反复重新发现同一条 SQL
- 忘记团队约定（命名、安全策略、首选工具）

OLAV 的应对：**不修模型，改 prompt**。每次 model 调用都自动把相关**记忆**注入进 prompt，让先例、约定、约束**就在注意力最集中的位置**显现出来。

这让记忆变成 OLAV 的**Prompt 编程语言**。行为由 LanceDB 里有什么决定，而不是由硬编码的中间件决定。

---

## 统一记忆存储

一张 LanceDB 表 —— `memory` —— 存放所有类别的知识，由单一 embedder 索引。混合检索（向量 + BM25）回收任意查询的相关行。

```
.olav/databases/memory.lance/
  └── memory/                  ← 一表多类
      ├── id              (string)
      ├── text            (string —— 完整内容，原样 embed)
      ├── vector          (FixedSizeList[float32] —— 维度自动从
      │                    embedder 探测；nomic-embed-v2 是 768，
      │                    OpenAI text-embedding-3-small 是 1536)
      ├── category        (usage_guide / expert_knowledge /
      │                    operational_event / query_pattern / fact /
      │                    decision / preference / audit / format_guide /
      │                    reflection / topology / document)
      ├── scope           (string —— "global" / agent 名 / "team-X")
      ├── metadata        (JSON —— 各类别附加字段)
      ├── origin          (agent / document / user / audit / directive)
      ├── confidence      (float 0.0-1.0)
      ├── tags            (JSON 数组 —— 实体 / 主题标签)
      ├── timestamp / created_at
      └── access_count    (int —— 每次召回命中累加)
```

为什么单表而非分表：混合检索**跨类别同时工作**——一个查询如 *"我们如何注册 InfluxDB?"* 能在一次调用里同时拉到 directive guide（`usage_guide`）和最近一次成功注册（`operational_event`）。`category` 字段用于过滤和渲染，**不用于隔离**。

### 数据安全：dim 不匹配 fail-fast

如果配置的 embedder 维度与 LanceDB 表里已存的维度不一致（例如从 768
本地 BGE 切到 2048 cloud embedder），OLAV **拒绝启动**并抛
`EmbeddingDimMismatchError`。表完整保留——运维必须显式处理：要么
修 embedder 配置，要么跑 re-embed 迁移，要么显式 opt in 旧的
drop-and-recreate（`OLAV_ALLOW_DESTRUCTIVE_DIM_MIGRATION=1`，仅
debug 用，会**销毁全部行**）。早期 OLAV 静默 drop 表，现在改成
显式 opt-in 防止 embedder fallback 抖动时静默丢数据。详见
`dev_docs/00 § ISSUE-EMBEDDING-FALLBACK-DIM-MISMATCH-DESTROYS-DATA`。

---

## 记忆类别 —— 存储里有什么

| 类别 | 写入者 | 用途 | 例子 |
|---|---|---|---|
| `usage_guide` | 运维（通过 `*.guide.yaml`）| 手写团队约定 / directive | "用户要 write/save 时必须发 format_and_export 工具调用" |
| `expert_knowledge` | curator agent (R87) 或 `pattern_extractor` (L2) | 提炼出的智慧——抽象模式、scope 内部落知识 | "我们 register_service 约定：`{kind}_{suffix}` 命名、token 放 env var" |
| `operational_event` (L1) | `OperationalEventCapturePlugin` 中间件 | 每次写类工具调用成功后自动捕获 | `"Action: register_service, Args: {…}, Result: {…}"` |
| `reflection` | `trace_learner`（scope=global，30 天 TTL）| 从失败运行中提炼的约束；经 recall 配额注入到每个 agent（ADR-0015）| `"take_snapshot 遇 environment mismatch 不要调用——会返回 skipped"` |
| `query_pattern` (R83) | `query_pattern_capture` 中间件 | db_query agent 用的成功 SQL 模板 | `"Q: 列出 border 角色的设备 → SELECT * FROM v_topo_devices WHERE role='border'"` |
| `format_guide` (R85) | 运维 | 各产物类型的输出格式规则 | "所有审计报告用 H2 + Markdown 表；拓扑用 Mermaid" |
| `fact` | 手工 / agent / 文档导入 | 稳定知识 —— ASN、IP、角色定义 | "R1 的 loopback 是 1.1.1.1；R1 是 AS 65000 的边界路由器" |
| `decision` | 审计轨迹 | 记录的 change-control 决策 | "2026-04-15：批准在 R1+R4 之间加 eBGP" |
| `audit` | 审计中间件 | 合规记录 —— 谁在什么时候做了什么 | "user=alice 在 14:32:08Z 跑了 `/netops_init`" |
| `topology` | `memory_curator` (R102) | Mermaid / DOT / SVG 拓扑源 | `"graph TD\n  R1 --> R3\n  R3 --> R4"` |
| `document` | `memory_curator` (R102) | 长文档 dual-track chunks | "（一段 ~500 token 的 runbook 切片，配套一条 summary `usage_guide`）" |

运维者最常看到和成长的是前三行。其余是基础设施，会在正常使用中慢慢累积。

### 已弃用：`schema_knowledge` / `value_distribution`

早期 OLAV (R83.4) 给每个 per-command auto-view 把列、样本行、值分布
预先 prime 到 LanceDB，让 AutoRecall push 给 agent 当 schema 提示。
实测对小模型 (qwen3.6-27b-dense) 是负 ROI —— agent 不用 cached schema，
反正会自己用 `information_schema` introspect。所以从 2026-04-30 起
push 路径**默认关闭**——大模型部署或 parity 测试需要时设
`OLAV_LEGACY_SCHEMA_PRIME=1` 重新打开。

替代方案是 **pull 模式**：`describe_table('netops.<view>')` 是 core
orchestrator 工具，agent 在 SQL 报列/表错时按需调用。详见
`dev_docs/00 § ISSUE-SCHEMA-PUSH-VS-PULL` 和
`schema_introspection_via_describe_table` 这条 guide。

---

## AutoRecallMiddleware —— 注入机制

`AutoRecallMiddleware` 在**每次 model 调用前**触发，通过 agent 的 `abefore_model` 钩子工作：

1. 提取当前用户查询（消息流里最后一条 human message）。
2. 用与 LanceDB 相同的 embedder 嵌入查询。
3. 混合检索（向量 + BM25 + 多样化器）召回 agent scope 内 top-K 最相关的记忆。
4. 把命中格式化进 `<relevant-memories>` 块。
5. 把这块注入到 model 输入里、**紧贴用户查询的位置**。
6. Model 看到的是：原 prompt + 相关历史教训 + 用户查询。

为什么"每次 model 调用"而不是"启动时一次"：相关性随每轮变化。不同 sub-agent 决策需要不同记忆。这个中间件**无状态、加性的** —— 不永久变更 prompt，只做每次调用级别的丰富。

注入位置**紧挨查询**——在模型注意力最高处显现，比埋在 system prompt 深处的规则有效得多。这就是为什么记忆比 SKILL.md 更能引导小模型行为的实证原因（参见 [自我改进循环](self-improving-loop.md) 里的实验矩阵）。

### 相关性门控注入 —— 只在命中时召回

经验**不是**每次调用一刀切注入——它是按与**当前**输入的相关性检索出来的。每次 model 调用，AutoRecall 对用户查询跑一次向量 + BM25 混合检索，给候选排序，取 top-K（按 tier：小 1 / 中 2 / 大 13），再套上 per-category 上限 + 最小配额 + scope 过滤。

大多数类别是纯 top-K-by-relevance —— **没有硬距离下限**。但 `reflection` 被特殊处理，加了一道**距离门**。reflection 教训每天增长（reflector 写的），而 reflection 配额只有 **1** 个槽；没有下限的话，那条唯一"最近"的教训会注入到**每一条** prompt，包括完全不相关的。所以与查询的 L2 距离超过 **0.65** 的 reflection 会被丢弃。这个阈值是实测标定的 —— 真实命中大约落在 ~0.36，不相关的 prompt 聚在 ≥0.75 —— 中间有一道干净的间隙。这让 `reflection` 真正做到"只在相关命中时才注入"。

!!! note "`usage_guide` 有意不加距离门"
    `usage_guide` 用同样方法标定过，但**故意不加距离门**：它的真实命中分布和噪声分布是重叠的（没有可切的干净间隙），而它是*引导*层，一次假阴性会直接劣化行为。它本身已经由 keyword-boost + on-intent 加载 + 配额 3 三重约束。

---

## 三层自我改进

### L1 —— 写操作捕获（始终在线、低成本）

`OperationalEventCapturePlugin` 是内置中间件，在**每次 agent 运行后**触发。它扫描消息流，找成功的写类工具调用（按动词词根匹配：register / deploy / push / save / destroy / write / record / emit / export / ingest / stop / create / update / delete / exec），脱敏后写出一条 `operational_event` 记忆。

按内容哈希幂等——重复执行相同操作不会复制。失败的工具调用（`status: error`）跳过以保持存储干净。成本：每次写工具调用一次嵌入 + 一行 LanceDB 入库，本地 Ollama 嵌入下亚秒级。

### L2 —— 模式抽取（按需触发）

`olav.core.memory.pattern_extractor` 是 Python helper，由 curator agent 或定时任务触发：

```bash
python -m olav.core.memory.pattern_extractor \
    --scope services --min-samples 5 --window-days 30
```

它按工具名分组 L1 事件；对任意分组 ≥ `min_samples` 条样本（窗口内）都**调用一次** chat 模型把它们抽象为可复用模式，写回为 `expert_knowledge`。成本：每分组一次 LLM 调用。Curator 跑夜间任务或按需触发——**永远不在热路径**。

在 Demo7 实测里，6 条历次 `register_service` 事件被一次 LLM 调用提炼为 8 条约定—— 命名、auth 模式、IP 网段、端口映射 —— 此后对任何新注册请求，这条提炼出的模式都是 AutoRecall 的 top-1 命中。

### L3 —— 跨团队 / 治理（企业扩展）

L3 的数据基础设施 —— 联邦 LanceDB、scope 感知聚合、合规级脱敏、谁看了什么的审计轨迹 —— 已就位。**L3 是天然的企业差异化**：跨团队聚合 L1+L2、按受监管行业（电信、金融、医疗）部署合规验证过的 directive bundle。

OSS 发行版默认带 L1+L2，L3 是**业务可选项**，不是分叉。

---

## 对话式入库（R102 —— memory_curator 子代理）

第三条入库路径，与 L1 隐式增长、声明式 YAML 互补。用户用自然语言告诉
OLAV："记住……" / "remember that ..." / "把 /path/to/runbook.md
加进记忆"。`memory_curator` 子代理把输入塑形成正确的记忆类别
（`usage_guide` / `document` / `topology`），**仅在用户确认后**提交。

| 路径 | 触发 | 适用 |
|---|---|---|
| **L1 隐式** | 每次成功的写类工具调用 | 观察驱动、自动 |
| **声明式 YAML** | `olav kb import-guides <dir>` | 版本控制、团队共享、有 schema |
| **对话式（R102）** | `memory_curator` 子代理 | 探索性、即时、无需学 schema |

### 两 turn HITL：filesystem draft 持久化

deepagents `task()` 子代理调用是**stateless 每次新 ctx**——Turn 2
（用户确认）那次进入子代理，看不到 Turn 1 渲染的 YAML 提案。OLAV 用
draft 持久化这对工具解决：

* **Turn 1** —— agent 调 `propose_memory_draft(intent, keywords,
  body, agent, scope, category, chunks=...)`。把提案写到
  `<workspace>/.curator_drafts/<intent>.draft.json`，返回 ready-to-quote
  YAML 预览 + "Confirm? Reply 可以 / OK / yes / 入库 / 确认" 提示，
  agent 直接把 preview 字段引用给用户看。
* **Turn 2** —— 用户回 "可以" 等确认词，agent 调
  `commit_to_memory(from_draft=True)`。读 latest draft（或显式 `intent=`），
  hydrate 全部参数，commit 到 LanceDB + 写 `<agent>/guides/<intent>.guide.yaml`，
  把 draft 归档到 `.curator_drafts/committed/<intent>_<ts>.json` 留审计。

这设计让 deepagents `task()` 语义不变，靠 filesystem 把对话连续性穿过去。

**HARD HITL 在三层执行**：
1. agent 的 system prompt 禁止 `confirm=False`，并把"我已审阅 / auto-confirm"
   等出现在**首次请求**里的字样视为 bypass 企图——渲染预览，等用户下一轮
2. `propose_memory_draft` 永远只写 draft；Turn 1 LanceDB 没东西落地
3. `commit_to_memory(from_draft=True)` 要求 draft 文件存在——没先调
   `propose_memory_draft` 直接 Turn 2 commit 会失败 `"no draft found"`

完整设计 + 决策日志见
`dev_docs/70 R102_CONVERSATIONAL_MEMORY_INGESTION_SUBAGENT.md`
（in-vivo 用 Playwright 真实浏览器多 turn 端到端验证，2026-04-30）。

### 安装即自动 prime

`olav init` 和 `olav agent install <skill>` 现在会自动把刚部署进 workspace
的所有 `*.guide.yaml` prime 进 LanceDB —— 运维不需要再单独跑
`olav kb import-guides`。如果 init（默认 BGE-512）和 skill install（如
qwen3-vl-2048）之间 embedder 维度变了，install 会检测到不匹配并按新维度
重 prime（合法场景，因为这时只有平台默认 guide，用户自定义记忆还没产生）。

---

## 加分项：显式 directive（零 LLM 引导）

有时你希望在系统还没有任何样本可学之前就**先**编码规则。把 `*.guide.yaml` 放到 `<workspace>/<agent>/guides/`，然后：

```bash
olav kb import-guides
```

guide 现在是一条 `usage_guide` 记忆，scope=global（如果在 `core/guides/` 下）或 scope=`<agent>`（其他位置）。AutoRecall 在未来相关查询上自动召回。

这就是团队编码自己约定的方式（"绝不内联 token"、"write_profile 前先校验 IP plan"、"审计报告用 H2 标题"）。详见 [扩展记忆](../guides/extending-memory.md)。

---

## Scope 隔离

每条记忆都有 `scope` 字段：

- `global` —— 所有 agent 都可见（**慎用**；这是共享存储）
- `<agent_name>` —— 比如 `services` / `netops` / `core` —— 仅该 agent 的召回看到
- `team:<id>` —— 你团队的私有知识 bundle

AutoRecall 默认查询 `scope IN (current_agent, "global")`。一条 scope=`services` 的记忆**不会污染** netops agent 的召回，反之亦然。运维通过 `olav_recall_memory(...)` 显式跨 scope 查询。

这就是为什么 OLAV 能扩展到多团队 / 多领域部署——一个团队的特殊性不会污染另一个团队的 agent 行为。

---

## 为什么这个设计胜过其他方案

| 替代方案 | 我们为什么不用 |
|---|---|
| 把规则硬编码进 agent SKILL.md | 静态；不能在不分叉源码的情况下做团队定制 |
| 新建"重写 prompt"中间件 | 重新发明 AutoRecall 已经做的事；没有按 scope 定制能力 |
| LangChain `ConversationSummaryMemory` | 不感知类别；无法分离 guideline / 历史 / 事实 |
| 把所有内容发给模型让它决定 | Token 有限；信噪比掉得快 |
| 训练 / 微调模型 | 月级工程；锁死一份权重 |

OLAV 的选择 —— **类别感知记忆 + AutoRecall 注入 + L1+L2 自强化** —— **可增量、可审计、可按 scope 定制、跨模型大小通用**。

---

## 运维心智模型

当你让 OLAV 做某事：

```
你的查询  ──►  AutoRecall 拉取相关记忆
              （directive + 过往模式 + 事实）
                       │
                       ▼
              增强后的 prompt 发给 LLM
                       │
                       ▼
              LLM 发出工具调用（或文本）
                       │
                       ▼
              L1 捕获成功的写类工具
                       │
              （时机到时 L2 抽取模式）
                       │
                       ▼
              下次问类似问题 → 记忆更丰富
```

每次交互都让下次更轻松一点。**没有 ML 训练、没有代码改动 —— 只是用 OLAV**。

---

## 分层 guide：编排智慧 vs 子代理执行细节

记忆 guide 在**两个不同抽象层**注入。这个区别在设计写什么进
存储时很关键。

| 层 | 在哪里 | 何时加载 | 例子 |
|---|---|---|---|
| **L0 — always-on 静态** | `static_context:` 在 `SKILL.md` / `AGENT.md` 里 | 每个 turn 都进 prompt | `REQUIRED_INFO_CHECK.md`（必查的前置条件）|
| **L1 — on-intent 静态** | `static_context_mode: on_intent` + reference 文件 | 用户 query 命中关键词时 | `ROUTING_EXPERT_GUIDE.md`（深度 BGP 算法 —— 仅在 `analyzer` 子代理调查时相关）|
| **L2 — AutoRecall guide** | `<agent>/guides/` 下的 `*.guide.yaml`，通过 `olav kb import-guides` 入库 | 用户 query 语义命中关键词；reranker 提到 top-K | 分层诊断剧本（"BGP `Idle` → 先查 L1"）|

同一个问题可以在三层都有内容 —— 没问题，因为每层服务不同消费者：

- **L2 guide** 告诉**编排器** "这是拓扑任务，路由到 analyzer；高层 pattern 是这样"
- **L1 reference** 告诉**子代理** "这是完整 Mermaid 语法 + 用 networkx 画图的算法"
- **L0 always-on** 告诉**所有人** "snapshot 之前永远先确认 device_list_confirmed"

写运维智慧时问自己：**这条信息谁需要 —— 决策的 agent 还是执行的 agent？** 答案决定层级。

## Code 层严格性：load-bearing 基础

记忆 guide 不是孤立工作的 —— 它们之所以有效，是因为底层工具的 schema
严格到 LLM 输出被强制塑形。R100（2026-04-29）把这一点摆明了：

| 本地 27B 类模型实测的失败 | 根因 | 修复 |
|---|---|---|
| `format_and_export({'data': {'format':'md',…}})`（嵌套 dict）| `data: Any` schema 允许任何 JSON 值 | `data: str` 严格类型 |
| `format_and_export(filename='"reports"')`（带字面引号）| 小模型在短 string args 上的 JSON 序列化怪癖 | 工具入口的防御性 `_strip_quote_leak()` |
| Agent 调 `write_file`（deepagents 虚拟 FS）而不是 `format_and_export` | 两个工具都可见，模型挑看起来更简单的 | `FilesystemPermission(write, deny)` 把模型重定向到真磁盘工具 |
| Agent 卡在过期的 DB 数据上不刷新 | 没有"何时该 re-snapshot"的记忆提示 | `take_snapshot_when_db_stale.guide.yaml` |
| 接口 down 但 agent 还在挖 BGP 属性 | 没有分层诊断 frame | `troubleshoot_layered_l1_to_l4.guide.yaml` |

模式：**prompt + memory 工程治根因；code 工程治症状**。严格 schema +
防御性 coercion + 权限闸门 屏蔽掉 prompt 调整治不动的失败模式。
记忆 guide 然后在剩下的合法选择之间引导 agent。两层都必要，单独
任何一层都不够。

完整 v1→v9 实证序列 + 决策日志，见仓库内
`dev_docs/69 R100_LOCAL_SMALL_MODEL_END_TO_END_MILESTONE.md`。

---

## 接下来去哪

- **[扩展记忆](../guides/extending-memory.md)** —— 实操：怎么加 directive guide
- **[自我改进循环](self-improving-loop.md)** —— L1+L2 机制 + 实验证据
- **[知识库](../guides/knowledge-base.md)** —— 同一存储的文档导入侧
