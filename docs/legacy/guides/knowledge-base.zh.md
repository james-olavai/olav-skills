# 知识库

OLAV 内置了语义搜索知识库——你可以把团队的运维文档、Runbook、故障处理手册等导入 OLAV，然后用自然语言搜索。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-21 | 知识库索引文档并支持语义搜索（向量 + BM25 混合） | ✅ v0.10.0 |

---

## 适用场景

- "BGP 故障切换的标准流程是什么？"
- "灾难恢复的步骤在哪里？"
- "上次处理这个问题的方案是什么？"

不用翻 Confluence、找 Wiki、搜邮件——直接问 OLAV。

---

## 导入文档

支持 `.md`（Markdown）、`.pdf`、`.txt` 格式。将文件放入 `.olav/knowledge/` 目录，然后索引：

```bash
olav kb sync
```

查看索引状态：

```bash
olav kb status
```

---

## 搜索知识库

直接用自然语言提问：

```bash
olav "我们的 Runbook 里关于 BGP 故障切换是怎么说的？"
olav kb search 'DR 流程'
```

OLAV 会返回最相关的文档片段，并结合 LLM 生成综合回答。

---

## 召回基准测试 —— `olav kb bench`

`olav kb bench` 是一个**领域无关的自召回基准**：它用每条被采样记忆**自己**的文本去查询，检查该条目是否出现在 top-K 里。健康的 embedder + 索引会把条目排在第 1 位（距离 ~0）；较低的 self-recall@1 意味着 embedder 退化或维度 / 索引出了问题。

```bash
olav kb bench                 # 采样 40 条记忆，top-5
olav kb bench --sample 100 --top-k 3
olav kb bench --json
```

输出示例：

```
KB self-recall bench (n=40, top-5):
  self-recall@1=98%  @3=98%  @5=98%
  by category:
    usage_guide  n=22  @1=96%  @5=96%
    reflection   n=5   @1=100%  @5=100%
```

!!! note "无需 golden set"
    与 paraphrase→intent 命中率探针（一个需要固定 golden set 的集成测试）不同，`kb bench` 不需要 golden 数据，因此在任何安装上都能跑。它还能暴露**近重复记忆**：两条近乎相同的行会在 rank-1 互相顶掉，拉低 @1 —— 这是存储里有冗余条目的有用信号。

---

## 工作原理

知识库使用向量语义搜索，结合 BM25 关键词搜索（RRF 融合排序），既能理解语义相似度，又不会遗漏关键词匹配：

```
文档 → 分块（~1024 字符）→ 生成向量嵌入 → 存入 LanceDB
查询 → 向量相似度搜索 + BM25 关键词搜索 → 融合排序 → 返回 Top-N 片段 → LLM 综合回答
```

---

## 配置向量嵌入

在 `.olav/config/api.json` 中配置嵌入模型：

=== "API 模式（推荐）"
    ```json
    "embedding": {
      "mode": "api",
      "api": {
        "model": "openai/text-embedding-3-small",
        "base_url": "https://openrouter.ai/api/v1"
      }
    }
    ```

=== "本地模式（离线/气隙环境）"
    ```json
    "embedding": {
      "mode": "local"
    }
    ```
    使用 BAAI/bge 模型，仅需 CPU，无需网络连接。适合安全隔离环境。

!!! note "知识库 vs Agent 记忆"
    文档和 agent 记忆共享同一张 UKS `memory` 表。文档通过 `olav kb sync/import` 显式导入；agent 记忆由 L1 捕获中间件（operational_event）和 L2 模式抽取（蒸馏后的 expert_knowledge）在运行中自动写入。团队约定可通过 `olav kb import-guides` 注入。两者都支持 `olav_recall_memory` 语义搜索。详见 [自我改进循环](../concepts/self-improving-loop.md) 与 [扩展记忆](extending-memory.md)。

## 来源分层（v0.21.1+）

