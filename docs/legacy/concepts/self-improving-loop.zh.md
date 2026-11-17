# 自我改进循环

OLAV **是为本地小模型设计的**——而小模型如果没人替它做笔记，就会反复犯同样的错。OLAV 的回应是一套反馈架构：**从自身使用中学习**——无需训练、无需运维干预。

!!! note "本页 = 更大系统中的记忆层"
    自我改进循环是 OLAV 的**记忆层**（"快权重"——非参数、每次运行都学）。它是[持续学习](continual-learning.md)三层之一：记忆（本页）、可选的巩固进模型**权重**（微调，由基准门把关）、以及 **harness** 配置调优。微调是*可选且带外*的——本循环完全不需要它。

本页解释这些层级（L1 → L2 → L3）、它们如何组合，以及证明它们真的能引导 model 行为的实验证据。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L3-L1-CAPTURE | `OperationalEventCapturePlugin` 自动把写类工具调用捕获为 `operational_event` 记忆 | ✅ R98 |
    | C-L3-L2-EXTRACT | `pattern_extractor` 通过 LLM 把 N 条样本蒸馏成可复用的 `expert_knowledge` 模式 | ✅ R98 |
    | C-L3-DIRECTIVE | Directive `*.guide.yaml` 文件通过 `olav kb import-guides` 入库 | ✅ R98 |
    | C-L3-AUTORECALL | 通过 `AutoRecallMiddleware` 把记忆自动注入 model 上下文 | ✅ v0.15+ |

---

## "自我改进"在这里到底意味着什么

它**不**意味着 OLAV 在背地里训练模型——那昂贵、慢、风险高。它意味着：

- 每次成功的工具调用都被**记录为记忆**。
- 重复出现的模式被 curator **蒸馏成 directive 记忆**。
- 所有这些记忆下次出现类似情况时**自动注入 prompt**。

用一周后，你的 OLAV 部署会**实质性变聪明**——同模型、同代码，**记忆更丰富**。

---

## 三层

```
                              ┌───────────────────────────────────────┐
   用户查询 ─────────────────┤  AutoRecallMiddleware                  │
                              │  （每次 model 调用前，召回 top-K 相关  │
                              │   记忆，注入到 prompt）                 │
                              └────────┬──────────────────────────────┘
                                       │
                                       ▼
                              ┌──────────────────────┐
                              │  AGENT + LLM          │
                              │  发出 tool_call       │
                              └────────┬──────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
  ┌──────────────┐           ┌──────────────────┐         ┌─────────────────┐
  │ L1 捕获       │           │ 其他中间件        │         │ 工具执行          │
  │ (每次 agent  │           │ (审计 / SQL 模式 │         │ + 结果回到         │
  │ 跑完后)       │           │ 等)              │         │ LLM              │
  │              │           │                  │         └─────────────────┘
  │ 扫描消息流，  │
  │ 把写类工具    │
  │ 调用捕获为    │
  │ operational_ │
  │ event 记忆   │
  └──────┬───────┘
         │
         │ 累积 ≥ N 条相似事件
         │
         ▼
  ┌────────────────────────────────────────────────┐
  │ L2 抽取  （按需 / 定时）                         │
  │                                                  │
  │ pattern_extractor.extract_operational_patterns  │
  │  （按工具分组，每分组调用 LLM 一次，               │
  │   把 expert_knowledge 写回）                     │
  └─────────────────────────────────────────────────┘
                                       │
                                       │ 下次 AutoRecall 命中
                                       ▼
                              ┌──────────────────────┐
                              │ Agent 现在同时看到：   │
                              │ directive + 具体先例  │
                              │ + 蒸馏出的模式        │
                              └──────────────────────┘
```

### L1 —— 写类捕获（始终在线、热路径）

`olav.plugins.middleware.operational_event_capture.OperationalEventCapturePlugin` 是内置中间件。每次 agent 跑完它会：

1. 扫描消息流找 **AIMessage → ToolMessage** 对，工具名匹配写类动词词根（register / deploy / push / save / destroy / write / record / emit / export / ingest / stop / create / update / delete / exec）。
2. **脱敏密钥**——args / result 里 token / password / api_key / secret / credential / auth_header 等字段值替换为 `<redacted>`。
3. 跳过失败调用（`status: error`）以保持存储不堆积坏例子。
4. 写一行以 SHA256 调用摘要为 key 的 `operational_event` 记忆——重跑同样操作不会重复。

