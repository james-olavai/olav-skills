---
title: 术语表 & 翻译风格
description: 中英术语对照规范表。所有翻译必须遵循本表，避免 OLAV 文档跨语言时出现表述漂移。
---

# 术语表 & 翻译风格

OLAV 文档双语化。一个英文术语在中文里可能有多种合理译法，本表
钉死规范译法。漂移（例如同页里"agent"一会儿译"代理"、一会儿译
"智能体"）会让站内搜索断裂、读者在中英切换时困惑。

若有未覆盖的新术语，在第一条用到它的 PR 里**双语**补齐到本表。

## 核心平台词表

| English | 中文 | 说明 |
|---|---|---|
| **Agent** | 代理 | 不译"智能体"。OLAV 的 agent 是基于 graph 的工具编排 LLM worker，"代理"贴近 LangChain 的语义。"智能体"是 AI 行业流行词，但不切合 OLAV 真实执行模型。 |
| **Sub-agent** | 子代理 | "agent" 的复合形。 |
| **Orchestrator** | 编排器 | 路由到子代理的顶层 agent。不译"调度器"（暗示 cron 类时间触发）。 |
| **Skill** | 技能 | `SKILL.md` + 工具组成的 agent 加载包。不译"能力"（太抽象）。 |
| **Tool** | 工具 | LangChain `@tool` 装饰的函数。全站统一。 |
| **Workspace** | 工作空间 | `.olav/workspace/<agent>/` 运行树。不译"工作区"。 |
| **Memory**（语义记忆） | 记忆 | LanceDB 后端检索库。 |
| **Memory**（RAM） | 内存 | 仅当字面指进程 RAM / DuckDB 内存克隆时使用。不要混。 |
| **Knowledge Base / KB** | 知识库 | UKS `memory` 表内容的用户面命名。 |
| **Guide** | guide | 行内保留英文（如 `*.guide.yaml`）。**指南**仅用于长 tutorial 的标题，永远不要拿它翻 YAML 文件类型。 |
| **Tier**（source-tier） | 分层 / tier | "Source-tier" 译 **来源分层**；具体 tier 名（`vendor` / `platform` / `team` / `user`）保留英文。 |
| **Reasoning / Thinking** | 推理 | 不译"思考"（太口语）。如"thinking mode" → "推理模式"。 |
| **Audit chain** | 审计链 | `kb_audit/` + `audit.duckdb` 合称。 |
| **Tombstone** | tombstone | 外来词；每页首次出现时简短解释。 |
| **Idempotent** | 幂等 | 标准译法。 |
| **Foreign key (FK)** | 外键 / FK | 代码上下文里允许用 FK 缩写。 |
| **Snapshot** | 快照 | 标准。 |
| **Drift** | 漂移 | 标准。 |
| **Hook** | hook | 外来词；长 prose 可写"勾子"。 |
| **Middleware** | middleware | 外来词，不译。 |
| **Callback** | callback | 外来词。 |

## R102 / R88 / R85 类编号

这些 token 在 dev_docs 和 changelog 里反复出现：

| Token | 含义 |
|---|---|
| **R102** | 对话式记忆注入（`memory_curator` 子代理路径）。 |
| **R88-A** | 服务注册时生成 per-service 工具（ADR-0007 前身）。 |
| **R85** | inline-save / `format_and_export` 写类工具。 |

中英双语都保持原样不译。

## 代码邻近 prose 里的大小写

* Agent / sub-agent / tool 名按源码小写：`core`、`netops`、`analyzer`、
  `sim`、`memory_curator`。
* SKILL.md / AGENT.md / MANIFEST.yaml 保留全大写 + `.md` / `.yaml` 扩展。
* CLI 动词按敲入形式小写：`olav user create`、`olav kb remove`。
* 中英两侧都不要翻译这些 token。

## 新增术语流程

挑一个译法、同 commit 更新本表。若英文在中文里有歧义（如 memory →
记忆 vs 内存），优先按上下文消歧，其次按惯例。
