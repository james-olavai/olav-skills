---
title: 更新日志
description: OLAV 平台的发布记录 — 近期变更、不兼容的重命名、迁移说明。
---

# 更新日志

这里只记录对用户可见的变化。完整 commit 历史见
[gitea 仓库](http://192.168.100.50:3000/admin/olav)（内部）
或公开发布对应的 GitHub 镜像。

## v0.26.0 — 让静默的行为可见；provider 层从"假定兼容"改为实测

这一版的主题是"把发生了什么告诉你"。导入丢弃了 74% 的命令输出却一声不吭；
自动导出只有在模型愿意提起时你才知道；`olav doctor` 在一个查询路径全线不可用的
安装上报了 9/9 通过。

### 新增

- **`olav doctor` 从 9 项检查扩到 11 项。** `auth` 会说明当前认证模式以及由谁来
  提供它 —— 曾出现过 9/9 全绿但每条查询路径都是死的：`init` 写入了
  `auth.mode=token`，而 token 的 provider 在 olav-ent 里。`context` 把 agent 的
  上下文预算与服务端实际声明的窗口对比，长任务里的 `agent error, code 400`
  就是这里来的。
- **`olav doctor --verify`** 证明服务真的能用，而不只是"能应答"；每个注册的服务
  各自贡献自己的就绪检查。
- **导入报告。** 导入会写出 `exports/import_reports/<snapshot>.md`，说明解析了多少、
  哪些没解析、以及原因 —— `raw_only`、`no_parser_registered`、`parser_no_match`
  或解析器报错。在 339 台设备的数据集上，8,761 条命令输出产生了 2,278 行结构化数据；
  那 74% 此前完全不可见。凡是 `learner` 能补上的缺口，报告会指出来但**不会去跑**
  —— 那会把两分钟的导入变成很长的过程。
- **查询导出会主动告知。** 当 `execute_sql` 为了塞进模型上下文而截断行数、
  把完整结果写到文件时，CLI 会说明总行数、文件路径，以及你实际看到了多少。
- **provider 支持改为实测而非假定。** 每个 provider 有独立驱动和各自的
  function-call 确定性默认值，由一层离线检查（钉住实际发出的请求）加一个
  可选的真实调用共同保证。

### 修复

- **查询存储的配置文本会失败**，报 `'str' object has no attribute 'items'`。
  字段级的长度预算被套用到了整行上，于是任何宽行都不再是"行"。
- **`olav init` 不再覆盖你选定的认证模式**，只在本次运行**新建**的配置上选择模式。
- **Claude 会以 400 拒绝请求** —— 当 temperature 超过 1.0（它的上限）时。
  现已按 provider 分别钳制。
- **记忆捕获每次查询跑了两遍**，多付一次 LLM 往返并写入重复记忆。
- **重复的查询会留下重复的 CSV。** 导出改为内容寻址，相同结果集复用同一文件。
- **没有 inventory 时 `devices.role` 一直是空的**；现在会在主机名可判断时从中推导。
- **两个同机部署可能互相认领对方的容器** —— compose 项目名已加命名空间。

### 移除

- **Mistral provider 支持**及其 `[mistral]` extra。配置向导曾提供一个普通安装
  并不附带驱动的 provider。

## v0.25.1 — 打包修复：作者编写的工作区文件现在会随包发布

### 修复

- **16 个作者编写的工作区参考文件从未被打包。** 那些塑造 agent 行为的指南与参考
  在仓库里存在、却不在 wheel 里，于是 pip 安装的行为与源码检出并不一致。
- **`render_report` 无条件初始化 LLM**，导致不需要模型的报告也必须有模型可达。

### 变更

- 工作区提示词里面向开发者的文本统一为英文，混合语言的提示不再进入模型。

## v0.25.0 — 分层：团队功能迁往 olav-ent

### 变更

- **Web API 与 WebGUI 迁移到 olav-ent。** 开源包是 CLI、运行时和 agent；
  团队功能（多用户认证、Web 界面）属于企业扩展。
- **`auth.mode=none` 直接把本机 OS 用户视为管理员。** 单人使用无需任何登录。
- **普通安装更小** —— OSS 依赖里去掉了 `fastapi` 和 `ldap3` extra。

### 新增

- **DuckDB 只有一条写入路径**（`open_write_connection`），并发写入不再互相破坏。

## v0.24.1 — 审计与导出修复

### 修复

- **审计后置检查的提示给出了不存在的命令**（`olav -a ops`），现在指向真实 agent。
- **Batfish 导出**在设备没有存档配置时，回退到内置的各平台备份命令，而不是导出空内容。

### 新增

- **`olav doctor` 增加工作区冲突检查** —— 发现竞争的 `~/.olav` 目录会报出来。

## v0.24.0 — 循环保护、声明式工作流、诚实的 doctor

### 新增

- **多步任务可以声明**而不必临场发挥：形态已知的工作按声明在子 agent 间路由。
- **每个 agent 都有零 LLM 的熔断器。** 模型重复调用同一个工具时由 Python 叫停，
  而不是指望模型自己察觉。
- **`olav doctor` 会说明它找到的后端** —— LLM 的模型与端点、embedding 同理 ——
  配置错误不必等到跑起来才发现。
- **每个脚本可在 `SKILL.md` 里声明超时。**

### 修复

- **首次运行向导拒绝它自己刚打印出来的编号选项**（embedding 菜单）。
- **API 模式的 embedding 读了一个不存在的配置键**，于是你配置的端点被静默忽略。
- **`deploy_service` 在容器已经退出时报告健康** —— `docker compose ps` 少了 `-a`。
- **一个损坏的审计视图会让整个审计失败**；现在按 job 单独降级。

## v0.23.1 — 元数据

### 变更

- 项目 URL 指向 `https://olavai.com` 与文档站；作者与版权信息统一。

## v0.23.0 — 自我反思、定时任务、召回基准

同时包含内部标记为 0.22.2、但从未发布的那批改动。

### 新增

- **`admin/reflector`** —— 每日自我反思：把反复出现的错误特征转成 agent 之后
  能召回的约束。
- **`olav cron`** —— 对已声明的定时任务做 `enable` / `disable` / `list`，
  显式开启而非隐式生效。
- **`olav kb bench`** —— 衡量知识库是否真能召回存进去的东西，不带领域假设。
- **`olav doctor` 从 3 项零 LLM 检查扩到 8 项**，新增 agents、tools、脚手架等。
- **`draw_topology`** —— 由 writer agent 一次生成 draw.io 或 Mermaid 拓扑图。
- **`execute_sql` 限制单元格文本长度**，一个 70 KB 的配置列不再吃掉模型的整个上下文。
- **Batfish 自助部署** —— 模拟器发现 Batfish 缺失时会诊断并提议部署，而不是直接失败。

### 修复

- **定时任务静默地什么都没做** —— 日志重定向指向了一个不存在的目录。
- **`register_service` 写入的认证键与读取方并不一致**，注册过的凭据被忽略。
- **支持 NetBox 4.5** —— Bearer `nbt_` token 与 `/api` 前缀。
- **`olav doctor --json` 输出的不是合法 JSON** —— rich 在值中间做了换行包裹。
- **响应慢的本地模型被报成"不可达"**，而不是"慢"。

## v0.22.1 — 本地 embedding 端点

### 修复

- **Ollama、llama.cpp、vLLM 的 embedding 端点**按配置正常工作；
  客户端不再假定返回是 OpenAI 的形状。

## v0.22.0 — 零仪式上手

### 新增

- **在空目录里直接敲 `olav` 就能把自己配好** —— 第一个问题之前不需要初始化仪式。
- **`olav doctor` 与 TUI 的 `/doctor`** —— 确定性、零 LLM 的健康检查，
  在怪罪模型之前先跑它。
- **欢迎界面报告你工作区里的真实发现**，而不是轮播小贴士。
- **对话式自我配置** —— admin agent 可在审批下修改自己的配置。
- **工作区写入与 cron 变更可撤销。**

## v0.21.1 — 子代理级 LLM 覆盖、KB 来源分层生命周期、NetBox CSV 同步

状态：**进行中**（rc4 之后，准备打 tag）。

### 新增

- **子代理级 LLM 覆盖。** 每个 `SKILL.md` / `AGENT.md` 现在可以带一个
  可选的 `llm:` 块，字段任选 `model`、`temperature`、`max_tokens`、
  `base_url`、`model_provider`、`num_ctx`、`num_predict`。缺省字段
  回退到全局 `api.json.llm.*`。不引入 profile 中间层 —— 单条
  fall-through 链。仓内消费者：
  - `sim`、`investigate`、`learner` → `temperature: 0.0`（确定性输出）
  - `core/memory_curator` → `max_tokens: 4096`（R102 HITL 消息很短）
  - `analyzer`（不覆盖）— 继承全局 32K
- **全局 `max_tokens` 由 16000 提到 32768。** gemma4:31b nothink 现在
  能生成长结构化表格 / 报告而不被截断。若不需要可在 skill 层单独
  覆盖（memory_curator 就是反过来的例子）。
- **来源分层 KB 生命周期（见 `dev_docs/79`）。** 每个 `*.guide.yaml`
  都带一个 `source_tier` 字段（`vendor` / `platform` / `team` /
  `user`）。优先级 + 检索权重由 tier 推导，不再是手设整数。
  - R102 对话注入落在 `source_tier: user`（priority 3，weight 0.83）
    —— 操作员临时口述规则不会误盖架构性 invariant
  - 旧 schema v1 仍能加载（默认 = `user` tier，向后兼容已显式声明的
    `priority:` 字段）
  - 新 CLI：`olav kb remove <intent> --reason "..."`（写
    tombstone YAML + 删 LanceDB 行 + `kb_audit/<ts>_remove_<intent>.yaml`
    审计行）、`olav kb show <intent>`、`olav kb list-guides
    --tier <name>`
- **`kb_audit/` git-可追踪审计链。** 每次 KB 写操作（R102 commit、
  `kb remove`、NetBox push）都会落一行
  `kb_audit/<iso-ts>_<verb>_<intent>.yaml`，含
  `action / intent / actor / timestamp / body_sha256` + 结果计数。
  跟 `audit.duckdb` 互补 —— git 跟踪，独立于运行时 DB 存活。
- **`olav user` CLI。** `olav admin add-user/...` 的友好别名。
  `olav user create <name>` 默认要求该 Linux 用户已存在
  （`pwd.getpwnam(name)`）—— OLAV 用户模型对齐操作系统，不引入
  平行身份。token 以 hash + salt 存储，明文只在 create 时显示一次。
  容器场景可用 `--no-verify` 跳过 Linux 检查。
- **采集时凭证脱敏**（`olav.core.redaction.scrub`，ADR-0008）。
  两层脱敏管道在网络配置进入 `audit.duckdb` 或导出数据集之前去除
  密码 / SNMP community / BGP MD5 key / IPSec PSK / TACACS / RADIUS
  共享密钥，同时保留 IP / 主机名 / ASN / 拓扑关系，使 diff 和故障
  诊断逻辑仍可正常工作。
  - **采集层**: `olav.core.redaction.scrub` 封装
    [netconan](https://github.com/intentionet/netconan)（Batfish 团队
    出品，~50KB 纯 Python；内置 55 条敏感项 regex + 5213 个保留词；
    每个 workspace 独立 `.redaction_salt` 文件 chmod 600）。接入
    `netops_init/run.py:_collect_cmd`（采集边界）和
    `AuditEventRecorder.record_message`（chat / LLM 文本的纵深防御）。
    替换标记 `netconanRemovedN`。
  - **企业网关层**: `audit_recorder.redact_sensitive`（5 条 regex 产出
    `[REDACTED]`）驱动 `audit_dataset_export.redact_audit_run` 的
    Layer 1，并在 gate-check 阶段再次运行作为最终逃逸检测器。
  - **配置**: `api.json.redaction.{enabled, anon_ip,
    extra_sensitive_words, extra_reserved_words}` +
    `OLAV_REDACTION=0` 环境变量总开关。通过
    `pip install 'olav[redaction]'` 安装拉取 `netconan>=0.13`；
    未安装时 OLAV 仍可运行，Layer 1 默认 fail-open 并打 WARNING。
  - `tests/governance/test_redaction.py` 16 条治理测试覆盖
    Cisco / Junos / salt 管理 / fail-open 语义 / 幂等性。
- **NetBox CSV 同步链**（`dev_docs/71` Ch10）。读（netops）与写
  （services）解耦的三阶段工作流，CSV 文件作为中间契约：
  - `olav --agent netops "/export_netbox_csv"` —— 从
    `netops.devices` 导出 NetBox-import-shape 的 CSV
    （列映射见 `netops/guides/netbox_csv_export.guide.yaml`）
  - `olav --agent services "/import_netbox_csv"` —— 干跑校验
    CSV 形状，写 `exports/reports/netbox_import_dry_run.md`
  - `olav --agent services "/import_netbox_csv --write
    --endpoint ... --token ..."` —— 真正向运行中的 NetBox 推送，
    5 步幂等 lookup-or-create 链（site → manufacturer →
    device-type → device-role → platform → device），每次写
    入都落一行 `kb_audit/` 审计

### 修复

- **CLI 重新写入 audit 表。** v0.20.3 把
  `OLAV_MIDDLEWARE_MODE` 默认翻为 `middleware`；CLI 调用路径
  从未把 `OlavRunContext` bind 到 `astream_events`，导致
  `AuditMiddleware._get_context()` 永远返回 None，每次工具调用
  都静默 no-op。`record_run_start` 又没传 `thread_id`，
  `sessions` UPSERT 守卫永远失败。两条都修：CLI 镜像 API
  server 的 context-bind 模式，并把 `thread_id=session_id
  or run_id` 串到每个 turn。结果：
  `audit_runs / sessions / audit_events / audit_messages /
  audit_tool_calls` 在单次工具调用后全部正常写入。
- **不再发 `reasoning_effort: minimal`。** Ollama OpenAI-compat 严格
  校验该字段，只接受 `high / medium / low / max / none`。删除
  —— `chat_template_kwargs.enable_thinking=false` 已经覆盖
  qwen3 / gemma4 nothink 路径。
- **`olav skill install` 拒绝覆盖平台 workspace。** 领域包安装
  （如 `olav-netops`）若携带了 `audit/` 的 dev-mirror，过去
  `olav skill install` 会静默盖掉平台 bundled audit/。现在
  `_PLATFORM_OWNED = {core, audit, services}` 命中已存在路径
  直接跳过 + 警告，逃生口 `--force-overwrite`。
- **`/netops_init` ingest 门控。** Stage 4 现在区分 "SSH 已收集"
  和 "DB 已 ingest"，当 ingest 录入 0 行但 SSH 收集成功时
  打印 `❌ FAILED at the ingest stage` + 非零退出。之前
  即使 ingest 报错也照样 `✅ Network initialization complete`。
- **5 个 SQL view schema guide 出厂**
  （`netops/guides/sql_schema_*.guide.yaml`）— 记录 BGP /
  OSPF / interface / route / device-and-topology 各 view
  的精确列名 + 可运行示例。AutoRecall 在 schema 探索查询时召
  回；gemma4 nothink 首次 SQL 即可命中正确列，不再列名 trial-
  and-error。
- **`olav user` subparser 新增。** CLI 此前只能走更啰嗦的
  `olav admin add-user`；`olav user create / list / token /
  revoke` 现在是用户面 canonical 形式。
- **平台 audit workspace 打入 wheel。** v0.21.1 前
  `pip install olav==0.19.0 && olav init` 只出 2 个 agent
  （`core` + `services`）—— audit/ 存在于 dev 仓但未进 wheel
  的 data 路径。fresh install 现在出 3 个平台 agent
  （audit / core / services）。

### 迁移

- **无 flag-day。** schema v1 `*.guide.yaml` 继续可加载，priming 时
  按 `source_tier: user` 处理 + 一行 deprecation 警告。按需迁移即可；
  v2 形态 = `schema_version: 2` + 显式 `source_tier:` 字段。
- **R102 commit 降级到 `user` tier。** 之前若依赖操作员口述规则
  压过平台 guide，这条路径不再走通。要升级，请走 git PR 改
  YAML `source_tier: team` 并合入主线 review。
- **Lab / CAB 内容已外迁。** `lab/` 子代理 + CAB 流程 + TCF schema
  demo + fault-injection 脚本已搬到 `olav-ent`。平台基 runsheet
  （`dev_docs/71`）删除旧 Ch7 + Ch8；企业 runsheet 接收它们。

---

## v0.21.0 — Web UI agent 路由 + 服务生命周期硬化

状态：**进行中**（本文撰写时最新为 rc4）。

### 修复

- **Web UI：左下角 agent dropdown 真的会切换运行的 graph。**
  rc2 之前，FastAPI server 用一个全局 `_agent_instance` 单例；
  你在 dropdown 里点 "ops" 只改了 audit 事件的 tag，背后跑的还是
  core 的 graph。`get_agent()` 现在是按 `assistant_id` 分键的 cache，
  5 个 agent 可以各跑各的、互不污染。
- **`POST /reload` 再次返回 200。**  rc2 把单例换成 cache dict 时
  忘了更新 `/reload` handler，每次 reload 都抛 `NameError`。
  现在 handler 会遍历 cache、逐个 best-effort-close，然后清空。
- **Middleware 模式下 audit 能看到 run_id。**  v0.20.3 把
  `OLAV_MIDDLEWARE_MODE` 默认改成 `middleware`；但 web server 从没
  给 `astream_events` 附带 `OlavRunContext`，导致
  `AuditMiddleware` 拿不到 `run_id` — 每次 web 请求都没被审计。
  现在两种模式都正确传 context。
- **刷新浏览器不丢 thread + agent 绑定。**  WebUI 把
  `currentThreadId` 和每个 thread 的 `agentId` 都写进
  `localStorage`；F5 之后状态完整恢复。
- **`core` 在 `/agents` 响应里强制置顶。**  装了 olav-netops 后
  多了 4 个按字母排在 `core` 前面的 workspace，导致 WebUI
  默认 agent 变成了 `audit`。server 端现在把 `core` 钉在 index 0。
- **服务进程被钉到正确的 workspace。**  rc4 之前，`uvicorn` /
  agent daemon / syslog receiver 从一个在模块 import 时冻结的
  `PROJECT_ROOT` 常量推导 PID/log 路径。如果从带 `pyproject.toml`
  的另一个仓库目录启 olav，spawned 进程会 silently 继承错误的
  cwd、读错 `users.duckdb` —— 所有登录都 401。现在改为基于
  `.olav/` 标记、每次调用重算的 cwd，且 `Popen(..., cwd=...)`
  显式传。
- **全新 wheel 安装下 `olav service start --all` 跑得通。**  其实
  不是 bug —— 参数顺序是 `olav service <action> --all`，从来
  就对 —— 但 runsheet 示例已经重新验过。
- **`olav skill install <path>` 仍然可用**，通过 v0.21.0-rc4 的
  deprecation shim。单数的 `skill` 分发器在 v0.20.3 已删；shim
  原样转发给 `olav agent install` 并打印一行黄色 warning。
  详见 [CLI 参考](cli.md) 的迁移说明。

### rc4 新增

- [`scripts/sync_netops_workspace.py`](http://192.168.100.50:3000/admin/olav/src/branch/0.11.0/scripts/sync_netops_workspace.py)
  — 同步平台 vendored 的 `.olav/workspace/ops/` 与 olav-netops
  的权威副本 `olav-netops/.olav/workspace/ops/`。默认方向：
  root → olav-netops；`--reverse` 反向；`--check` 是 CI 漂移守护。

---

## v0.20.3 — deepagents 迁移收官（P7 final）

- **移除 `olav skill install` 子命令。**  用 `olav agent install`。
  （v0.21.0-rc4 加了 deprecation shim 保留兼容，见上。）
- **`OLAV_MIDDLEWARE_MODE` 默认翻到 `middleware`。**  老的 callback
  plugin 仍可通过 `OLAV_MIDDLEWARE_MODE=callback` 使用，给
  `AuditMiddleware` 留一个完整 release 的验证期。
- **`parse_subcommand_args` 渗透到 7 个 verb。**  共享 helper 处理
  argparse 的引号/空参/`--` 边界。
- **`workspace_discovery.py` 合并。**  三处重复的扫描器合成一处；
  CLI、web `/agents`、TUI 都走同一代码路径。

## v0.20.2 — P1 + P2 + P6 cutover

- **Native TUI**（走 `langgraph dev` 子进程）成为默认，替换
  原先的 in-process overlay。`OLAV_TUI_MODE=overlay` 可回退到
  v0.19.x 路径紧急使用。
- **Tool-loader** 启动时自动发现
  `.deepagents/agents/<name>/tools/*.py` 和
  `.deepagents/agents/<name>/skills/<skill>/tools/*.py`。
- **v0.20 workspace 布局迁移** 通过
  `olav migrate v0_20_layout` 提供（幂等 + `--dry-run`）。

## v0.20.1 — P3 middleware 迁移

- **`AgentMiddleware` 子类** 用于 audit / guardrails /
  memory-capture / memory-recall / uks-capture，与 callback plugin
  路径并存。`OLAV_MIDDLEWARE_MODE` 环境变量切换两条路径。

## v0.20.0 — P5 graph 工厂 + `olav agent install`

- **`olav agent install <path>`** 作为公开安装 verb 出现。
  `olav skill install` 还能用（两者共享底层 dispatcher）；这个
  release 是重命名的起点。
- **`graph_factory.build_graph()`** 内部 API，按 `assistant_id`
  构造 LangGraph — 为 v0.21.0-rc2 的 per-id cache 打基础。

---

## v0.19.0 — deepagents 之前的最后一个版本

见 [DEMO_RUNSHEET](https://github.com/olav-ai/olav/blob/v0.19.0/dev_docs/42.%20DEMO_RUNSHEET.md)（历史存档）。
