# Installation

This page walks you through installing and initializing OLAV in 5 minutes.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-01 | `olav version` reports version correctly | ✅ v0.18.0 |
    | C-L2-13 | `olav init` creates project directory structure | ✅ v0.18.0 |

---

## Prerequisites

| Dependency | Description |
|-----------|-------------|
| Python 3.11+ | Runtime environment for OLAV |
| LLM API Key | Supports OpenAI, Anthropic, Ollama (local), and more |

## Step 1: Install

=== "pip install (recommended)"
    ```bash
    pip install olav
    ```

=== "From source (development)"
    ```bash
    git clone https://github.com/olav-ai/olav.git
    cd olav
    uv sync
    ```
    !!! note "Development mode"
        When installed from source, prefix all `olav` commands with `uv run`, e.g. `uv run olav version`.
        All examples below use bare `olav` commands (as installed via pip).


=== "Docker"
    ```bash
    git clone https://github.com/olav-ai/olav.git
    cd olav
    cp .env.docker.example .env      # endpoint + key
    docker compose build olav
    ```

    !!! warning "`run`, not `up`"
        OLAV is an interactive terminal application, not a server. `docker
        compose up olav` starts a container that immediately exits — there is
        nothing to daemonise. Every invocation is a `run`:

        ```bash
        docker compose run --rm olav init
        docker compose run --rm olav doctor
        docker compose run --rm olav --agent core "how many devices are there?"
        ```

    State lives in one named volume mounted at `/data`: config, both databases,
    the deployed workspaces and `exports/`. `docker compose down` keeps it,
    `down -v` deletes it. Add the network domain — and Batfish, the one service
    that *is* long-running — with `docker compose --profile netops`.

    See [Running OLAV in Docker](../reference/docker.md) for the full reference.

Verify the installation:

```bash
olav version
```

Expected output:
```
OLAV v0.26.0
```

## Step 2: Run OLAV — It Sets Itself Up

Navigate to your working directory and simply run:

```bash
olav
```

On a first run OLAV detects there is nothing set up yet and bootstraps
itself — directories, databases, the core Agent, and the local embedding
model — then asks for the one thing it cannot infer:

```
First run detected — setting up OLAV...
No LLM API key configured yet.
Paste your LLM API key: ********
✓ API key saved to .olav/config/api.json
```

That's it. You're in the interactive TUI and can start asking questions.
The welcome screen reflects your **actual state** — if something needs
attention (embedding backend unavailable, no device data imported yet),
it says so with the fix, instead of showing a generic tip.

!!! tip "Health check any time"
    ```bash
    olav doctor          # from the shell
    /doctor              # from inside the TUI
    ```
    Zero-LLM probes of scaffolding, LLM connectivity, and the embedding
    backend — each failing check paired with an actionable fix.

### What `olav doctor` checks

`olav doctor` is a fast, zero-LLM preflight. It never crashes — each of its
8 checks reports a `✓` / `⚠` verdict:

| Check | What it verifies |
|---|---|
| `scaffolding` | `.olav/` directories, config, and databases are in place |
| `llm` | LLM endpoint is reachable and the configured model responds |
| `embedding` | embedding backend (local model or API) is available |
| `agents` | top-level SKILL.md files parse — N loaded / M failed |
| `subagents` | every sub-agent is declared by its parent, no orphans |
| `tools` | every `@tool` file compiles — a broken tool file breaks its agent |
| `memory` | experience layer primed? counts per category — `⚠` when 0 guides |
| `recall` | a **smoke** probe: embed one query + one search confirms the recall path is wired (NOT an accuracy benchmark — use `olav kb bench` for that) |

Sample output:

```
✓ agents: 6 loaded, 0 failed
✓ subagents: 18 wired, 0 orphaned
✓ tools: 19 registered, 0 syntax error(s)
✓ memory: primed — 34 usage_guide · 13 reflection · 10 fact
✓ recall: responsive (3 hit(s) for probe) — `olav kb bench` for accuracy
```

`olav doctor --json` emits the same checks as machine-readable JSON.

