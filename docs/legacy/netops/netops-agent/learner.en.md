# Learner

The **learner** does parser-layer learning for CLI output. When the stock
ntc-templates can't parse a device's output, the learner produces a persistent
parser — a TextFSM template or a Python `def parse(raw)` — so every subsequent
pipeline run parses that command automatically.

Invoke it via `/learn_cmd`: it **learns once, freezes the parser**, and every
later collection picks it up without restart.

---

## When to use

- "The parser missed `show ip bgp community` — teach it"
- "No template exists for this vendor's `show version` output"
- "Batch-learn parsers for these commands that came back unparsed"

---

## Tools

| Tool | When to use |
|------|-------------|
| `learn_commands` | Batch mode — learn parsers for several unparsed commands at once |
| `cmd_learn` | Interactive single-command learning (`/learn_cmd`) |

---

## Example

```bash
olav --agent netops "/learn_cmd 'show ip bgp community' --device R1"
```

The orchestrator routes to the learner, which analyzes the raw output,
generates a TextFSM (or Python) parser, validates it, and freezes it. From then
on the collection pipeline parses that command with no further action.

```python
task("learner", "learn parsers for the commands that came back raw in the last snapshot")
```
