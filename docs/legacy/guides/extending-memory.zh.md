# 扩展记忆 —— 给团队加 Directive Guide

OLAV 的召回中间件会把相关**记忆**自动注入每次 model 调用，但记忆库一开始基本是空的。两种方式填充：

1. **隐式增长** —— L1+L2 在你使用 OLAV 时自动捕获每次成功的工具调用，再蒸馏成模式。免费、自动。详见 [自我改进循环](../concepts/self-improving-loop.md)。
2. **显式 directive** —— 写 `*.guide.yaml` 文件描述你团队的约定，再用一条 CLI 命令入库。本页重点。

用 directive 编码 OLAV 单凭观察学不到的东西：

- "所有 service 必须用 `token_env`，绝不内联 token"
- "Bash 脚本写到 `exports/scripts/`，绝不写 `/tmp`"
- "审计 profile 必须同时含 `bgp_health` 和 `interface_state` job"
- "用户说 'deploy' 时，先 diff 再要审批"

---

## Directive guide 的结构

一个 directive guide 是带三个结构字段 + 一个自由 body 的 YAML 文件：

```yaml
schema_version: 1
intent: write_class_tool_call_directive    # 短标识符
agent: core                                 # 谁拥有这个 guide
keywords:                                   # AutoRecall 匹配提示
  - write
  - save
  - create
  - generate
  - export
  - 写
  - 保存
  - 生成
body: |
  写类请求 → 必须 emit tool_call（不只是描述）

  当用户请求创建、写入、保存、生成或导出任何产物时，你必须在
  同一条 AIMessage 里 emit tool_call 来真实执行。

  反例 —— 不要这样：
    User: "Write a bash script to backup configs"
    AI:   "Now I'll write the backup script…"  [停在这里、没 tool_call]

  正例：
    AI:   format_and_export(format="sh", subdir="scripts", …)

  工具选择：
    - 真磁盘产物 → format_and_export
    - workspace 文件 → 委派给 admin sub-agent
    - 服务注册 → register_service
    - 审计 profile → write_profile
```

字段规则：

- **`schema_version: 1`** —— 必填；格式如有变动会 bump。
- **`intent`** —— 你的唯一标识符；用作 memory ID 的一部分。
- **`agent`** —— guide 主 scope。AutoRecall 在该 agent 上召回。
- **`keywords`** —— 在 embedder 语义匹配之外补充字面字符串提示。混用中英文 + 团队术语。列表是**包含式**——用户查询里只要命中任一关键词就能加权。
- **`body`** —— 真正给 model 看的内容。**为模型而写，不是为人类**：
    - 用指令式（"必须"、"始终"、"绝不"）
    - 反例 + 正例对照
    - 写出具体工具名 + 参数
    - 控制在 ~600 tokens 以内 —— body 太大会浪费召回预算

---

## guide 放哪里

放到对应 agent 的 `guides/` 目录：

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

`<agent>/guides/` 是 `olav kb import-guides` 扫描的约定。这个布局之外的文件会被忽略。

如果你希望某个 guide **对所有 agent 可见**，放到 `core/guides/` —— `olav kb import-guides` 会把这个目录下的入库为 `scope=global`，每次召回都会浮现。

---

## 把 guide 入库

写好 YAML 后，跑：

```bash
olav kb import-guides
```

发生了什么：

1. 扫描每个 `.olav/workspace/<agent>/guides/*.guide.yaml`。
2. 对每个文件嵌入 body，写一行以 `<intent>-<agent>` 为 key 的 `usage_guide` 记忆。
3. 幂等 —— 文件内容不变时重跑是 no-op。
4. 报告 `Imported N guide(s) (M skipped)` 你能看到结果。

完事。AutoRecall 在下次 agent 调用时就会用上这条新记忆。**不需要重启、不需要重 build、不动代码**。

---

## 端到端例子

你想让 OLAV **保存审计 profile 前总是先校验 IP plan**。两分钟搞定：

