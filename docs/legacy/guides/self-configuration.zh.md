# 自配置与恢复

OLAV 的设计目标是：**改配置永远不需要手动编辑配置文件，一次错误的变更也
永远不会毁掉可用的环境**。本页覆盖对话式配置工具、回滚、撤销，以及运行
途中出故障时该怎么办。

---

## 对话式修改 LLM / 嵌入配置

向 **admin** agent 提出请求（它会把配置类请求路由给 `editor` 子代理）：

```bash
olav --agent admin "把 LLM 模型换成 deepseek-chat"
olav --agent admin "把我的 LLM api key 更新为 sk-..."
olav --agent admin "嵌入切换到 api 模式，key 是 sk-..."
```

也可以在 TUI 内先 `/workspace admin`，再用自然语言提出。

### 先验证，后落盘

每一次对话式配置变更都会**先对真实 provider 测试，通过才写入**：

- 候选配置可用 → 保存并立即生效（无需重启），且写入前自动快照旧配置。
- 候选配置有问题（key 错误、模型名不存在、endpoint 不可达）→ **带着
  provider 的真实报错被拒绝**，你正在使用的配置不受任何影响。

```
> 把我的 LLM api key 更新为 sk-wrong-key
Rejected — candidate config failed to connect: Error code: 401 —
Authentication Fails... Current config unchanged.
```

这正是推荐用对话而不是手改 `api.json` 的原因——手动编辑没有任何验证，
可能留下一个无法启动的配置。

### 回滚

每次生效的变更都会先快照旧配置。一句话撤销最近一次变更：

```bash
olav --agent admin "回滚我的 LLM 配置"
```

---

## 撤销 Agent 的写操作

除配置外，agent 的写状态操作——**工作区文件写入**和 **cron 计划任务变更**
——都会自动记录。撤销最近一次：

```bash
olav --agent admin "撤销我刚才的操作"
```

agent 可以先预览将要撤销的内容再执行。撤销是单步、最近优先的：再问一次
即撤销更早的那次操作。

| 操作 | 撤销行为 |
|------|----------|
| 覆盖了文件 | 恢复原内容 |
| 新建了文件 | 删除该文件 |
| 新增了 cron 任务 | 移除该任务 |
| 修改了 cron 调度 | 恢复原调度 |
| 删除了 cron 任务 | 按存档重建 |

!!! note "范围"
    配置类变更走专用的"回滚我的 LLM 配置"路径（带验证的快照）。服务
    部署目前不可撤销。

---

## 运行途中出故障怎么办

使用过程中的 LLM 故障（key 过期、配额耗尽、模型下线）不会只留给你一段
裸堆栈——错误后面紧跟着下一步该做什么：

```
Error: Error code: 401 - {'error': {'message': 'Authentication Fails...'}}
The LLM provider rejected the API key (invalid or expired) — run
`olav doctor` to diagnose. If this started after a config change, undo
it: olav --agent admin "rollback my LLM config"
```

### 健康体检（Doctor）

```bash
olav doctor           # 在 shell 中
olav doctor --json    # 机器可读输出
/doctor               # 在 TUI 内
```

确定性、零 LLM 的探测——即使坏掉的恰恰是 LLM 配置本身，它也能工作：

```
✓ scaffolding: .olav/ deployed
✓ llm: connected
⚠ embedding: sentence-transformers unavailable; cannot create local embeddings
    fix: check embedding.mode / embedding.api.* in .olav/config/api.json

overall: needs attention
```

每个失败项都带一行 `fix:`——目标是告诉你该**做什么**，而不只是哪里坏了。