!!! note "Scripted / CI setup"
    `olav init` performs the same bootstrap non-interactively (it never
    prompts). Provide the key via the `OPENAI_API_KEY` environment
    variable or by editing `.olav/config/api.json` afterwards.

## Step 3: Configure API Key (manual / multi-provider)

The interactive prompt above covers the common single-provider case. For
multi-provider setups, custom endpoints, or scripted deployment, edit
`.olav/config/api.json` directly — or just **ask the admin agent** to do
it for you:

```bash
olav --agent admin "switch my LLM to deepseek-chat with key sk-..."
olav --agent admin "rollback my LLM config"     # undo the last change
```

Config changes made this way are **validated against the live provider
before being saved** — a bad key or model name is rejected with the real
provider error and your working config stays untouched. Every applied
change snapshots the previous config first, so a one-line rollback always
works.

!!! tip "Three deployment tiers — pick what fits"
    OLAV supports three independently switchable tiers: **Layer 1 fully local** (no cloud keys), **Layer 2 LLM-only** (minimum: no embedding/reranker), and **Layer 3 full cloud** (highest quality). The quickstart below focuses on cloud LLM; ready-to-paste configs for all three tiers are in [Configuration Reference → Deployment Tiers](../reference/configuration.md#deployment-tiers).

`api.json` can use `shared.api_key` for homogeneous deployments where one key covers everything; for multi-provider setups, set `api_key` per section (per-section beats shared):

=== "OpenAI"
    ```json
    {
      "shared": { "api_key": "sk-..." },
      "llm": { "provider": "openai", "model": "gpt-4o" },
      "embedding": { "mode": "api", "api": { "model": "openai/text-embedding-3-small" } }
    }
    ```
=== "Anthropic"
    ```json
    {
      "shared": { "api_key": "sk-ant-..." },
      "llm": { "provider": "anthropic", "model": "claude-3-5-sonnet-20241022" },
      "embedding": { "mode": "local" }
    }
    ```
    Anthropic doesn't provide an embedding API. Either point `embedding` at any
    OpenAI-compatible endpoint, or use on-CPU embedding — which needs the extra:
    `pip install 'olav[local-embed]'`.
=== "OpenRouter (multi-model)"
    ```json
    {
      "shared": { "api_key": "sk-or-..." },
      "llm": { "provider": "custom", "model": "openai/gpt-4o", "base_url": "https://openrouter.ai/api/v1" },
      "embedding": { "mode": "api", "api": { "model": "openai/text-embedding-3-small", "base_url": "https://openrouter.ai/api/v1" } }
    }
    ```
=== "Ollama (local/offline)"
    ```json
    {
      "llm": { "provider": "ollama", "model": "llama3.1", "base_url": "http://localhost:11434" },
      "embedding": { "mode": "api", "api": { "model": "embeddinggemma", "base_url": "http://localhost:11434/v1", "api_key": "ollama" } }
    }
    ```
    Fully offline, no API key needed — Ollama serves the embedding model too, so
    this needs no extra beyond the base install. (For on-CPU embedding with no
    server at all, see the admonition below.)

!!! info "On-CPU embedding is an optional extra"
    `embedding.mode` defaults to **`api`**: OLAV embeds by calling an
    OpenAI-compatible endpoint. Any local server works (Ollama, llama.cpp,
    vLLM), so "API mode" does not mean "cloud".

    `mode: local` embeds on the CPU in-process with no server, via
    `sentence-transformers`. That package is **not** in the default install —
    it is the only thing pulling `torch` (plus the NVIDIA CUDA runtime on
    linux-x86_64, ~4.6 GB measured). Install it deliberately:

    ```bash
    pip install 'olav[local-embed]'
    ```

    With `mode: local` and the extra missing, embedding is unavailable and
    memory / recall / semantic routing stay off. `olav doctor` reports this
    explicitly, and the first-run wizard refuses to select the mode.

!!! tip "Embedding model & language coverage"
    A common on-CPU starting point is **`BAAI/bge-small-zh-v1.5`** — 512-dim,
    ~90 MB, the smallest first-run footprint. It is Chinese-optimized but
    handles English too.

    For **English-heavy or international deployments** where retrieval quality
    on English configs/docs matters, switch to a multilingual or English
    model — no file editing needed, just ask the admin agent (it validates
    the new model against the live backend **before** saving, and one
    sentence rolls it back):

    ```bash
    # Multilingual (balanced zh+en), or a local Ollama embedding model:
    olav --agent admin "switch embedding to a multilingual model"
    olav --agent admin "switch embedding to api mode using the local Ollama \
      model embeddinggemma at http://localhost:11434/v1 (api key 'ollama')"
    olav --agent admin "rollback my embedding config"   # undo
    ```

    !!! warning "Changing the model changes the vector dimension"
        Different models emit different dimensions (bge-small-zh = 512,
        bge-base-en = 768, bge-m3 = 1024). On a **fresh** install this is
        harmless. On an install with an **existing memory table**, OLAV
        fails fast with `EmbeddingDimMismatchError` to protect your data —
        re-embed / reset the memory store when you deliberately switch.

!!! warning "Protect your keys"
    `api.json` contains API keys. It is already in `.gitignore` (created by `olav init`).

!!! tip "Environment variables"
    Override `api.json` values via environment variables (higher priority):
    ```bash
    export OLAV_LLM_API_KEY="sk-..."       # overrides shared.api_key
    export OLAV_LLM_MODEL="gpt-4o"         # overrides llm.model
    export OPENAI_API_KEY="sk-..."          # OpenAI shorthand
    export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic shorthand
    ```

The resulting directory structure:

```
.olav/
├── config/
│   ├── api.json        ← LLM + auth config (contains keys — do not commit)
│   ├── services.yaml   ← registered external services
│   └── settings.json   ← platform settings (active Agent, etc.)
├── databases/
│   ├── audit.duckdb    ← audit log (auto-records all operations)
│   └── domain.duckdb   ← domain data (Agent execution results, etc.)
└── workspace/
    └── core/           ← pre-deployed core Agent
        ├── AGENT.md    ← Agent capability definition
        └── MANIFEST.yaml ← route keywords and version info
```

## Step 4: Install Domain Skills (optional)

OLAV core provides database queries, API integration, remote execution, and platform management. For domain-specific capabilities, install skill packages:

=== "Network Operations"
    ```bash
    pip install olav-netops
    olav agent install olav-netops
    ```
    `pip install` pulls the package from PyPI; `olav agent install` then resolves the installed package, deploys its workspace, and registers the agent (it also accepts a local path or git URL for development: `olav agent install /path/to/olav-netops/`).
    Adds: SSH collection (Nornir), topology analysis, drift detection, ContainerLab, network-aware audit health checks.
    Installs the `netops` agent and makes `audit` network-aware. Verify with `olav list` (6 agents: admin, audit, core, devops, netops, services).

=== "Enterprise Features"
    ```bash
    pip install olav-ent
    ```
    Adds: the **web service + browser UI + HTTP API** (`olav service web`, port 2280), the **CLI-accelerating daemon** (`olav service daemon`), **multi-user token / LDAP authentication** (`olav login`, `olav admin add-user`), plus SFT/trajectory dataset export and encrypted datasets. In open-source OLAV, `olav service` provides the syslog receiver only and identity defaults to `auth.mode=none` (OS user = admin).

=== "Optional Dependencies"
    ```bash
    pip install olav[pdf]       # PDF document import for knowledge base
    pip install olav[netops]    # Network analysis libs without full olav-netops
    pip install olav[audit]     # ML anomaly detection (scikit-learn, scipy)
    ```

!!! tip "Verify agents"
    After install, check available agents:
    ```bash
    olav list
    # Base install (5): admin, audit, core, devops, services
    # + olav-netops adds: netops   (and makes audit network-aware)
    ```

## Step 5: Set Up .gitignore

The workspace is safe to commit — share Agent definitions with your team. Config and databases should not be committed:

```gitignore
# .gitignore
.olav/config/      # contains API keys
.olav/databases/   # contains audit logs and domain data
.olav/run/         # runtime PID files
```

```bash
git add .olav/workspace/
git commit -m "feat: init olav workspace"
```

---

**Next:** [Your First Query →](first-query.md)