```bash
# 1. 创建文件
cat > .olav/workspace/audit/guides/ip_plan_validation.guide.yaml <<'YAML'
schema_version: 1
intent: ip_plan_validation_required
agent: audit
keywords:
  - save profile
  - audit profile
  - bgp peering
  - validate ip
  - 校验 ip
  - 检查子网
body: |
  在用任何 BGP / OSPF / interface 校验 job 调用 write_profile() 前，
  你必须先查 v_topo_links_clean，确认 profile 里的 IP plan 与
  实际拓扑子网一致。

  示例流程：
    1. execute_sql: SELECT DISTINCT subnet FROM v_topo_links_clean
    2. 对照 profile yaml_jobs 里引用的 IP
    3. 如不一致 → 提示用户、不要 save
    4. 如一致 → write_profile(name=…, yaml_jobs=…)

  原因：profile 查的 IP 不在拓扑里时会静默返回 0 行——假绿审计。
  Demo7 Ch6 暴露了这个失败模式。
YAML

# 2. 入库
olav kb import-guides

# 输出：
# Imported 6 guide(s) from .olav/workspace (0 skipped)
```

下次有人用 audit agent，且用户查询的 embedding 接近上述关键词，directive 就会浮现在 `<relevant-memories>` 里。Agent 现在知道团队规则。

---

## 故障处理覆盖 — 让 OLAV 用你团队的 runbook

常见诉求："OLAV 默认的 BGP-Idle 排查会按 L1→L2→L3 顺序走。但**我们**这个 fleet 90% 的 BGP-Idle 是因为某个 over-eager 的自动修复脚本把 `ge-0/0/0` 给 admin-shut 了。我们想让 OLAV 优先查这个。"

Directive guide 就是用来编码这个的。三个关键点：

### 1. 选一个足够具体的 intent

```yaml
intent: bgp_idle_my_company_specific_root_cause
```

不要用 `troubleshoot_layered` 这种通用 intent。Intent 是 memory ID 的一部分，要跟平台默认的区分开，避免 re-import 时撞键。

### 2. 在 body 里**显式说覆盖**

Body 是模型字面看到的文本。如果你想覆盖默认的 L1→L4 顺序，**直接说**：

```yaml
body: |
  OVERRIDE 默认的 layered_l1_to_l4 排序，对 prod fleet 上的所有
  BGP-Idle 请求，先跑下面 STEPS 1-3。只有这三步都返回 0 行时
  才走默认的 layered diagnosis。

  STEP 1（90% 命中率）：检查 ge-0/0/0 是否被自动修复脚本 admin-shut。
    SELECT device_name, interface, admin_state, link_state
    FROM netops.v_show_interfaces_terse_auto
    WHERE device_name = '<host>'
      AND interface LIKE 'ge-0/0/0%'
      AND admin_state = 'down';
  若有结果 → 根因：自动修复误判。建议通过变更单 CR-##### 回滚。

  STEP 2（~7%）：MX204 + Junos 21.4 的 ASN 配置漂移 —
  实测自动 config-merge 会把 `peer-as` 丢掉。
    SELECT raw_output FROM netops.raw_output_store
    WHERE command = 'show configuration protocols bgp'
      AND device_name = '<host>'
      AND raw_output NOT LIKE '%peer-as%';

  STEP 3（~3%）：到 partner ASN 65501 的 jumbo 链路 MTU 不匹配。
    SELECT interface, mtu FROM netops.v_show_interfaces_auto
    WHERE device_name = '<host>' AND mtu < 9000;
```

### 3. 选运维实际表述用的关键词

```yaml
keywords:
  # 用户输入侧的措辞
  - BGP Idle
  - BGP 邻居 Idle
  - peer Idle
  - eBGP Idle
  - bgp neighbor down
  - 路由邻居故障
  # 运维内部的领域术语
  - mycompany prod
  - prod fleet
  - prod cluster
```

**中英文都要**，加上内部术语。AutoRecall 的 hybrid 检索任意一个关键词命中就触发 — 列多没坏处。

`olav kb import-guides` 之后，prod fleet 的每次 BGP-Idle 请求都会先注入你的 STEPS 1-3，比平台通用 L1→L4 优先。覆盖是自动的，不需要告诉 agent 用它。

---

## 陷阱 — 看着没问题但后期咬人的

| 现象 | 大概率原因 | 修法 |
|---|---|---|
| Guide 永远不出现 | 关键词太窄 / 语种不对 | 加 5+ 关键词变体，用运维实际语言 |
| Guide 在错的查询上触发 | 关键词太通用（如 `error`、`down`） | 用 `BGP Idle` 而非 `Idle` 这种多词组合 |
| 多个 guide 在同一查询撞车 | 两个 guide 共用 intent 前缀 | 用不同的 `intent:` id；前缀是 memory key 的一部分 |
| Guide 触发了但模型不理 | Body 太长（> 600 token） | 砍短 — recall 配额有限，长 guide 把别的挤掉了 |
| Re-import 没更新 body | Idempotent on `<intent>-<agent>` | 改了 intent 名要跑 `olav kb status` 找孤儿 |
| Guide 注入到错的 agent | 文件放错 `<agent>/guides/` 目录 | 移到正确目录再 import；跨 agent 用 `core/guides/` |
| 覆盖没真覆盖 | Body 写了 "see also default" 之类的犹豫话 | 用祈使语气："OVERRIDE"、"FIRST"、"ONLY fall through if…" |

