# Configuration Reference

All configuration is centralized in the `.olav/config/api.json` file. Running `olav init` creates a baseline version.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-38 | `agent_overrides` assigns different LLM models to different Agents | ✅ v0.10.0 |
    | C-L2-39 | `OLAV_LLM_*` environment variables override config file | ✅ v0.10.0 |

!!! warning "Protect your config file"
    `api.json` contains API keys — do not commit it to Git. Add `.olav/config/` to your `.gitignore`.

---

## File Structure

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

## Deployment Tiers { #deployment-tiers }

The LLM, embedding, and reranker endpoints are independently switchable. The three tiers below are all contractually supported — pick whichever fits your network / compliance / hardware constraints.

=== "Layer 1 — Fully local & offline (recommended for production)"
    On-CPU SentenceTransformer for embedding, reranker disabled. Zero cloud keys, nothing leaves the host. The embedding model auto-downloads from HuggingFace on first run (~440 MB).

    Requires the on-CPU extra — `sentence-transformers` is not in the default install:

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

    Memory is fully usable: semantic search + BM25/FTS. Lighter alternative: swap `bge-base-en-v1.5` → `bge-small-en-v1.5` (130 MB, 384-dim).

=== "Layer 2 — LLM-only minimum (CI / air-gapped / embedded)"
    No embedding, no reranker. Memory degrades to BM25 keyword search; semantic recall is gone, but **no crashes**. Good for test containers, isolated networks, or strictly-LLM use cases.

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

=== "Layer 3 — Full cloud (highest quality)"
    Cloud LLM + cloud embedding + cloud reranker. Each endpoint carries its own API key; per-section precedence keeps them from cross-pollinating.

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

Mix-and-match is supported: "Cloud LLM + local embedding + no reranker" is a common cost-effective combo.

### Switching Between Tiers

* **Edits to `api.json` take effect on next launch** — restart the `olav` process or open a new session, no rebuild needed.
* **Changing embedding dimension triggers a LanceDB rebuild** — different models have different dims (`bge-base=768` / `pplx-embed=1024` / `text-embedding-3-small=1536`). LanceDB locks the dim per table; mismatches raise `EmbeddingDimMismatchError`. Two ways to fix:
    * `olav agent install <wheel>` for any skill — enters the `allow_dim_swap=True` path, rebuilds the table, and re-primes guides. **User-authored memories are dropped**, but system-level guides / expert KB rebuild automatically.
    * Manual backup + rebuild: `mv .olav/databases/memory.lance{,.backup}` then run any command that re-primes (e.g. `/netops_init`).
* **`shared.api_key` should usually stay empty.** Only set it when LLM + embedding + reranker all share the same provider and same key (rare). For multi-provider deployments, leave it blank and use per-section keys.

---

## Shared Configuration

The `shared` section holds cross-endpoint defaults.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `api_key` | | `""` | Cross-endpoint API key. Use only when LLM + embed + rerank all sit on the same provider; leave empty for multi-provider deployments |
| `timeout` | | `120` | Default request timeout in seconds |

### API key precedence (Patch B / 2026-05)

Each endpoint resolves its api_key independently:

1. **per-section** — `llm.api_key` / `embedding.api.api_key` / `reranker.api_key` (preferred)
2. **shared** — `shared.api_key` (fallback for homogeneous deployments)
3. **environment** — `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (last resort)

Per-section beating shared means heterogeneous setups don't get cross-contaminated: you can run `llm.api_key=sk-or-v1-...` alongside `embedding.api.api_key=pplx-...` and each endpoint picks up its own key.

---

## LLM Configuration

OLAV supports multiple LLM providers. Select the one you use:

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
    Anthropic models automatically enable Prompt Caching to reduce the cost of repeated requests.

=== "OpenRouter (Multi-Model Aggregator)"
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
    OpenRouter gives you access to virtually all mainstream models.

=== "Ollama (Local / Offline)"
    ```json
    {
      "llm": {
        "provider": "ollama",
        "model": "llama3.1",
        "base_url": "http://localhost:11434"
      }
    }
    ```
    No API key required — data never leaves your machine. `shared.api_key` can be omitted.

### LLM Field Reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `provider` | ✅ | | Provider: `openai` / `anthropic` / `azure_openai` / `ollama` / `groq` / `mistral` / `custom` |
| `model` | ✅ | | Model ID (e.g., `gpt-4o`, `claude-3-5-sonnet-20241022`) |
| `api_key` | | | Per-provider override (usually use `shared.api_key` instead) |
| `base_url` | | | Required for `custom` provider; optional for others (use for self-hosted proxies) |
| `temperature` | | `0.1` | Generation temperature — lower values produce more deterministic output |
| `max_tokens` | | `32000` | Maximum number of tokens to generate |

---

## Embedding Configuration

The knowledge base and agent memory use vector embeddings for semantic search. Three modes are supported:

=== "API Mode (cloud / self-hosted)"
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
    When `fallback` is enabled, the system falls back to a local model if the API is unreachable (requires the `local` block to be configured too).

=== "Local Mode (offline / air-gapped)"
    ```json
    "embedding": {
      "mode": "local",
      "local": {
        "model": "BAAI/bge-base-en-v1.5",
        "device": "cpu"
      }
    }
    ```
    Uses SentenceTransformer in-process — CPU/GPU only, no server, no cloud
    dependency. **Needs `pip install 'olav[local-embed]'`**: that package is the
    only thing pulling `torch` into the install (plus the NVIDIA CUDA runtime on
    linux-x86_64, ~4.6 GB measured), so it ships as an extra rather than a
    default dependency. Without it, `mode: local` leaves embedding unavailable —
    `olav doctor` reports it under the `embedding` check. Common models:

    | Model | dim | Size | Notes |
    |---|---|---|---|
    | `BAAI/bge-base-en-v1.5` | 768 | 440 MB | High quality, CPU-friendly |
    | `BAAI/bge-small-en-v1.5` | 384 | 130 MB | Ultra-light |
    | `BAAI/bge-small-zh-v1.5` | 512 | 90 MB | Chinese |
    | `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 470 MB | 50+ languages |

