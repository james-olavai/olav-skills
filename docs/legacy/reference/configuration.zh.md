# 配置参考

所有配置集中在 `.olav/config/api.json` 文件中。运行 `olav init` 会创建一个基础版本。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-38 | `agent_overrides` 为不同 Agent 指定不同 LLM 模型 | ✅ v0.10.0 |
    | C-L2-39 | `OLAV_LLM_*` 环境变量覆盖配置文件 | ✅ v0.10.0 |

!!! warning "保护配置文件"
    `api.json` 包含 API 密钥，不要提交到 Git。请将 `.olav/config/` 加入 `.gitignore`。

---

## 文件结构

```json
{
  "shared":    { ... },
  "llm":       { ... },
  "embedding": { ... },
  "reranker":  { ... },
  "auth":      { ... },
  "agent_overrides": { ... }
}
```

---

## 部署模式（三档） { #deployment-tiers }

OLAV 把 LLM / Embedding / Reranker 三个端点设计成可独立切换。下面三档都是契约级支持，按你的网络/合规/算力条件选：

=== "Layer 1 — 全本地离线（推荐生产）"
    用 on-CPU SentenceTransformer embed + 关 reranker。零 cloud key，不出本机。首次启动自动从 HuggingFace 下载嵌入模型（约 440 MB）。

    需要装 on-CPU extra——`sentence-transformers` 不在默认安装里：

    ```bash
    pip install 'olav[local-embed]'
    ```

    ```json
    {
      "llm": {
        "provider": "ollama",
        "model": "qwen3.6:27b",
        "base_url": "http://localhost:11434/v1",
        "model_provider": "openai",
        "context_budget": 64000
      },
      "embedding": {
        "mode": "local",
        "local": { "model": "BAAI/bge-base-en-v1.5", "device": "cpu" }
      },
      "reranker": { "enabled": false },
      "auth": { "mode": "token" }
    }
    ```

    Memory 完整可用：语义搜索 + BM25/FTS。轻量替代：把 `bge-base-en-v1.5` 换成 `bge-small-en-v1.5`（130 MB，384 dim）。

=== "Layer 2 — LLM-only 极简（CI / 隔离网）"
    完全不要 embed/rerank。Memory 退化为 BM25 关键字搜索；语义召回失效，但**功能完整不崩溃**。适合测试容器、气隙环境、嵌入式部署。

    ```json
    {
      "llm": {
        "provider": "ollama",
        "model": "qwen3.6:27b",
        "base_url": "http://localhost:11434/v1",
        "model_provider": "openai"
      },
      "embedding": { "mode": "none" },
      "reranker":  { "enabled": false },
      "auth": { "mode": "token" }
    }
    ```

=== "Layer 3 — 全 cloud（最高质量）"
    Cloud LLM + Cloud embed + Cloud rerank。每个端点带各自的 API key，per-section 优先级保证不会串。

    ```json
    {
      "llm": {
        "provider": "custom",
        "model": "google/gemma-4-31b-it:free",
        "base_url": "https://openrouter.ai/api/v1",
        "model_provider": "openai",
        "api_key": "sk-or-v1-XXXXX",
        "context_budget": 64000
      },
      "embedding": {
        "mode": "api",
        "api": {
          "model": "pplx-embed-v1-0.6b",
          "base_url": "https://api.perplexity.ai/v1",
          "api_key": "pplx-XXXXX"
        }
      },
      "reranker": {
        "enabled": true,
        "kind": "openrouter",
        "model": "cohere/rerank-4-fast",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-or-v1-XXXXX"
      },
      "auth": { "mode": "token" }
    }
    ```

可以混搭：「Cloud LLM + 本地 embed + 无 rerank」是最常见的性价比组合。

### 在档位间切换

* **改 `api.json` 即生效** — 重启 `olav` 进程或开新 session，无需 rebuild。
* **embed 维度变化触发 LanceDB 重建** — 不同嵌入模型 dim 不同（`bge-base=768` / `pplx-embed=1024` / `text-embedding-3-small=1536`）。LanceDB 锁 dim，启动时若 dim 不一致会报 `EmbeddingDimMismatchError`。两种解法：
    * `olav agent install <wheel>` 任意 skill — 触发 `allow_dim_swap=True`，自动重建 + 重新 prime。**会清空 user memory**，但 system-level guides/expert KB 自动重建。
    * 手动备份 + 重建：`mv .olav/databases/memory.lance{,.backup}` 然后跑任何会重新 prime 的命令。
