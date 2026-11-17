# 安装

本页将引导你在 5 分钟内完成 OLAV 的安装和初始化。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-01 | `olav version` 正确报告版本号 | ✅ v0.18.0 |
    | C-L2-13 | `olav init` 创建项目目录结构 | ✅ v0.18.0 |

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | OLAV 的运行环境 |
| LLM API Key | 支持 OpenAI、Anthropic、Ollama（本地）等多种提供商 |

## 第一步：安装

=== "pip install（推荐）"
    ```bash
    pip install olav
    ```

=== "从源码安装（开发）"
    ```bash
    git clone https://github.com/olav-ai/olav.git
    cd olav
    uv sync
    ```
    !!! note "开发模式"
        源码安装后，所有 `olav` 命令需加 `uv run` 前缀，如 `uv run olav version`。
        以下文档示例均使用 `pip install` 后的裸 `olav` 命令。


=== "Docker"
    ```bash
    git clone https://github.com/olav-ai/olav.git
    cd olav
    cp .env.docker.example .env      # 端点与密钥
    docker compose build olav
    ```

    !!! warning "用 `run`，不是 `up`"
        OLAV 是交互式终端应用，不是服务。`docker compose up olav` 启动的容器会
        立刻退出——它没有需要常驻的东西。每次调用都是 `run`：

        ```bash
        docker compose run --rm olav init
        docker compose run --rm olav doctor
        docker compose run --rm olav --agent core "有多少台设备？"
        ```

    状态集中在挂载到 `/data` 的一个命名卷里：配置、两个数据库、已部署的
    工作区，以及 `exports/`。`docker compose down` 会保留它，`down -v` 会删除。
    用 `docker compose --profile netops` 追加网络域，以及 Batfish——那是这里
    唯一真正常驻的服务。

    完整参考见 [在 Docker 中运行 OLAV](../reference/docker.md)。

验证安装是否成功：

```bash
olav version
```

看到类似输出即表示安装成功：
```
OLAV v0.26.0
```

## 第二步：直接运行 —— OLAV 自己完成初始化

进入你的工作目录，直接运行：

```bash
olav
```

首次运行时 OLAV 会检测到尚未初始化，自动完成全部准备工作——目录、数据库、
核心 Agent、本地嵌入模型——然后只询问一件它无法推断的事：

```
First run detected — setting up OLAV...
No LLM API key configured yet.
Paste your LLM API key: ********
✓ API key saved to .olav/config/api.json
```

到此为止，你已经进入交互式 TUI，可以直接提问。欢迎页展示的是你的**真实
状态**——如果有需要注意的事项（嵌入后端不可用、还没有导入设备数据），
它会直接说明并给出解决办法，而不是显示一条随机提示语。

!!! tip "随时健康体检"
    ```bash
    olav doctor          # 在 shell 中
    /doctor              # 在 TUI 内
    ```
    零 LLM 的确定性探测：脚手架、LLM 连通性、嵌入后端——每个失败项
    都附带可执行的修复动作。

### `olav doctor` 检查什么

`olav doctor` 是一次快速、零 LLM 的预检。它从不崩溃 —— 8 项检查每项
给出一个 `✓` / `⚠` 结论：

| 检查项 | 验证内容 |
|---|---|
| `scaffolding` | `.olav/` 目录、配置、数据库是否就位 |
| `llm` | LLM 端点可达、配置的模型有响应 |
| `embedding` | 嵌入后端（本地模型或 API）是否可用 |
| `agents` | 顶层 SKILL.md 是否解析成功 —— N 已加载 / M 失败 |
| `subagents` | 每个子代理都被其父级声明、无孤儿目录 |
| `tools` | 每个 `@tool` 文件能否编译 —— 一个坏工具文件会拖垮它所属的 agent |
| `memory` | 经验层是否已 prime？各类别计数 —— 0 条 guide 时 `⚠` |
| `recall` | 一次**冒烟**探测：嵌入一条查询 + 一次检索确认召回链路已接通（不是精度基准 —— 精度用 `olav kb bench`） |

输出示例：

```
✓ agents: 6 loaded, 0 failed
✓ subagents: 18 wired, 0 orphaned
✓ tools: 19 registered, 0 syntax error(s)
✓ memory: primed — 34 usage_guide · 13 reflection · 10 fact
✓ recall: responsive (3 hit(s) for probe) — `olav kb bench` for accuracy
```