验证 guide 接好了：

```bash
olav kb search "BGP Idle prod fleet"  # 你的 guide 必须在 top-3
```

如果不在，关键词没匹配上用户实际查询。

---

## 什么时候用 `expert_knowledge` 而不是 `usage_guide`

两者都通过 AutoRecall 注入，但有不同的 quota（每 turn：3 usage_guide + 2 expert_knowledge）：

| 你要编码的是… | Category | 为什么 |
|---|---|---|
| 工作流 / 决策树（"碰到 X 怎么办"） | `usage_guide` | 跟用户意图绑定，本质是 directive |
| 领域知识（"BGP path 属性怎么评估"） | `expert_knowledge` | 共享基础设施，跟 intent 绑定弱 |
| 合规规则（"key 不能写日志"） | `usage_guide` | 工作流覆盖 |
| 厂商怪异（"Junos 21.4 自动 merge 会掉 peer-as"） | `expert_knowledge` | 背景知识 |

写故障处理覆盖的多数团队需要 `usage_guide` — 也就是 `*.guide.yaml` 文件的默认。只有当知识属于平台领域而非工作流 directive 时才用 `*.expert.yaml`。

---

## 跨团队分享 guide

Directive 是纯文本 YAML 文件。像分发任何文本资产一样分发：

```bash
# 在独立仓库写 guide
git clone https://gitea.team.local/our-org/olav-directives /tmp/d

# 从任意目录入库
olav kb import-guides /tmp/d
```

这让 **OLAV directive bundle** 成为可销售、可内部分发、可合规认证的真实工件。一份"金融监管 pack"就是一个 `.guide.yaml` 文件夹。

CLI 接收可选路径：

```bash
olav kb import-guides /path/to/directives    # 显式目录
olav kb import-guides                        # 默认：.olav/workspace
```

---

## 检查记忆状态

入库前后审计存储里有什么：

```bash
olav kb status                  # 按 category / origin / scope 汇总
olav kb search "BGP register"   # 自然语言语义搜索
```

或直接通过 Python：

```python
from olav.core.memory import get_store, MEMORY_TABLE

tbl = get_store().get_table(MEMORY_TABLE)
guides = tbl.search().where("category = 'usage_guide'", prefilter=True).to_list()

for g in guides:
    print(f"{g['scope']:10s}  {g['id']}  ← {g['text'][:80]}")
```

---

## Directive 不奏效时 —— 退回 L1+L2

Directive 是**主动**的 —— 你提前写好。但你不可能预想到每个细节。对事后才发现的东西，OLAV 的 L1+L2 层会自动处理：

- 每次成功的工具调用作为先例被捕获（L1）。
- 当样本足够多，curator 跑一次把它们提炼为模式（L2）。
- 模式随后在未来查询里浮现（与 directive 并列或替代）。

所以你**不需要**为每条约定写 directive —— 只为那些能提前清楚说出来的写。剩下的从观察中自然浮现。

完整 L1+L2 细节见 [自我改进循环](../concepts/self-improving-loop.md)。

---

## 速查表

| 任务 | 命令 |
|---|---|
| 写 directive | 把 `.guide.yaml` 放到 `<agent>/guides/` |
| 入库 | `olav kb import-guides` |
| 检查存储 | `olav kb status` / `olav kb search "<查询>"` |
| 打包分发 | tar 打包 `*.guide.yaml`；接收方 `olav kb import-guides /path/to/extracted` |
| 更新已有 | 改 YAML、重跑 `olav kb import-guides`（按 memory ID 幂等）|
| 强制 scope | 放 `core/guides/` 下的入库为 `scope=global`，否则 `scope=<agent>` |

---

## 相关阅读

- **[记忆架构](../concepts/memory-architecture.md)** —— 类别有哪些、AutoRecall 怎么工作
- **[自我改进循环](../concepts/self-improving-loop.md)** —— L1+L2 层
- **[知识库](knowledge-base.md)** —— 同存储的文档导入侧
