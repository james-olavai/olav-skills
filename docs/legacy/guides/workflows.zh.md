# 多步骤工作流

工作流把多个子代理串成一条经过验证的交付链——例如：*起草变更计划 → 对照现网状态做
轻量预检 → 用 Batfish 做形式化验证*——用户只需一句自然语言请求。工作流以 **YAML
数据**声明（而非提示词散文），由平台确定性地渲染进域编排器的系统提示。

!!! abstract "特性声明"
    | ID | 声明 | 状态 |
    |----|-------|--------|
    | C-WF-01 | 工作流从 `<agent_dir>/workflows/*.workflow.yaml` 加载 | ✅ main |
    | C-WF-02 | 非法工作流文件被跳过，绝不阻塞 agent 启动 | ✅ main |
    | C-WF-03 | netops 内置 `change_plan` 与 `decommission_impact` 两条工作流 | ✅ main |
    | C-WF-04 | 重复失败的工具调用会被熔断（3 次短路 / 6 次硬停止） | ✅ main |

---

## 使用内置工作流（netops）

工作流不需要显式调用——自然表达意图，编排器会按各工作流的 trigger 匹配。

**变更计划验证链**（add / modify / 冗余 / 变更）：

```bash
olav --agent netops "Draft a change plan to add a redundant eBGP uplink \
  to the AARNet peer on the 'alpha' border router"
```

**退役影响链**（decommission / remove / 退役 / 下线）：

```bash
olav --agent netops "Decommission the branch switch alpha-br-3560 — draft \
  the removal plan and tell me what we lose"
```

两者都会执行三次委派，结果按固定标题堆叠返回：

```
## Draft                              ← analyzer：计划落盘至 exports/change_plans/
## Pre-check (reporter)               ← 状态核对 + 独立复算 blast radius
## Formal verification (Batfish)      ← 子网冲突、BGP 兼容性、可达性裁决
```

预检层秒级出结果（查状态库）；Batfish 层给出配置语义级的形式化裁决。若 Batfish
未运行，该步骤会优雅降级为部署指引——计划与预检结果照常交付。

## 编写自己的工作流

在域 agent 目录下的 `workflows/` 放一个 `*.workflow.yaml`：

```yaml
schema_version: 1
name: change_plan
title: Change-plan workflow — draft, pre-check, formally verify
trigger: 'change-plan intent for ADDING or MODIFYING config ("add / modify /
  redundant") — a change plan is not done until it is verified'
artifact: 'the plan FILE PATH (exports/change_plans/<file>.md, found in
  step 1''s reply); never paste the plan content itself between steps'
steps:
  - agent: analyzer                # 必须是该编排器已声明的子代理
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

校验刻意宽容：文件格式错误、步骤引用未声明的子代理、headers 数量不匹配都会让该
工作流**跳过**（回退到普通单代理路由）——绝不阻塞启动。

### 编写规则（实测教训）

1. **产物按引用传递。** 步骤之间只传文件路径或快照 ID——不传内容本体。小型本地模
   型在长文本往返中会丢失内容。
2. **每个步骤代理都需要机器可提取的输出契约。** 下一步只能引用上一步回复中*点名*
   的产物。若某子代理习惯用散文作答而不报产物名，先给它加显式尾行规则（如
   `PROFILE_PATH: <path>`），再写工作流。
3. **先审计编排器已有的硬规则。** 成熟编排器里"子代理的回复即最终答案"这类规则会
   压制注入的工作流——这是实测结论而非推测。要么重构规则，要么放弃在该域组合。
4. **trigger 必须不相交。** 两条工作流争抢同一个意图词会让选择变成掷硬币
   （`change_plan` 负责 add/modify；`decommission_impact` 负责 remove/退役）。
5. **每域 ≤2–3 条。** 每条渲染后约占编排器提示 30–40 行。
6. **2–4 步，每步都是既有子代理的有界任务。** 开放式探索和自闭环任务不适合工作流。

## 与记忆转向层的关系

| 层 | 机制 | 用途 |
|---|---|---|
| 控制流——*执行哪些步骤* | workflow YAML → 系统提示 | 固定的已知形状序列 |
| 步内行为——*把单步做好* | 知识库 guide（自动召回注入） | 如"冗余类计划必须引用 blast-radius 数字" |
| 进化——*发现新工作流* | 轨迹回顾 → 人审 → 固化为 YAML | 把反复出现的手工序列变成声明 |

不要把控制流放进记忆 guide：召回按相似度排名且有配额上限，编排指令只会*偶尔*在
场——在 31B 级模型上实测约 33% 跟随率，而系统提示渲染的工作流步骤执行率为 100%。

## 可靠性护栏

工作流运行在平台的工具调用熔断器保护之下：同一失败调用连续 3 次后短路（不再执
行），模型仍坚持重复到 6 次则硬停止并诚实标注"结果为部分完成"——损坏的工具调用
不再可能滚雪球成上下文溢出崩溃。