性能：每次写类工具调用一次嵌入 + 一行 LanceDB 入库，本地 Ollama 嵌入下亚秒级。

### L2 —— 模式抽取（按需、可批量）

`olav.core.memory.pattern_extractor.extract_operational_patterns(scope, min_samples, window_days)` 是 Python helper。它按工具名分组 `operational_event` 记忆；任何分组在窗口内 ≥ `min_samples` 条样本时，**调用 chat 模型一次**抽象成可复用模式，结果写回为 `expert_knowledge`。

按需或定时夜间跑：

```bash
python -m olav.core.memory.pattern_extractor \
    --scope services --min-samples 5 --window-days 30
```

也可以通过 curator agent 经 `execute_skill_script` 调用——同一段代码。这**有意不是中间件**——LLM 调用昂贵、可批处理、需要可审计，三点都说明该是显式触发，不是热路径注入。

Demo7 实测里，6 条历次 `register_service` 事件被蒸馏成 8 条约定：

> - Action 始终是 `register_service`，需要四个参数：`name`、`kind`、`endpoint`、`token_env`。
> - 命名约定遵循 `{kind}_{suffix}`……
> - 必须通过 `token_env` 参数做认证……
> - 差异提示：host 寻址混用（一些 `localhost`，一些 `192.168.100.x`）……

这条蒸馏出的模式此后是任何新服务注册请求的 AutoRecall top-1 命中。

### 失败驱动的约束（`reflection`）

L1/L2 从**成功**中学习。还有一条并行路径从**失败**中学习：每次运行后，audit 中间件在后台触发 `trace_learner`（fire-and-forget）。它从 `audit.duckdb` 读失败/取消的运行，让 LLM 提炼成一行行*约束*（"当 Y 成立时不要调用 X"），以 `scope=global` + 30 天 TTL 写成 `reflection` 记忆（ADR-0015）。AutoRecall 再通过预留的 `reflection` 配额槽把它们注入到**每个** agent —— 一个 agent 犯的错成为所有 agent 的护栏。用 `/trace-review [hours] [limit]` 可按需触发复盘。

!!! note "2026-06-13 全线接通"
    这个环此前只写不读数月：约束写入 `scope=shared:audit`，而 recall 查的是 `scope=global`，所以没有 agent 读得到。现在改为 `scope=global` + 真实 embedding + 预留 recall 配额。

## reflector 子代理 —— 定时自我反思

上文的 `trace_learner` 从 `audit.duckdb` 的**结构化**失败中学习。**reflector**（`admin/reflector` 子代理）与它互补：它**还会**挖纯文本日志，并多出一条代码提案通道。它通过 4am 的 `reflect` cron 任务每日运行，用 `olav cron enable reflect` 启用。

它的反幻觉核心是 `scan_error_signatures`。与其把几 MB 日志文本塞进小模型上下文（单是 `api_server.log` 就可能 ~200 MB+），它返回一个有界的**错误签名直方图**：

1. 从 `audit.duckdb` 拉近期错误 + 尾扫每个 `.olav/logs/*.log` 的最后 ~2 MB。
2. **归一化**每条消息 —— 把时间戳 / id / 数字 / 路径剥成占位符 —— 得到一个稳定的*签名*。
3. 分组 + 计数，返回 top-K 个签名（K 按 tier：15 / 30 / 60），每个带**一个**截断示例和一个 `truncated` 标志。

agent 看到的是 `"ERROR X ×47, example: …"` —— 精确、封顶、无法编造。随后它把每个签名分诊到两条通道之一：

- **KB 通道** —— 一行运维教训 → **直接**写成 `reflection` 记忆（`record_reflection`；`scope=global`、30 天 TTL、配额 1 —— 自限且可逆）；或一条永久 `usage_guide` → 写成**草稿**到 `.curator_drafts/`，经 memory-curator 走人工（HITL）提交（`propose_guide_draft`）。
- **Code 通道** —— 真正的代码缺陷 → 一份**提案**（根因 + 建议修复 + 可选 patch）写到 `exports/reflections/`（`draft_code_fix`）。它**绝不**改源码、跑 git 或应用任何改动 —— 由人来审。

