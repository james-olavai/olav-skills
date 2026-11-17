---
title: Glossary & Translation Style
description: Canonical English ↔ Chinese term pairs.  Translators MUST follow this table so OLAV documentation reads as one voice across languages.
---

# Glossary & Translation Style

OLAV's docs are bilingual.  When a term has more than one plausible
Chinese translation, this table picks the canonical form.  Drift —
e.g. translating "agent" sometimes as 代理 and sometimes as 智能体
within the same page — makes search broken and confuses readers
switching between EN and ZH versions.

If a new term doesn't fit anything below, add it here (in **both**
languages) in the same PR as the first doc that uses it.

## Core platform vocabulary

| English | Chinese | Notes |
|---|---|---|
| **Agent** | 代理 | NOT 智能体. OLAV agents are tool-orchestrating LLM workers; the literal "代理" (proxy) is closer to LangChain's framing. "智能体" is the AI-industry buzzword but doesn't match OLAV's actual graph-based execution model. |
| **Sub-agent** | 子代理 | Compound from "agent". |
| **Orchestrator** | 编排器 | The top-level agent that routes between sub-agents.  Not 调度器 (scheduler — implies cron-style timing). |
| **Skill** | 技能 | The `SKILL.md` + tools package an agent loads.  Not 能力 (capability — too abstract). |
| **Tool** | 工具 | LangChain `@tool`-decorated function.  Uniform across docs. |
| **Workspace** | 工作空间 | `.olav/workspace/<agent>/` runtime tree.  Not 工作区. |
| **Memory** (semantic) | 记忆 | LanceDB-backed retrieval store. |
| **Memory** (RAM) | 内存 | Only when literally referring to process RAM / DuckDB in-memory clones.  Don't mix. |
| **Knowledge Base / KB** | 知识库 | The user-facing name for the UKS `memory` table contents. |
| **Guide** | guide | Keep the English word inline (e.g. `*.guide.yaml`).  Use **指南** only in headings of long-form tutorials, never as a translation of the YAML file type. |
| **Tier** (source-tier) | 分层 / tier | "Source-tier" → **来源分层**; per-tier names (`vendor` / `platform` / `team` / `user`) stay English. |
| **Reasoning / Thinking** | 推理 | Not 思考 (too colloquial).  e.g. "thinking mode" → "推理模式". |
| **Audit chain** | 审计链 | `kb_audit/` + `audit.duckdb` together. |
| **Tombstone** | tombstone | Loanword; explain on first use per page. |
| **Idempotent** | 幂等 | Standard. |
| **Foreign key (FK)** | 外键 / FK | Use English abbreviation in code-adjacent prose. |
| **Snapshot** | 快照 | Standard. |
| **Drift** | 漂移 | Standard. |
| **Hook** | hook | Loanword; in long-form prose may write "勾子". |
| **Middleware** | middleware | Loanword.  Don't translate. |
| **Callback** | callback | Loanword. |

## R102 / R88 / R85 references

These appear throughout dev_docs and changelogs:

| Token | Means |
|---|---|
| **R102** | Conversational memory ingestion (the `memory_curator` sub-agent path). |
| **R88-A** | Per-service tool generation at registration time (ADR-0007 precursor). |
| **R85** | Inline-save / `format_and_export` write-class tool. |

Keep these as-is in both languages.

## Capitalisation in code-adjacent prose

* Agent / sub-agent / tool names stay lowercase as in source: `core`,
  `netops`, `analyzer`, `sim`, `memory_curator`.
* SKILL.md / AGENT.md / MANIFEST.yaml stay all-caps with `.md` / `.yaml`.
* CLI verbs are lowercase as typed: `olav user create`, `olav kb remove`.
* Don't translate them in either language.

## When you must coin a new term

Pick one form, then update this table in the same commit.  If the
English term is ambiguous in Chinese (like "memory" → 记忆 vs 内存),
disambiguate by context first, by convention second.