=== "Disabled (LLM-only minimum)"
    ```json
    "embedding": { "mode": "none" }
    ```
    No embedder is loaded. Memory writes use a zero-vector placeholder; reads fall back to BM25/FTS keyword search. **No crashes**, but semantic recall is gone. Suits CI test containers, isolated devices, or pure-LLM use cases that don't need semantic memory.

---

## Reranker Configuration

The reranker adds a cross-encoder re-ranking pass after memory recall to improve top-K relevance. **Disabled by default** — quality is highly model-dependent (some local rerankers actually make results worse).

=== "Disabled (default / recommended)"
    ```json
    "reranker": { "enabled": false }
    ```

=== "Local llama.cpp"
    ```json
    "reranker": {
      "enabled": true,
      "kind": "llama_cpp",
      "base_url": "http://localhost:11433"
    }
    ```
    Requires `llama-server` started with `--reranking` flag, loading a BERT-class cross-encoder (e.g. bge-reranker-v2-m3).

=== "Cloud (OpenRouter / Cohere)"
    ```json
    "reranker": {
      "enabled": true,
      "kind": "openrouter",
      "model": "cohere/rerank-4-fast",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key": "sk-or-v1-..."
    }
    ```
    `kind` accepts `openrouter` / `cohere` / `openai_compat` / `openai`, all routed to `POST {base_url}/rerank` with body `{query, documents, model}`. `api_key` falls back to `shared.api_key` if absent.

### Reranker Fields

| Field | Required | Description |
|---|---|---|
| `enabled` | ✅ | `true` / `false` |
| `kind` | when enabled | `llama_cpp` / `openrouter` / `cohere` / `openai_compat` / `openai` / `ollama` |
| `base_url` | when enabled | Endpoint root path (do NOT append `/rerank`) |
| `model` | cloud kinds | Model ID |
| `api_key` | cloud kinds (or shared fallback) | Bearer token |

`OLAV_RERANKER_DISABLE=1` environment variable disables the reranker without editing the file.

---

## Authentication Mode

Configured in the `auth` section to determine how user identity is established:

| Mode | Use Case | Description |
|------|----------|-------------|
| `none` | Personal use, local development | **Default (open-source)** — identity is the OS user, who has full admin rights (no RBAC friction) |
| `token` | Small teams | **Enterprise (olav-ent)** — built-in OLAV token authentication |
| `ldap` | Enterprise | **Enterprise (olav-ent)** — connects to an LDAP directory |
| `ad` | — | Not implemented — **fails fast** with an error (no silent downgrade) |
| `oidc` | — | Not implemented — **fails fast** with an error (no silent downgrade) |

!!! info "Enterprise authentication providers"
    The `token` and `ldap` authentication providers ship in the enterprise extension `olav-ent` (`pip install olav-ent`). Open-source OLAV runs with `auth.mode=none`. The RBAC engine (roles + permission matrix) is present in open-source, but the multi-user auth providers that populate it are enterprise. `ad` and `oidc` are not implemented and raise an error rather than silently downgrading.

---

## Assigning Different Models to Different Agents

You can use different LLM models for specific Agents — for example, a cheap small model for quick queries and a powerful large model for complex analysis:

```json
"agent_overrides": {
  "core": { "model": "gpt-4o-mini" },
  "audit": { "provider": "anthropic", "model": "claude-3-5-sonnet-20241022" }
}
```

Agents not listed in `agent_overrides` use the top-level `llm` configuration.

---

## Environment Variables

All configuration can be overridden via environment variables, which take precedence over `api.json`:

| Environment Variable | Config Equivalent | Description |
|---------------------|-------------------|-------------|
| `OLAV_LLM_MODEL` | `llm.model` | Model ID |
| `OLAV_LLM_API_KEY` | `shared.api_key` | API key |
| `OLAV_LLM_BASE_URL` | `llm.base_url` | API endpoint |
| `OPENAI_API_KEY` | `shared.api_key` | OpenAI shortcut |
| `ANTHROPIC_API_KEY` | `shared.api_key` | Anthropic shortcut |
| `OLAV_WEB_PORT` | | Web service port (Round 19 added env override) |
| `OLAV_WEB_HOST` | | Web service bind address |

