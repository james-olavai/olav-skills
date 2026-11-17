# Learner

**learner** 为 CLI 输出做解析器层的学习。当现成的 ntc-templates 无法解析某设备的
输出时，learner 产出一个持久化的解析器 —— 一份 TextFSM 模板或一段 Python
`def parse(raw)` —— 使后续每次 pipeline 运行都能自动解析该命令。

通过 `/learn_cmd` 调用它：它**学习一次、冻结解析器**，之后每次采集都会自动
拾取，无需重启。

---

## 何时使用

- "解析器漏了 `show ip bgp community` —— 教它"
- "这个厂商的 `show version` 输出没有模板"
- "为这些返回未解析的命令批量学习解析器"

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `learn_commands` | 批量模式 —— 一次为多条未解析命令学习解析器 |
| `cmd_learn` | 交互式单命令学习（`/learn_cmd`） |

---

## 示例

```bash
olav --agent netops "/learn_cmd 'show ip bgp community' --device R1"
```

编排器路由到 learner，它分析原始输出，生成 TextFSM（或 Python）解析器，校验
并冻结。此后采集 pipeline 无需任何额外操作即可解析该命令。

```python
task("learner", "为上一个快照中返回原始文本的命令学习解析器")
```