每条 `*.guide.yaml` 都带一个 **`source_tier`** 字段，决定 AutoRecall
给它多少信任度。tier 通过一张固定表映射到优先级 + 检索权重 —— 不再
使用容易跨部署漂移的手设整数。

| Tier | 优先级 | 检索权重 | 适用场景 |
|---|---|---|---|
| `vendor` | 10 | 2.0 | 厂商/上游文档（Cisco BGP best practices、Junos 配置参考） |
| `platform` | 8 | 1.67 | OLAV 出厂带的架构性 invariant（schema-aware view guide 等） |
| `team` | 6 | 1.33 | 组织级策略（NetBox 命名规范、AS 分配、tenant 默认值） |
| `user` | 3 | 0.83 | 个人偏好或临时规则（R102 对话注入落在这层） |

权重 1.67 的 platform guide 与权重 0.83 的 user-tier 规则同时命中
某条查询时，platform 排在前面 —— 操作员临时口述偏好不会意外覆盖
有文档基础的架构性 invariant。

### 撰写一条分层 guide

```yaml
# .olav/workspace/services/guides/netbox_naming.guide.yaml
schema_version: 2
intent: netbox_naming_convention
agent: services
source_tier: team           # schema v2 必填
keywords: [netbox, naming, hostname, convention]
body: |
  设备 hostname 遵循 `<site>-<role>-<n>` 命名。status 默认 active。
  生产 tenant 永远 `acme-network-ops`。
```

`olav kb import-guides .olav/workspace` 会把它 prime 进去，行权重
`weight=1.33`（team tier）。

### 对话式注入落在 `user` tier

R102 `memory_curator`（"记住 — X" 路径）写入 `source_tier: user`，
不可更改。要把 user-tier 规则升级到 team-tier，请把 YAML 复制出来、
改 `source_tier: team`、走正常 git PR review 合入主线 —— 平台有意
不允许 LLM 生成的 commit 自行跨越信任边界。

## 删除一条 guide

`olav kb remove` 是经过审计的删除路径：

```bash
olav kb remove netbox_sync_defaults --reason "Q2 把 tenant 改为 acme-corp"
```

命令会：

1. 把源 YAML 重命名为 `<intent>.guide.yaml.removed`（**tombstone**）。
   `prime_guides_from_dir` 会跳过被 tombstone 的文件，重 prime 不会
   死灰复燃。
2. 按 id（`guide_<agent>_<intent>`）删 LanceDB 行。
3. 落一行 `kb_audit/<iso-ts>_remove_<intent>.yaml`，含
   `action / intent / agent / actor / timestamp / body_sha256 / reason`。

`--reason` 必填（脚本场景可设 `OLAV_KB_REMOVE_ALLOW_NO_REASON=1`
跳过）。`--keep-yaml` 只删 LanceDB 行，YAML 留在盘上 —— 用于编辑
后重 prime 驱赶旧缓存。

## kb_audit/ —— git 可追踪的变更链 {#kb-audit-changelog}

每次 KB 写操作（R102 commit、`kb remove`、NetBox CSV push）都会向
`<workspace>/kb_audit/` 落一行带时间戳的 YAML。该目录在
`ownership_manifest.yaml` 里有显式 git-track 例外，所以变更记录独立
于运行时 DB 存活。

```yaml
# kb_audit/2026-05-15T14-29-35_remove_netbox_sync_defaults.yaml
action: remove
intent: netbox_sync_defaults
agent: services
actor: yhvh
timestamp: '2026-05-15T14:29:35+10:00'
body_sha256: 1b3ed3914eb58bb036b13c8d9bbb1edd6002bc1f3882e69dcabdfeadf81f44a6
reason: moved to git PR flow
source_tier: user
```

与 `audit.duckdb` `audit_tool_calls`（记录每次工具调用）互补 ——
`kb_audit/` 行是**语义意图**记录，不是执行 trace，并且能扛过
`olav reset` / DB 重建。