!!! note "reflection 写入会做语义去重"
    与已有 reflection 的 L2 距离在 0.3 以内的新教训会被跳过，所以对同一批反复出现的错误每天跑也不会堆积近乎重复的教训。

### L3 —— 跨团队 / 治理（企业扩展）

L3 的数据基础设施 —— 联邦 LanceDB、scope 感知聚合、合规级脱敏、谁看了什么的审计轨迹 —— 已就位。**L3 是天然的企业差异化**：跨团队聚合 L1+L2、按受监管行业（电信、金融、医疗）部署合规验证过的 directive bundle。

OSS 发行版默认带 L1+L2，L3 是**可选业务**，不是分叉。

---

## 加分：显式 directive（零 LLM 引导）

有时你希望在系统还没有任何样本可学之前就**先**编码规则。把 `*.guide.yaml` 放到 `<workspace>/<agent>/guides/`，然后：

```bash
olav kb import-guides
```

guide 现在是一条 `usage_guide` 记忆，scope=global（如果在 `core/guides/` 下）或 scope=`<agent>`（其他位置）。AutoRecall 在未来相关查询上自动召回。

这就是团队编码自己约定的方式（"绝不内联 token"、"write_profile 前先校验 IP plan"、"审计报告用 H2 标题"）。完整教程见 [扩展记忆](../guides/extending-memory.md)。

---

## 实证：它确实有用

Demo7 Chapter 8（2026-04-28）实测了这套架构是否真能引导小模型。每次实验用同样含糊的用户 prompt：

> "Write a bash script to backup running-config from all routers. Use real device names/IPs from the database. Save to exports/scripts/. Include --dry-run + error handling."

| # | 后端 | 记忆状态 | 结果 | 耗时 |
|---|---|---|---|---|
| 1 | qwen3.6:27b（本地）| 空 | ❌ 只叙事 | 2:51 |
| 2 | qwen3.6:27b（本地）| 空（用户用 directive prompt）| ✅ 118 行脚本 | 2:26 |
| 3 | qwen3.6:27b（本地）| 空（max_tokens 16000）| ❌ 只叙事 | 2:44 |
| 4 | qwen3.6:27b（本地）| 空（SKILL.md 强化）| ❌ 只叙事 | 1:52 |
| 5 | deepseek-v4-flash（云）| 空 | ✅ 126 行脚本 | 2:52 |
| **6** | **qwen3.6:27b（本地）**| **directive guide + L1 先例** | ✅ **150 行脚本** | **3:40** |

#1、#3、#4、#6 用同样模型、同样 prompt、同样 SKILL.md。把 #6 从"只叙事"翻转到"150 行 bash"的唯一变量是**记忆内容**（一条 directive guide + 一条来自 #5 deepseek 跑出的 L1 捕获）。

这就是实证。**让 27B 本地模型在自由格式请求上正确表现的，是记忆注入——不是更大的上下文，也不是 SKILL.md 里更强的 prompt 规则**。

---

## 衰减与裁剪

记忆并非永生。schema 包含 `access_count`（每次召回命中累加）和 `created_at`，curator 可以：

- 降级 / 删除 N 天内零访问的条目。
- 把最近被引用的记忆维持在更高 confidence。
- 当 operational_event 累积超阈值，提升为 expert pattern。

这些 curator 是可插拔的——默认的能用，团队可以加自己的。

---

## 操作原则

1. **记忆是 prompt 编程语言。** 行为规则放在记忆里，不是代码里。增减调优是改文本，不是重构。
2. **L1+L2 让存储从观察中成长。** 你不需要预想每条约定——很多在使用中自然浮现。
3. **Directive 启动 L1+L2 学不到的东西。** "绝不内联 token"很难从观察学到；写成 guide 容易。
4. **按 scope 隔离防污染。** A 团队的记忆不会泄露到 B 团队的 agent。同代码、不同记忆、不同行为。
5. **默认可审计。** 每条记忆都有 origin、confidence、scope、timestamp。`olav_recall_memory` 显示某查询召回了哪些。

---

## 接下来

- **[记忆架构](memory-architecture.md)** —— 表 schema、类别、AutoRecall 内部机制
- **[扩展记忆](../guides/extending-memory.md)** —— 实操：写 guide
- **[知识库](../guides/knowledge-base.md)** —— 同存储的文档导入侧
