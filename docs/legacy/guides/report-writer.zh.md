# 报告撰写器

报告撰写器是 Core Agent 的子 agent，将原始查询结果润色为结构化的、可展示的报告。当 OLAV 检测到输出适合格式化时会自动调用，也可以显式请求。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-V15-03 | 报告撰写器将原始工具输出润色为格式化 Markdown 报告 | ✅ v0.15.0 |

---

## 工作原理

Core Agent 完成工具调用（如 `execute_sql`、`execute_cli`）后，可将原始结果交给 writer 子 agent 润色：

```
Core Agent → execute_sql → 原始表格数据
                      ↓
            Writer 子 Agent → 格式化 Markdown 报告
```

writer 接收：
- 用户原始问题
- 工具原始输出
- 可选格式提示（如"以表格形式"、"以列表形式"）

并返回带有标题、表格和摘要句子的润色 Markdown 文档。

---

## 触发方式

### 自动触发

当查询产生表格数据时，OLAV 自动调用 writer：

```bash
olav "显示所有 BGP 邻居及其状态"
```

输出（writer 格式化后）：

```markdown
## BGP 邻居状态 — R1

| 邻居 IP    | AS    | 状态         | 运行时间 |
|------------|-------|--------------|---------|
| 10.0.0.2   | 65002 | Established  | 2天3小时 |
| 10.0.0.3   | 65003 | Idle         | —       |

**摘要：** 2 个邻居中有 1 个断开。R1 → 10.0.0.3 处于 Idle 状态。
```

### 显式请求

直接请求生成报告：

```bash
olav "为设备清单写一份报告"
olav "将上次查询结果格式化为 Markdown 表格"
```

---

## workspace 位置

writer 子 agent 定义于：

```
.olav/workspace/core/writer/
├── AGENT.md       ← writer agent 定义
└── prompts/
    └── system.md  ← writer 系统提示词
```

它作为子 agent 调用被 Core Agent 使用，不会出现在 `olav list` 中，因为它不是独立 workspace。

---

## 配置

只要 `core` workspace 激活，writer 始终可用，无需额外配置。

如需禁用某次查询的格式化，在问题末尾加 `--raw`：

```bash
olav "显示 BGP 邻居 --raw"
```

这将直接返回未格式化的工具原始输出。