### Context / prompt-budget debug env vars

Post-R36/R42 the platform exposes two operator-level observability
switches. They're off by default — set to `1` / `true` / `yes` / `on`
to enable.

| Environment Variable | Since | Purpose |
|---|---|---|
| `OLAV_DEBUG_CONTEXT` | Round 36 | When enabled, `agent._inject_static_context` emits a multi-line INFO log per agent listing each injected reference's bytes / estimated tokens and the total vs the active tier's `context_budget`. Useful for "why is this agent starting at 6K tokens?" diagnostics. |
| `OLAV_DEBUG_SUMMARIZATION` | Round 42 | When enabled, `build_summarization_middleware` logs the resolved tier + trigger + keep settings so operators can verify the tier-aware summarization policy (small=50% / medium=65% / large=80% of `context_budget`) took effect. |
| `OLAV_STATIC_CONTEXT_MODE` | Round 24 | Force a static_context mode regardless of per-agent frontmatter: `always` / `on_intent` / `lazy`. |

### Tier + resolver switches

| Environment Variable | Since | Purpose |
|---|---|---|
| `OLAV_LLM_MODEL_TIER` | Sprint 0b | Force `small` / `medium` / `large` tier without editing config. Drives every `TIER_DEFAULTS` key (context_budget, recall_top_k, return_compact_chars, summarization_trigger_pct, execute_sql_context_rows, search_logs_default_limit, …). |
| `OLAV_BACKUP_COMMANDS_PATH` | Round 46 | Priority-0 override for the `backup_only_commands.yaml` lookup. When set to an existing file, it wins over the three default candidates. Missing-file values fall through to defaults (typo-safe). |
| `OLAV_SANDBOX_NETNS` | — | `1` forces network-namespace isolation for every sandbox subprocess. |
| `OLAV_MEMORY_DB_PATH` | — | Override the LanceDB memory store path used by `olav_recall_memory`. |

!!! tip "Use environment variables in CI/CD"
    In CI/CD pipelines, pass secrets via environment variables rather than files to avoid writing them to disk.

---

## Remote AsyncSubAgents

OLAV can connect to remote LangGraph Cloud or self-hosted LangGraph deployments as AsyncSubAgents. Add an `async_subagents` array to `api.json`:

```json
{
    "async_subagents": [
        {
            "name": "remote-ops",
            "description": "High-capacity ops agent running on LangGraph Cloud",
            "url": "https://my-deployment.langsmith.com",
            "assistant_id": "ops",
            "api_key_env": "LANGGRAPH_API_KEY"
        }
    ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | ✅ | Unique subagent name (used by `olav_delegate` tool) |
| `description` | ✅ | Human-readable description for orchestrator routing |
| `url` | ✅ | LangGraph deployment base URL |
| `assistant_id` | ✅ | Graph/assistant ID on the remote deployment |
| `api_key_env` | ❌ | Env var name holding the API key. Falls back to `LANGGRAPH_API_KEY` |

Remote subagents are loaded at startup alongside local workspace subagents. Failures are skipped gracefully — they never block local agent initialization.

---

## Hook System

Configure external commands triggered by OLAV session events in `~/.olav/hooks.json`:

```json
{
    "hooks": [
        { "event": "session.start", "command": "notify-send 'OLAV session started'" },
        { "event": "tool.call",     "command": "logger -t olav 'Tool: $OLAV_HOOK_TOOL'" }
    ]
}
```

See [Agent Harness → Event Hook System](../concepts/agent-harness.md#event-hook-system) for the full event reference.

---

## workspace.yaml — Skill Declaration

Each skill workspace can include a `workspace.yaml` that declares metadata and optional integration features.

```yaml
name: my-skill
version: 1.0.0
description: Human-readable description of the skill
route_keywords:
  - keyword1
  - keyword2

# Optional: inject selected tools into Core Agent at install time
inject_into_core:
  tools:
    - tool_function_name   # must match a @tool function in tools.py
  description: "Short description shown in Core Agent's tool listing"
```

### Fields

| Field | Required | Description |
|-------|:--------:|-------------|
| `name` | ✅ | Skill identifier (must match workspace directory name) |
| `version` | ❌ | Semver string (shown in `olav skill list`) |
| `description` | ❌ | Human-readable summary |
| `route_keywords` | ❌ | Keywords used for automatic query routing |
| `inject_into_core` | ❌ | Tools to inject into Core Agent (see below) |

### `inject_into_core` Fields

| Field | Required | Description |
|-------|:--------:|-------------|
| `tools` | ✅ | List of tool function names to inject |
| `description` | ❌ | Label shown in Core Agent's injected-tools section |

Injected tools are added to `.olav/workspace/core/AGENT.md` at install time and removed at uninstall. See [Build a Skill → Injecting Tools into Core Agent](../guides/build-a-skill.md#injecting-tools-into-core-agent) for usage examples.