* **`shared.api_key` 设置** — 默认空字符串。只在所有端点共用一把 key 时填（少见）。**不要** 把单 service 的 key 放 shared，会导致跨 service 错配。

---

## 共享配置

`shared` 部分包含跨端点的全局默认值。

| 字段 | 必填 | 默认值 | 说明 |
|------|------|-------|------|
| `api_key` | | `""` | 跨端点共用的 API key。仅在 LLM + embed + rerank 都用同一个 provider 时设；多 provider 部署留空 |
| `timeout` | | `120` | 默认请求超时（秒） |

### API key 优先级（Patch B / 2026-05）

每个端点有独立的 api_key 解析顺序：

1. **per-section** — `llm.api_key` / `embedding.api.api_key` / `reranker.api_key`（首选）
2. **shared** — `shared.api_key`（兜底，homogeneous 部署用）
3. **环境变量** — `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`（最后兜底）

per-section 优先意味着多 provider 部署不会被 shared 串联污染：你可以 `llm.api_key=sk-or-v1-...` 同时 `embedding.api.api_key=pplx-...`，两个端点各拿各的 key。

---

## LLM 配置

OLAV 支持多种 LLM 提供商。选择你使用的提供商：

=== "OpenAI"
    ```json
    {
      "shared": { "api_key": "sk-..." },
      "llm": {
        "provider": "openai",
        "model": "gpt-4o"
      }
    }
    ```

=== "Anthropic"
    ```json
    {
      "shared": { "api_key": "sk-ant-..." },
      "llm": {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022"
      }
    }
    ```
    Anthropic 模型自动启用 Prompt Caching，减少重复请求的成本。

=== "OpenRouter（多模型聚合）"
    ```json
    {
      "shared": { "api_key": "sk-or-..." },
      "llm": {
        "provider": "custom",
        "model": "x-ai/grok-2",
        "base_url": "https://openrouter.ai/api/v1"
      }
    }
    ```
    通过 OpenRouter 可以使用几乎所有主流模型。

=== "Ollama（本地离线）"
    ```json
    {
      "llm": {
        "provider": "ollama",
        "model": "llama3.1",
        "base_url": "http://localhost:11434"
      }
    }
    ```
    无需 API Key，数据不出本机。`shared.api_key` 可省略。

### LLM 字段说明

| 字段 | 必填 | 默认值 | 说明 |
|------|------|-------|------|
| `provider` | ✅ | | 提供商：`openai` / `anthropic` / `azure_openai` / `ollama` / `groq` / `mistral` / `custom` |
| `model` | ✅ | | 模型 ID（如 `gpt-4o`、`claude-3-5-sonnet-20241022`） |
| `api_key` | | | 按提供商覆盖密钥（通常使用 `shared.api_key`） |
| `base_url` | | | `custom` 提供商必填，其他可选（用于自建代理） |
| `temperature` | | `0.1` | 生成温度，越低越确定性 |
| `max_tokens` | | `32000` | 最大生成 Token 数 |

---

## 向量嵌入配置

知识库和 Agent 记忆使用向量嵌入做语义搜索。三种 mode：

=== "API 模式（cloud / 自托管）"
    ```json
    "embedding": {
      "mode": "api",
      "api": {
        "model": "openai/text-embedding-3-small",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-or-v1-..."
      },
      "fallback": { "enabled": true }
    }
    ```
    `fallback` 启用后，API 不可用时自动回退本地模型（需要 `local` 块同时配好）。