`olav doctor --json` 以机器可读的 JSON 输出同样这些检查。

!!! note "脚本化 / CI 场景"
    `olav init` 保留等价的非交互式初始化（永不弹出提问）。key 通过
    `OPENAI_API_KEY` 环境变量提供，或事后编辑 `.olav/config/api.json`。

## 第三步：配置 API Key（手动 / 多 provider）

上面的交互式提问覆盖了最常见的单 provider 场景。多 provider、自定义
endpoint 或脚本化部署时，可直接编辑 `.olav/config/api.json`——或者
**直接让 admin agent 帮你改**：

```bash
olav --agent admin "把 LLM 换成 deepseek-chat，key 是 sk-..."
olav --agent admin "回滚我的 LLM 配置"     # 撤销上一次变更
```

通过对话修改的配置会**先对真实 provider 验证再落盘**——坏 key 或错误的
模型名会带着 provider 的真实报错被拒绝，你正在使用的配置不受影响。每次
生效的变更都会先快照旧配置，因此一句话回滚永远可用。

!!! tip "三种部署模式 — 按你的环境选"
    OLAV 设计了三档独立可切的部署：**Layer 1 全本地**（无 cloud key）、**Layer 2 LLM-only**（极简，无 embed/rerank）、**Layer 3 全 cloud**（最高质量）。下面的 quickstart 示例侧重 cloud LLM；完整三档 ready-to-paste 配置见 [配置参考 → 部署模式](../reference/configuration.md#deployment-tiers)。

`api.json` 中可以用 `shared.api_key` 统一管理密钥（homogeneous 部署）；多 provider 部署则在每个 section 的 `api_key` 单独配置（per-section 优先于 shared）：

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
    Anthropic 不提供 embedding API。可以把 `embedding` 指向任意 OpenAI 兼容端点，
    或使用 on-CPU embedding——后者需要额外安装：`pip install 'olav[local-embed]'`。
=== "OpenRouter（多模型聚合）"
    ```json
    {
      "shared": { "api_key": "sk-or-..." },
      "llm": { "provider": "custom", "model": "openai/gpt-4o", "base_url": "https://openrouter.ai/api/v1" },
      "embedding": { "mode": "api", "api": { "model": "openai/text-embedding-3-small", "base_url": "https://openrouter.ai/api/v1" } }
    }
    ```
=== "Ollama（本地离线）"
    ```json
    {
      "llm": { "provider": "ollama", "model": "llama3.1", "base_url": "http://localhost:11434" },
      "embedding": { "mode": "api", "api": { "model": "embeddinggemma", "base_url": "http://localhost:11434/v1", "api_key": "ollama" } }
    }
    ```
    完全离线，无需 API Key——Ollama 同时提供 embedding 模型，所以除基础安装外
    无需任何 extra。（完全不要服务端的 on-CPU 方案见下面的说明。）

!!! info "on-CPU embedding 是可选 extra"
    `embedding.mode` 默认是 **`api`**：OLAV 通过调用 OpenAI 兼容端点来做
    embedding。任何本地服务都算（Ollama、llama.cpp、vLLM），所以「api 模式」
    并不等于「上云」。

    `mode: local` 是进程内 on-CPU embedding、不需要服务端，依赖
    `sentence-transformers`。该包**不在**默认安装里——它是唯一会拉进 `torch`
    的依赖（在 linux-x86_64 上还会带上 NVIDIA CUDA 运行时，实测约 4.6 GB）。
    需要时显式安装：

    ```bash
    pip install 'olav[local-embed]'
    ```

    若设了 `mode: local` 而未安装该 extra，embedding 不可用，记忆 / 召回 /
    语义路由都处于关闭状态。`olav doctor` 会明确报出这一项，首次运行向导也会
    拒绝选中该模式。

!!! tip "embedding 模型与语言覆盖"
    on-CPU 常用的起点是 **`BAAI/bge-small-zh-v1.5`**——512 维、~90MB，首次启动
    体积最小。它中文优化、但英文也能处理。

    对于**英文为主或国际部署**、英文配置/文档的检索质量很重要的场景，切换到
    多语言或英文模型——无需编辑文件，直接让 admin agent 处理（它会在保存前
    对新模型做实时校验，且一句话即可回滚）：

    ```bash
    # 多语言（中英均衡），或指向本地 Ollama embedding 模型：
    olav --agent admin "把 embedding 切换到多语言模型"
    olav --agent admin "把 embedding 切到 api 模式，用本地 Ollama 的 \
      embeddinggemma，地址 http://localhost:11434/v1（api key 填 'ollama'）"
    olav --agent admin "回滚我的 embedding 配置"   # 撤销
    ```

    !!! warning "换模型会改变向量维度"
        不同模型输出不同维度（bge-small-zh = 512、bge-base-en = 768、
        bge-m3 = 1024）。**全新安装**无影响；**已有记忆表**的安装上，OLAV
        会以 `EmbeddingDimMismatchError` fail-fast 保护数据——刻意切换时
        需重新嵌入 / 重置记忆库。

!!! warning "保护你的密钥"
    `api.json` 包含 API 密钥，**务必**加入 `.gitignore`（`olav init` 已自动处理）。

!!! tip "环境变量方式"
    不想在文件中存储密钥时，可通过环境变量覆盖（优先级高于 `api.json`）：
    ```bash
    export OLAV_LLM_API_KEY="sk-..."       # 覆盖 shared.api_key
    export OLAV_LLM_MODEL="gpt-4o"         # 覆盖 llm.model
    export OPENAI_API_KEY="sk-..."          # OpenAI 快捷方式
    export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic 快捷方式
    ```

创建的目录结构如下：

```
.olav/
├── config/
│   ├── api.json        ← LLM 和认证配置（包含密钥，不要提交到 git）
│   ├── services.yaml   ← 已注册的外部服务
│   └── settings.json   ← 平台设置（当前活跃 Agent 等）
├── databases/
│   ├── audit.duckdb    ← 审计日志（自动记录所有操作）
│   └── domain.duckdb   ← 业务数据（Agent 执行结果等）
└── workspace/
    └── core/           ← 预装的核心 Agent
        ├── AGENT.md    ← Agent 的能力定义
        └── MANIFEST.yaml ← 路由关键词和版本信息
```

## 第四步：安装领域技能（可选）

OLAV 核心提供数据库查询、API 集成、远程执行和平台管理。安装领域技能包可扩展专业能力：

=== "网络运维"
    ```bash
    pip install olav-netops
    olav agent install olav-netops
    ```
    `pip install` 从 PyPI 拉取包；`olav agent install` 随后解析已安装的包、部署其 workspace 并注册 agent（开发时也可传本地路径或 git URL：`olav agent install /path/to/olav-netops/`）。
    新增：SSH 采集（Nornir）、拓扑分析、漂移检测、ContainerLab 数字孪生、网络感知的审计健康检查。
    安装 `netops` agent，并让 `audit` 具备网络感知能力。用 `olav list` 验证（6 个 agent：admin、audit、core、devops、netops、services）。

=== "企业功能"
    ```bash
    pip install olav-ent
    ```
    新增：**Web 服务 + 浏览器界面 + HTTP API**（`olav service web`，端口 2280）、**加速 CLI 的 daemon**（`olav service daemon`）、**多用户 token / LDAP 认证**（`olav login`、`olav admin add-user`），以及 SFT/trajectory 数据集导出、加密数据集。开源版 OLAV 的 `olav service` 仅提供 syslog 接收器，身份默认为 `auth.mode=none`（OS 用户即 admin）。

=== "可选依赖"
    ```bash
    pip install olav[pdf]       # PDF 文档导入（知识库）
    pip install olav[netops]    # 网络分析库（不含完整 olav-netops）
    pip install olav[audit]     # ML 异常检测（scikit-learn, scipy）
    ```

!!! tip "验证 agent"
    安装后，检查可用 agent：
    ```bash
    olav list
    # 基础安装（5 个）：admin、audit、core、devops、services
    # olav-netops 追加：netops   （并使 audit 具备网络感知能力）
    ```

## 第五步：设置 .gitignore

工作空间（workspace）可以安全提交到 git，与团队共享 Agent 定义。但配置和数据库不应提交：

```gitignore
# .gitignore
.olav/config/      # 包含 API 密钥
.olav/databases/   # 包含审计日志和业务数据
.olav/run/         # 运行时 PID 文件
```

```bash
git add .olav/workspace/
git commit -m "feat: init olav workspace"
```

---

**下一步：** [运行你的第一个查询 →](first-query.md)