=== "本地模式（离线/气隙）"
    ```json
    "embedding": {
      "mode": "local",
      "local": {
        "model": "BAAI/bge-base-en-v1.5",
        "device": "cpu"
      }
    }
    ```
    用 SentenceTransformer 进程内推理，仅 CPU/GPU 即可，无服务端、无 cloud 依赖。
    **需要 `pip install 'olav[local-embed]'`**：这个包是唯一会把 `torch` 拉进安装的
    依赖（在 linux-x86_64 上还带 NVIDIA CUDA 运行时，实测约 4.6 GB），所以它作为
    extra 而不是默认依赖发布。未安装时 `mode: local` 会让 embedding 不可用——
    `olav doctor` 的 `embedding` 检查项会报出来。常用模型：

    | 模型 | dim | 大小 | 备注 |
    |---|---|---|---|
    | `BAAI/bge-base-en-v1.5` | 768 | 440 MB | 高质量，CPU 即可 |
    | `BAAI/bge-small-en-v1.5` | 384 | 130 MB | 极轻量 |
    | `BAAI/bge-small-zh-v1.5` | 512 | 90 MB | 中文 |
    | `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 470 MB | 50+ 语言 |

=== "禁用模式（LLM-only 极简）"
    ```json
    "embedding": { "mode": "none" }
    ```
    完全不加载嵌入。Memory 写入会用零向量占位、读取走 BM25/FTS 关键字搜索。**不会崩溃**，但语义召回失效。适合：CI 测试容器、隔离网设备、不需要语义搜索的纯 LLM 场景。

---

## Reranker 配置

Reranker 在 Memory 召回后加一个 cross-encoder 重排序步骤，提升 top-K 相关性。**默认关闭**，开启后效果与 reranker 模型质量强相关（实测某些本地小模型反而让结果变差）。

=== "禁用（默认 / 推荐）"
    ```json
    "reranker": { "enabled": false }
    ```

=== "本地 llama.cpp"
    ```json
    "reranker": {
      "enabled": true,
      "kind": "llama_cpp",
      "base_url": "http://localhost:11433"
    }
    ```
    需 llama-server 启动时带 `--reranking` flag，加载 BERT-class cross-encoder（如 bge-reranker-v2-m3）。

=== "Cloud（OpenRouter / Cohere）"
    ```json
    "reranker": {
      "enabled": true,
      "kind": "openrouter",
      "model": "cohere/rerank-4-fast",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key": "sk-or-v1-..."
    }
    ```
    `kind` 接受 `openrouter` / `cohere` / `openai_compat` / `openai`，都走 `POST {base_url}/rerank` body shape `{query, documents, model}`。`api_key` 缺省时回退 `shared.api_key`。

### Reranker 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `enabled` | ✅ | `true` / `false` |
| `kind` | 启用时必填 | `llama_cpp` / `openrouter` / `cohere` / `openai_compat` / `openai` / `ollama` |
| `base_url` | 启用时必填 | 端点根路径（不含 `/rerank`） |
| `model` | cloud kinds 必填 | 模型 ID |
| `api_key` | cloud kinds 必填（或 fallback shared） | Bearer token |

环境变量 `OLAV_RERANKER_DISABLE=1` 可临时关闭，无需改文件。

---

## 认证模式 { #认证模式 }

在 `auth` 部分配置，决定如何识别用户身份：

| 模式 | 适用场景 | 说明 |
|------|---------|------|
| `none` | 个人使用、本地开发 | **默认（开源版）** —— 身份即操作系统用户，拥有完整管理员权限（无 RBAC 摩擦） |
| `token` | 小团队 | **企业版 (olav-ent)** —— OLAV 内置令牌认证 |
| `ldap` | 企业 | **企业版 (olav-ent)** —— 对接 LDAP 目录 |
| `ad` | — | 未实现 —— **fail-fast** 报错（不静默降级） |
| `oidc` | — | 未实现 —— **fail-fast** 报错（不静默降级） |

!!! info "企业版认证 provider"
    `token` 与 `ldap` 认证 provider 属于企业版扩展 `olav-ent`（`pip install olav-ent`）。开源版 OLAV 以 `auth.mode=none` 运行。RBAC 引擎（角色 + 权限矩阵）在开源版中存在，但填充它的多用户认证 provider 属于企业版。`ad` 与 `oidc` 尚未实现，会直接报错而非静默降级。

---

## 为不同 Agent 指定不同模型

你可以为特定 Agent 使用不同的 LLM 模型——比如快速查询用便宜的小模型，复杂分析用强大的大模型：

```json
"agent_overrides": {
  "core": { "model": "gpt-4o-mini" },
  "audit": { "provider": "anthropic", "model": "claude-3-5-sonnet-20241022" }
}
```

未在 `agent_overrides` 中指定的 Agent 使用顶层 `llm` 配置。

---

## 环境变量

所有配置都可以通过环境变量覆盖，优先级高于 `api.json`：

| 环境变量 | 对应配置 | 说明 |
|---------|---------|------|
| `OLAV_LLM_MODEL` | `llm.model` | 模型 ID |
| `OLAV_LLM_API_KEY` | `shared.api_key` | API 密钥 |
| `OLAV_LLM_BASE_URL` | `llm.base_url` | API 地址 |
| `OPENAI_API_KEY` | `shared.api_key` | OpenAI 快捷方式 |
| `ANTHROPIC_API_KEY` | `shared.api_key` | Anthropic 快捷方式 |
| `OLAV_WEB_PORT` | | Web 服务端口（Round 19 加 env override）|
| `OLAV_WEB_HOST` | | Web 服务绑定地址 |

### 上下文 / prompt 预算调试环境变量

R36 / R42 后平台暴露两个运维级可观测性开关，默认关闭；取值
`1` / `true` / `yes` / `on` 即开启。

| 环境变量 | 引入 | 作用 |
|---|---|---|
| `OLAV_DEBUG_CONTEXT` | Round 36 | 启用后 `agent._inject_static_context` 每 agent 发一条多行 INFO 日志，列每条静态引用的字节 / 估算 token 以及总量对照当前 tier 的 `context_budget`。用于"为什么这 agent 一启动就吃 6K tokens"类诊断。 |
| `OLAV_DEBUG_SUMMARIZATION` | Round 42 | 启用后 `build_summarization_middleware` 打印解析后的 tier + trigger + keep 设置，让运维验证 tier 敏感 summarization 策略（small=50% / medium=65% / large=80% of `context_budget`）实际生效。 |
| `OLAV_STATIC_CONTEXT_MODE` | Round 24 | 强制 static_context mode（`always` / `on_intent` / `lazy`），不论 per-agent frontmatter 声明。 |

### Tier & Resolver 开关

| 环境变量 | 引入 | 作用 |
|---|---|---|
| `OLAV_LLM_MODEL_TIER` | Sprint 0b | 强制 `small` / `medium` / `large` 层级，不改 config 即可切。驱动 `TIER_DEFAULTS` 所有 key（context_budget、recall_top_k、return_compact_chars、summarization_trigger_pct、execute_sql_context_rows、search_logs_default_limit 等）。 |
| `OLAV_BACKUP_COMMANDS_PATH` | Round 46 | `backup_only_commands.yaml` 查找的优先级 0 覆盖。指向存在的文件时覆盖三条默认候选；指向不存在的文件时 fall through 到默认（typo 不会静默关闭 loader）。 |
| `OLAV_SANDBOX_NETNS` | — | `1` 强制每次 sandbox 子进程使用 network namespace 隔离。 |
| `OLAV_MEMORY_DB_PATH` | — | 覆盖 `olav_recall_memory` 的 LanceDB 存储路径。 |

!!! tip "CI/CD 推荐用环境变量"
    在 CI/CD 流水线中，推荐通过环境变量而非文件传递密钥，避免密钥写入磁盘。

---

## 远程 AsyncSubAgent

OLAV 可将 LangGraph Cloud 或自托管 LangGraph 部署接入为 AsyncSubAgent。在 `api.json` 中添加 `async_subagents` 数组：

```json
{
    "async_subagents": [
        {
            "name": "remote-ops",
            "description": "运行在 LangGraph Cloud 上的高算力 Ops Agent",
            "url": "https://my-deployment.langsmith.com",
            "assistant_id": "ops",
            "api_key_env": "LANGGRAPH_API_KEY"
        }
    ]
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 唯一 SubAgent 名称（供 `olav_delegate` 工具路由） |
| `description` | ✅ | 供 Orchestrator 路由决策的描述 |
| `url` | ✅ | LangGraph 部署 URL |
| `assistant_id` | ✅ | 远程部署上的 Graph/Assistant ID |
| `api_key_env` | ❌ | 持有 API 密钥的环境变量名，未设置时回退到 `LANGGRAPH_API_KEY` |

远程 SubAgent 在启动时与本地 workspace SubAgent 一起加载。连接失败时优雅跳过，不阻塞本地 Agent 初始化。

---

## Hook 系统

在 `~/.olav/hooks.json` 中配置由 OLAV 会话事件触发的外部命令：

```json
{
    "hooks": [
        { "event": "session.start", "command": "notify-send 'OLAV 会话已启动'" },
        { "event": "tool.call",     "command": "logger -t olav '工具: $OLAV_HOOK_TOOL'" }
    ]
}
```

完整事件参考见 [Agent Harness → 事件 Hook 系统](../concepts/agent-harness.md#事件-hook-系统)。
